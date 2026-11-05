"""Offline chronological development benchmark, including cold-start source rows.

Run: python -m ml.prediction_benchmark [run|reproduce] --output-dir PATH.
No model is promoted or exported into production by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pyarrow.parquet as pq
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from threadpoolctl import threadpool_limits

from ml.dataset import FEATURE_COLUMNS, load_deadlines, timestamp
from ml.deadlines import VERIFIED, reconstruct_deadlines
from ml.evaluation.metrics import metrics
from ml.training.data import load_population, table_keys
from ml.training.models import Preprocessor, predict_models

SEASONS = ("2023-24", "2024-25")
ALPHAS = (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0)
SMALL = tuple(
    f"pre_gw_last3_matches_{s}_mean"
    for s in (
        "total_points",
        "minutes",
        "starts",
        "expected_goals",
        "expected_assists",
        "goals_scored",
        "assists",
    )
)
MINUTES = tuple(
    f"pre_gw_last{n}_matches_{s}"
    for n in (3, 5, 10)
    for s in ("minutes_mean", "start_rate", "appearance_rate", "count")
)
DEFAULT_OUTPUT = Path("ml/artifacts/prediction-benchmark-v2-verified")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


@dataclass
class Dataset:
    x: np.ndarray
    y: np.ndarray
    season: np.ndarray
    gw: np.ndarray
    keys: np.ndarray
    deadline: np.ndarray
    ready: np.ndarray
    minutes: np.ndarray
    starts: np.ndarray
    reaches60: np.ndarray
    fixtures: np.ndarray
    position: np.ndarray
    audit: dict


def load(data_dir: Path) -> Dataset:
    """All complete quality-eligible prepared targets, including missing history.

    This is the full *source* population, not an invented historic deadline roster.
    Timestamped source publication/registration is unavailable and is disclosed.
    """
    filters = [("season", "in", list(SEASONS))]
    f = pq.read_table(
        data_dir / "features.parquet",
        filters=filters,
        columns=["season", "player_key", "gameweek", "feature_cutoff_utc", *FEATURE_COLUMNS],
    )
    o = pq.read_table(
        data_dir / "player_gameweeks.parquet",
        filters=filters,
        columns=[
            "player_key",
            "gameweek",
            "target_points",
            "target_complete",
            "observed_minutes",
            "observed_starts",
            "fixture_records",
            "position",
        ],
    )
    q = pq.read_table(
        data_dir / "quality.parquet",
        filters=filters,
        columns=["player_key", "gameweek", "evaluation_eligible"],
    )
    if table_keys(f) != table_keys(o) or table_keys(f) != table_keys(q):
        raise ValueError("Prepared feature/target/quality rows must align by identity")
    if len(set(table_keys(f))) != len(f):
        raise ValueError("Duplicate source player/deadline")
    meta = json.loads((data_dir / "preparation_metadata.json").read_bytes())
    if not meta["verified_archive"] or sha(data_dir / "deadlines.csv") != meta["deadline_sha256"]:
        raise ValueError("Deadline evidence or checksum changed")
    deadlines = load_deadlines(data_dir / "deadlines.csv")
    proofs = reconstruct_deadlines(data_dir.parent / "raw", list(SEASONS))
    verified = {
        (r["season"], r["gameweek"]): timestamp(r["deadline_utc"])
        for r in proofs
        if r["verification_status"] == VERIFIED
    }
    season = np.asarray(f["season"].to_pylist())
    gw = np.asarray(f["gameweek"].to_pylist(), dtype=int)
    cutoff = np.asarray(f["feature_cutoff_utc"].to_pylist(), dtype=object)
    proven = np.asarray(
        [
            verified.get((s, int(g))) == d and d is not None and deadlines[s, int(g)] == d
            for s, g, d in zip(season, gw, cutoff)
        ]
    )
    quality = np.asarray(q["evaluation_eligible"].to_pylist(), dtype=bool)
    complete = np.asarray(o["target_complete"].to_pylist(), dtype=bool)
    mask = proven & quality & complete
    x = np.column_stack([f[c].cast("double").to_numpy() for c in FEATURE_COLUMNS])
    y = o["target_points"].cast("double").to_numpy()
    minutes = o["observed_minutes"].cast("double").to_numpy()
    starts = o["observed_starts"].cast("double").to_numpy()
    if np.isinf(x).any() or not np.isfinite(y[mask]).all() or not np.isfinite(minutes[mask]).all():
        raise ValueError("Invalid prepared predictors/targets")
    readiness, max_minutes = {}, {}
    for r in pq.read_table(
        data_dir / "match_records.parquet",
        filters=filters,
        columns=["player_key", "gameweek", "kickoff_utc", "minutes", "finished"],
    ).to_pylist():
        key = r["player_key"], r["gameweek"]
        if not r["finished"] or r["kickoff_utc"] is None or r["minutes"] is None:
            raise ValueError("Complete target lacks result readiness")
        readiness[key] = max(
            readiness.get(key, r["kickoff_utc"]), r["kickoff_utc"] + timedelta(hours=24)
        )
        max_minutes[key] = max(max_minutes.get(key, 0), r["minutes"])
    keys = table_keys(f)
    audit = {
        s: {
            "source_rows": int(np.sum(season == s)),
            "eligible_source_rows": int(np.sum(mask & (season == s))),
            "zero_minute_rows": int(np.sum(mask & (season == s) & (minutes == 0))),
            "cold_start_rows": int(
                np.sum(
                    mask
                    & (season == s)
                    & (x[:, FEATURE_COLUMNS.index("pre_gw_history_fixtures")] == 0)
                )
            ),
            "quarantined_rows": int(np.sum(~quality & (season == s))),
        }
        for s in SEASONS
    }
    return Dataset(
        x[mask],
        y[mask],
        season[mask],
        gw[mask],
        np.asarray(f["player_key"].to_pylist())[mask],
        cutoff[mask],
        np.asarray([readiness[k] for k in keys], dtype=object)[mask],
        minutes[mask],
        starts[mask],
        np.asarray([max_minutes[k] >= 60 for k in keys])[mask],
        o["fixture_records"].to_numpy()[mask],
        np.asarray(o["position"].to_pylist())[mask],
        audit,
    )


def ready_before(data: Dataset, cutoff, candidates: np.ndarray | None = None) -> np.ndarray:
    """Keep whole-deadline groups; late labels never enter earlier fitting."""
    mask = (data.deadline < cutoff) & (data.ready < cutoff)
    if candidates is not None:
        mask &= candidates
    return mask


def indices(names: tuple[str, ...]) -> np.ndarray:
    return np.asarray([FEATURE_COLUMNS.index(n) for n in names], dtype=int)


def select_alpha(data: Dataset, available: np.ndarray) -> tuple[float, list[dict]]:
    """Expanding deadline-block validation; fit preprocessing inside every fold."""
    candidates = sorted(set(data.deadline[available]))
    if len(candidates) < 12:
        raise ValueError("At least twelve past deadlines required for chronological selection")
    boundaries = np.unique(
        np.asarray([len(candidates) // 2, 3 * len(candidates) // 4, len(candidates)])
    )
    sums = {a: [0.0, 0] for a in ALPHAS}
    folds = []
    for start, stop in itertools.pairwise(boundaries):
        first = candidates[int(start)]
        train = ready_before(data, first, available)
        valid = available & (data.deadline >= first) & (data.deadline <= candidates[int(stop) - 1])
        if not train.any() or not valid.any():
            continue
        pre = Preprocessor.fit(data.x[train], FEATURE_COLUMNS)
        xt, xv = pre.transform(data.x[train]), pre.transform(data.x[valid])
        scores = {}
        for alpha in ALPHAS:
            prediction = Ridge(alpha=alpha, solver="cholesky").fit(xt, data.y[train]).predict(xv)
            squared = float(np.sum((prediction - data.y[valid]) ** 2))
            sums[alpha][0] += squared
            sums[alpha][1] += int(valid.sum())
            scores[str(alpha)] = float(np.sqrt(squared / valid.sum()))
        folds.append(
            {
                "first_validation_deadline": first.isoformat(),
                "train_rows": int(train.sum()),
                "validation_rows": int(valid.sum()),
                "rmse": scores,
                "latest_training_result_ready": max(data.ready[train]).isoformat(),
            }
        )
    if not folds:
        raise ValueError("No chronological alpha folds")
    chosen = min(ALPHAS, key=lambda a: (sums[a][0] / sums[a][1], -a))
    for fold in folds:
        fold["pooled_rmse_by_alpha"] = {
            str(a): float(np.sqrt(sums[a][0] / sums[a][1])) for a in ALPHAS
        }
    return chosen, folds


def paired(
    actual: np.ndarray,
    reference: np.ndarray,
    contender: np.ndarray,
    deadlines: np.ndarray,
    draws: int = 2000,
) -> dict:
    """Paired four-deadline moving blocks, weighted by rows inside drawn blocks."""
    groups = sorted(set(deadlines))
    if len(groups) < 4:
        raise ValueError("At least four evaluation deadlines required")
    a, b = reference - actual, contender - actual
    counts = np.asarray([np.sum(deadlines == d) for d in groups])
    abs_a = np.asarray([np.abs(a[deadlines == d]).sum() for d in groups])
    abs_b = np.asarray([np.abs(b[deadlines == d]).sum() for d in groups])
    sq_a = np.asarray([np.square(a[deadlines == d]).sum() for d in groups])
    sq_b = np.asarray([np.square(b[deadlines == d]).sum() for d in groups])
    rng = np.random.default_rng(20261105)
    starts = rng.integers(0, len(groups) - 3, size=(draws, int(np.ceil(len(groups) / 4))))
    sampled = (starts[:, :, None] + np.arange(4)).reshape(draws, -1)[:, : len(groups)]
    n = counts[sampled].sum(axis=1)
    mae = (abs_b[sampled].sum(axis=1) - abs_a[sampled].sum(axis=1)) / n
    rmse = np.sqrt(sq_b[sampled].sum(axis=1) / n) - np.sqrt(sq_a[sampled].sum(axis=1) / n)
    return {
        "mae_delta": float(np.abs(b).mean() - np.abs(a).mean()),
        "mae_ci95": np.quantile(mae, [0.025, 0.975]).tolist(),
        "rmse_delta": float(np.sqrt(np.square(b).mean()) - np.sqrt(np.square(a).mean())),
        "rmse_ci95": np.quantile(rmse, [0.025, 0.975]).tolist(),
        "deadlines_improved_rmse": int(np.sum(sq_b / counts < sq_a / counts)),
        "deadlines": len(groups),
        "draws": draws,
        "block_deadlines": 4,
    }


def probability_scores(y: np.ndarray, p: np.ndarray) -> dict:
    if y.shape != p.shape or not len(y) or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Invalid aligned probability forecasts")
    bounded = np.clip(p, 1e-12, 1 - 1e-12)
    bins = []
    for lo, hi in zip(np.arange(0, 1, 0.1), np.arange(0.1, 1.01, 0.1)):
        m = (p >= lo) & ((p < hi) if hi < 1 - 1e-9 else (p <= 1))
        if m.any():
            bins.append(
                {
                    "range": [float(lo), float(hi)],
                    "n": int(m.sum()),
                    "predicted": float(p[m].mean()),
                    "observed": float(y[m].mean()),
                }
            )
    return {
        "n": len(y),
        "brier": float(np.square(p - y).mean()),
        "log_loss": float(-np.mean(y * np.log(bounded) + (1 - y) * np.log(1 - bounded))),
        "mean_predicted": float(p.mean()),
        "observed_rate": float(y.mean()),
        "calibration": bins,
    }


def run(data_dir: Path, output_dir: Path) -> dict:
    if (output_dir / "results.json").exists():
        raise ValueError("Use a fresh output directory to preserve experiment evidence")
    data = load(data_dir)
    prediction = {
        n: np.full(len(data.y), np.nan)
        for n in (
            "recent_average",
            "ols_small",
            "ridge_fixed100",
            "ridge_tuned",
            "ridge_no_minutes",
            "tree_tuned",
        )
    }
    probability = {n: np.full(len(data.y), np.nan) for n in ("appearance", "start", "sixty")}
    probability_baseline = {n: np.full(len(data.y), np.nan) for n in probability}
    targets = {"appearance": data.minutes > 0, "start": data.starts > 0, "sixty": data.reaches60}
    folds, equations, trees = [], [], []
    # Four expanding outer deadline blocks. The boundary is an operational refit
    # cadence; all inner selection and transformations use past available labels.
    gws = sorted(set(data.gw[data.season == "2024-25"]))
    chunks = np.array_split(gws, 4)
    small_indices = indices(SMALL)
    minute_names = {
        n
        for n in FEATURE_COLUMNS
        if "minutes" in n or "starts" in n or "start_rate" in n or "appearance_rate" in n
    }
    with threadpool_limits(limits=2):
        for chunk in chunks:
            test = (data.season == "2024-25") & np.isin(data.gw, chunk)
            first = min(data.deadline[test])
            train = ready_before(data, first)
            alpha, inner = select_alpha(data, train)
            pre = Preprocessor.fit(data.x[train], FEATURE_COLUMNS)
            xt, xv = pre.transform(data.x[train]), pre.transform(data.x[test])
            fallback = float(data.y[train].mean())
            recent = data.x[test, FEATURE_COLUMNS.index("pre_gw_points_mean_last3_observed_gws")]
            prediction["recent_average"][test] = np.where(np.isnan(recent), fallback, recent)
            for name, value in (("ridge_fixed100", 100), ("ridge_tuned", alpha)):
                model = Ridge(alpha=value, solver="cholesky").fit(xt, data.y[train])
                prediction[name][test] = model.predict(xv)
            retained_small = np.asarray(
                [i for i, raw in enumerate(pre.kept) if raw in small_indices]
            )
            ols = LinearRegression().fit(xt[:, retained_small], data.y[train])
            prediction["ols_small"][test] = ols.predict(xv[:, retained_small])
            equations.append(
                {
                    "first_deadline": first.isoformat(),
                    "training_rows": int(train.sum()),
                    "intercept_raw_imputed": float(
                        ols.intercept_
                        - np.sum(ols.coef_ * pre.mean[retained_small] / pre.scale[retained_small])
                    ),
                    "coefficients_per_raw_unit": {
                        FEATURE_COLUMNS[pre.kept[i]]: float(c / pre.scale[i])
                        for i, c in zip(retained_small, ols.coef_)
                    },
                    "imputation_medians": {
                        FEATURE_COLUMNS[pre.kept[i]]: float(pre.median[i]) for i in retained_small
                    },
                }
            )
            no_minutes = np.asarray(
                [i for i, raw in enumerate(pre.kept) if FEATURE_COLUMNS[raw] not in minute_names]
            )
            ablated = Ridge(alpha=alpha, solver="cholesky").fit(xt[:, no_minutes], data.y[train])
            prediction["ridge_no_minutes"][test] = ablated.predict(xv[:, no_minutes])
            # Tune a deliberately small tree grid on a trailing past-deadline block.
            past_deadlines = sorted(set(data.deadline[train]))
            split = past_deadlines[3 * len(past_deadlines) // 4]
            ttrain = ready_before(data, split, train)
            tvalid = train & (data.deadline >= split)
            tpre = Preprocessor.fit(data.x[ttrain], FEATURE_COLUMNS)
            tx, vx = tpre.transform(data.x[ttrain]), tpre.transform(data.x[tvalid])
            configs = [(7, 100), (15, 200), (31, 200)]
            scores = []
            for leaves, minimum in configs:
                model = lgb.LGBMRegressor(
                    n_estimators=120,
                    learning_rate=0.04,
                    num_leaves=leaves,
                    max_depth=5,
                    min_child_samples=minimum,
                    reg_lambda=10,
                    n_jobs=2,
                    verbosity=-1,
                    deterministic=True,
                    force_col_wise=True,
                    random_state=20261105,
                )
                model.fit(tx, data.y[ttrain])
                scores.append(float(np.mean(np.square(model.predict(vx) - data.y[tvalid]))))
            chosen = min(range(len(scores)), key=lambda i: (scores[i], configs[i][0]))
            leaves, minimum = configs[chosen]
            model = lgb.LGBMRegressor(
                n_estimators=120,
                learning_rate=0.04,
                num_leaves=leaves,
                max_depth=5,
                min_child_samples=minimum,
                reg_lambda=10,
                n_jobs=2,
                verbosity=-1,
                deterministic=True,
                force_col_wise=True,
                random_state=20261105,
            )
            model.fit(xt, data.y[train])
            prediction["tree_tuned"][test] = model.predict(xv)
            trees.append(
                {
                    "first_deadline": first.isoformat(),
                    "grid": configs,
                    "inner_rmse": np.sqrt(scores).tolist(),
                    "chosen": [leaves, minimum],
                }
            )
            minute_cols = np.asarray(
                [i for i, raw in enumerate(pre.kept) if FEATURE_COLUMNS[raw] in MINUTES]
            )
            for name, y in targets.items():
                classifier = LogisticRegression(C=0.1, max_iter=1000, solver="lbfgs").fit(
                    xt[:, minute_cols], y[train]
                )
                probability[name][test] = classifier.predict_proba(xv[:, minute_cols])[:, 1]
                baseline_name = {
                    "appearance": "pre_gw_last5_matches_appearance_rate",
                    "start": "pre_gw_last5_matches_start_rate",
                }.get(name)
                baseline = (
                    data.x[test, FEATURE_COLUMNS.index(baseline_name)]
                    if baseline_name
                    else np.full(int(test.sum()), np.nan)
                )
                # No historic threshold-rate feature exists; 60-minute baseline is
                # training prevalence, not thresholding an average-minute forecast.
                count = data.x[test, FEATURE_COLUMNS.index("pre_gw_last5_matches_count")]
                prevalence = float(y[train].mean())
                shrunk = (baseline * count + 2 * prevalence) / (count + 2)
                probability_baseline[name][test] = np.where(np.isnan(shrunk), prevalence, shrunk)
            folds.append(
                {
                    "first_deadline": first.isoformat(),
                    "gameweeks": chunk.tolist(),
                    "training_rows": int(train.sum()),
                    "evaluation_rows": int(test.sum()),
                    "latest_training_ready": max(data.ready[train]).isoformat(),
                    "selected_alpha": alpha,
                    "inner_folds": inner,
                }
            )
    evaluated = data.season == "2024-25"
    for values in (*prediction.values(), *probability.values(), *probability_baseline.values()):
        if not np.isfinite(values[evaluated]).all():
            raise ValueError("Missing predictions must not silently disappear from evaluation")
    recent_minutes = data.x[:, FEATURE_COLUMNS.index("pre_gw_last5_matches_minutes_mean")]
    recent_starts = data.x[:, FEATURE_COLUMNS.index("pre_gw_last5_matches_start_rate")]
    groups = {
        "all_source_population": evaluated,
        "selection_candidates_predeadline": evaluated
        & (recent_minutes >= 60)
        & (recent_starts >= 0.8),
        "cold_start": evaluated
        & (data.x[:, FEATURE_COLUMNS.index("pre_gw_history_fixtures")] == 0),
        "sparse_history_1_to_3": evaluated & (data.x[:, 0] > 0) & (data.x[:, 0] <= 3),
        "recent_minutes_under30": evaluated & (recent_minutes < 30),
        "recent_minutes_30_to60": evaluated & (recent_minutes >= 30) & (recent_minutes < 60),
        "recent_minutes_60plus": evaluated & (recent_minutes >= 60),
        "double_gameweek": evaluated & (data.fixtures > 1),
        "observed_zero_minutes_diagnostic": evaluated & (data.minutes == 0),
    }
    groups.update({p: evaluated & (data.position == p) for p in ("GK", "DEF", "MID", "FWD")})
    results = {
        "version": "prediction-benchmark-v2",
        "status": "retrospective_development_only",
        "population_audit": data.audit,
        "evaluation_rows": int(evaluated.sum()),
        "evaluation_deadlines": len(set(data.deadline[evaluated])),
        "coverage_all_models": 1.0,
        "metrics": {
            name: {
                g: metrics(data.y[m], values[m]) if m.any() else {"n": 0} for g, m in groups.items()
            }
            for name, values in prediction.items()
        },
        "paired_vs_fixed100": {
            name: paired(
                data.y[evaluated],
                prediction["ridge_fixed100"][evaluated],
                values[evaluated],
                data.deadline[evaluated],
            )
            for name, values in prediction.items()
            if name != "ridge_fixed100"
        },
        "probability_metrics": {
            name: {
                "logistic": probability_scores(y[evaluated], probability[name][evaluated]),
                "baseline": probability_scores(y[evaluated], probability_baseline[name][evaluated]),
            }
            for name, y in targets.items()
        },
        "folds": folds,
        "tree_selection": trees,
        "ols_equations": equations,
        "limitations": [
            "Prepared source rows are not a verified deadline-eligible historical roster; missing roster/blank rows remain unknown.",
            "Corrected retrospective results have a 24-hour availability proxy, not timestamped publication vintages.",
            "2024/25 and 2025/26 were previously inspected; no untouched holdout is claimed; this run never reads 2025/26 outcomes.",
            "No price/injury/registration timestamp history; price/injury subgroups unavailable.",
            "Recent minutes bands are predeadline historical means, not predicted-minutes bands.",
            "No honest historical six-GW forecasts or external archives available; future evidence required.",
            "Minutes classifiers are separate diagnostic marginals; not a joint runtime simulation model.",
        ],
        "provenance": {
            "files": {
                n: sha(data_dir / n)
                for n in (
                    "features.parquet",
                    "player_gameweeks.parquet",
                    "quality.parquet",
                    "deadlines.csv",
                )
            },
            "code_sha256": sha(Path(__file__)),
            "alpha_grid": list(ALPHAS),
            "selection_metric": "chronological inner pooled RMSE",
            "threads": 2,
            "probability_baseline": "last-five rate shrunk by two training-prevalence pseudo-observations; sixty uses train prevalence",
            "versions": {
                n: importlib.metadata.version(n)
                for n in ("numpy", "scipy", "scikit-learn", "lightgbm", "pyarrow")
            },
        },
    }
    write_json(output_dir / "results.json", results)
    np.savez_compressed(
        output_dir / "predictions.npz",
        actual=data.y[evaluated],
        gameweek=data.gw[evaluated],
        player_key=data.keys[evaluated],
        **{k: v[evaluated] for k, v in prediction.items()},
    )
    return results


def reproduce(data_dir: Path, directory: Path) -> dict:
    """Reload frozen original models and compare every saved prediction/target.

    Original source fingerprints are reported, not falsely claimed identical after
    a documented path-portability correction in the historical loader.
    """
    selection = json.loads((directory / "selection.json").read_bytes())
    config = selection["config"]
    recorded = selection["fingerprints"]
    for name, digest in recorded["inputs_sha256"].items():
        if sha(data_dir / name) != digest:
            raise ValueError(f"Original input bytes changed: {name}")
    checked = {}
    for phase, season, location in (
        ("validation", "2024-25", "validation_models"),
        ("test", "2025-26", "final_models"),
    ):
        pop = load_population(data_dir, [season], config["expected_candidates"])
        saved = pq.read_table(directory / f"{phase}_predictions.parquet")
        if (
            saved["player_key"].to_pylist() != pop.player_key.tolist()
            or saved["gameweek"].to_pylist() != pop.gameweek.tolist()
        ):
            raise ValueError("Original row alignment changed")
        np.testing.assert_array_equal(saved["actual_points"].to_numpy(), pop.y)
        bundle = joblib.load(directory / location / "ridge.joblib")
        models = {
            "recent_average": None,
            "ridge": bundle["model"],
            "lightgbm": lgb.Booster(model_file=str(directory / location / "lightgbm.txt")),
        }
        predictions, _ = predict_models(bundle["preprocessor"], models, pop.x, config)
        differences = {}
        for name, values in predictions.items():
            error = float(np.max(np.abs(saved[name].to_numpy() - values)))
            np.testing.assert_allclose(saved[name].to_numpy(), values, rtol=0, atol=1e-12)
            differences[name] = error
        checked[phase] = {
            "rows": len(pop.y),
            "zero_minute_rows": int(np.sum(pop.minutes == 0)),
            "max_prediction_difference": differences,
            "metrics": {n: metrics(pop.y, p) for n, p in predictions.items()},
        }
    return {
        "reproduced": checked,
        "training_performed": False,
        "all_prepared_input_hashes_match": True,
        "source_change_disclosure": "loader now resolves checksum-pinned deadlines.csv relative to the current checkout",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "reproduce"))
    parser.add_argument("--data-dir", type=Path, default=Path("ml/data/processed"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.data_dir, args.output_dir)
        print(json.dumps({"rows": result["evaluation_rows"], "output": str(args.output_dir)}))
    else:
        result = reproduce(args.data_dir, Path("ml/artifacts/baselines-v1"))
        write_json(args.output_dir / "original_reproduction.json", result)
        print(
            json.dumps({"reproduced_rows": {k: v["rows"] for k, v in result["reproduced"].items()}})
        )


if __name__ == "__main__":
    main()
