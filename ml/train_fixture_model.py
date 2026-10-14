"""Fixed chronological fitting, validation and portable export; no holdout selection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from sklearn.linear_model import LogisticRegression, PoissonRegressor, Ridge
from sklearn.metrics import (
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    mean_poisson_deviance,
    mean_squared_error,
)
from threadpoolctl import threadpool_limits

from fpl_forfeit.assistant.component_features import FEATURE_COLUMNS, PLAYER_COLUMNS, TEAM_COLUMNS
from fpl_forfeit.assistant.component_model import ComponentModel
from ml.fixture_dataset import archived_schedules, build, load


def pre(rows, names):
    x = np.array([[r[c] if r[c] is not None else np.nan for c in names] for r in rows], dtype=float)
    med = np.array(
        [np.nanmedian(x[:, i]) if np.isfinite(x[:, i]).any() else 0 for i in range(len(names))]
    )
    filled = np.where(np.isnan(x), med, x)
    scale = filled.std(axis=0)
    scale[scale == 0] = 1
    return (filled - filled.mean(axis=0)) / scale, {
        "names": list(names),
        "median": med.tolist(),
        "mean": filled.mean(axis=0).tolist(),
        "scale": scale.tolist(),
    }


def fit(rows, teams, config):
    models = {}

    def fitted(name, subset, names, y, estimator, link, weights=None):
        x, meta = pre(subset, names)
        estimator.fit(x, y, sample_weight=weights)
        if link == "softmax" and list(estimator.classes_) != [0, 1, 2, 3]:
            raise ValueError("All four playing states are required in initial training")
        meta.update(
            link=link,
            coef=estimator.coef_.tolist(),
            intercept=estimator.intercept_.tolist()
            if isinstance(estimator.intercept_, np.ndarray)
            else float(estimator.intercept_),
        )
        if name.startswith("duration"):
            meta["residual_quantiles"] = np.quantile(
                y - estimator.predict(x), [0.1, 0.3, 0.5, 0.7, 0.9]
            ).tolist()
        models[name] = meta

    states = np.array(
        [
            0
            if not r["target_minutes"]
            else 1
            if not r["target_starts"]
            else 2
            if r["target_minutes"] < 60
            else 3
            for r in rows
        ]
    )
    fitted(
        "playing_state",
        rows,
        FEATURE_COLUMNS,
        states,
        LogisticRegression(C=config["logistic_C"], max_iter=500),
        "softmax",
    )
    for state in (1, 2, 3):
        subset = [r for r, s in zip(rows, states) if s == state]
        fitted(
            f"duration{state}",
            subset,
            FEATURE_COLUMNS,
            np.array([r["target_minutes"] for r in subset]),
            Ridge(alpha=config["ridge_alpha"]),
            "identity",
        )
    for name, target, names in (
        ("goals", "goals_scored", PLAYER_COLUMNS),
        ("assists", "assists", PLAYER_COLUMNS),
        ("yellow", "yellow_cards", FEATURE_COLUMNS),
        ("saves", "saves", FEATURE_COLUMNS),
    ):
        subset = [
            r
            for r in rows
            if r["target_minutes"] > 0
            and r["target_" + target] is not None
            and (name != "saves" or r["position_GK"])
        ]
        exposure = np.array([r["target_minutes"] / 90 for r in subset])
        fitted(
            name,
            subset,
            names,
            np.array([r["target_" + target] for r in subset]) / exposure,
            PoissonRegressor(alpha=config["poisson_alpha"], max_iter=300),
            "log",
            exposure,
        )
    fitted(
        "team_goals",
        teams,
        TEAM_COLUMNS,
        np.array([r["target_goals"] for r in teams]),
        PoissonRegressor(alpha=1, max_iter=300),
        "log",
    )
    return {
        "format_version": 1,
        "version": config["version"],
        "features": list(FEATURE_COLUMNS),
        "models": models,
        "config": config,
        "enabled": {name: True for name in models},
        "training_rows": len(rows),
        "training_team_rows": len(teams),
    }


def metrics(pred, actual):
    return {
        "n": len(actual),
        "mae": float(mean_absolute_error(actual, pred)),
        "rmse": float(mean_squared_error(actual, pred) ** 0.5),
        "bias": float(np.mean(np.array(pred) - actual)),
    }


def evaluate(model, rows, teams):
    proposal = [model.proposals(r) for r in rows]
    p = np.array([1 - r["state_probabilities"][0] for r in proposal])
    minutes = np.array([r["expected_minutes_proposal"] for r in proposal])
    actual = np.array([r["target_minutes"] for r in rows])
    base_p = np.array(
        [
            (r["appearance5"] * min(5, r["history_count"]) + 4 * 0.6)
            / (min(5, r["history_count"]) + 4)
            for r in rows
        ]
    )
    base_m = np.array([r["minutes5"] for r in rows])
    result = {
        "appearance": {
            "brier": float(brier_score_loss(actual > 0, p)),
            "log_loss": float(log_loss(actual > 0, p)),
            "baseline_brier": float(brier_score_loss(actual > 0, base_p)),
            "baseline_log_loss": float(log_loss(actual > 0, base_p)),
        },
        "minutes": metrics(minutes, actual),
        "baseline_minutes": metrics(base_m, actual),
        "components": {},
        "calibration": [],
    }
    for lower in np.arange(0, 1, 0.1):
        mask = (p >= lower) & (p < lower + 0.1)
        if mask.any():
            result["calibration"].append(
                {
                    "band": float(lower),
                    "n": int(mask.sum()),
                    "predicted": float(p[mask].mean()),
                    "observed": float((actual[mask] > 0).mean()),
                    "predicted_minutes": float(minutes[mask].mean()),
                    "observed_minutes": float(actual[mask].mean()),
                }
            )
    for name, target, feature in (
        ("goals", "goals_scored", "xg90"),
        ("assists", "assists", "xa90"),
        ("saves", "saves", "saves90"),
        ("yellow", "yellow_cards", "yellow90"),
    ):
        subset = [
            r
            for r in rows
            if r["target_minutes"] > 0
            and r["target_" + target] is not None
            and (name != "saves" or r["position_GK"])
        ]
        preds = np.array([model.predict(name, r) * r["target_minutes"] / 90 for r in subset])
        baseline = np.array(
            [
                (r[feature] if r[feature] is not None else (0.1 if name != "saves" else 3))
                * r["target_minutes"]
                / 90
                for r in subset
            ]
        )
        outcomes = np.array([r["target_" + target] for r in subset])
        result["components"][name] = {
            **metrics(preds, outcomes),
            "poisson_deviance": float(mean_poisson_deviance(outcomes, np.maximum(preds, 1e-8))),
            "baseline_deviance": float(mean_poisson_deviance(outcomes, np.maximum(baseline, 1e-8))),
            "diagnostic": "conditional on observed exposure, not a deployable prediction",
        }
    team_p = np.array([model.predict("team_goals", r) for r in teams])
    baseline = np.array(
        [
            max(0.25, min(4, (r["own_gf"] + r["opp_ga"]) / 2 * (1.1 if r["home"] else 0.9)))
            for r in teams
        ]
    )
    target = np.array([r["target_goals"] for r in teams])
    result["components"]["team_goals"] = {
        **metrics(team_p, target),
        "poisson_deviance": float(mean_poisson_deviance(target, team_p)),
        "baseline_deviance": float(mean_poisson_deviance(target, baseline)),
        "clean_sheet_brier": float(brier_score_loss(target == 0, np.exp(-team_p))),
        "baseline_clean_sheet_brier": float(brier_score_loss(target == 0, np.exp(-baseline))),
    }
    mask = np.array([r["starts5"] >= 0.8 and r["minutes5"] >= 60 for r in rows])
    result["regular_starters_minutes"] = metrics(minutes[mask], actual[mask])
    return result


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq

    config = json.loads(Path("ml/fixture_model_config.json").read_bytes())
    started = perf_counter()
    matches, quarantine, deadlines, fixtures = load()
    rows, teams, coverage = build(
        matches, quarantine, deadlines, fixtures, archived_schedules(), ("2023-24", "2024-25")
    )
    out = Path("ml/artifacts/fixture-components-v1")
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out / "fixture_dataset.parquet", compression="zstd")
    train = [r for r in rows if r["season"] == "2023-24"]
    valid = [r for r in rows if r["season"] == "2024-25"]
    with threadpool_limits(limits=2):
        dev = fit(train, [r for r in teams if r["season"] == "2023-24"], config)
        validation = evaluate(
            ComponentModel(data=dev), valid, [r for r in teams if r["season"] == "2024-25"]
        )
        final = fit(rows, teams, config)
    # Fixed validation-only gates: retain statistical component if its simple comparator wins.
    for name, score in validation["components"].items():
        final["enabled"][name] = score["poisson_deviance"] <= score["baseline_deviance"]
    final["enabled"]["playing_state"] = (
        validation["appearance"]["brier"] <= validation["appearance"]["baseline_brier"]
    )
    final["provenance"] = {
        "prepared_matches_sha256": hashlib.sha256(
            Path("ml/data/processed/match_records.parquet").read_bytes()
        ).hexdigest(),
        "source_hashes": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                Path("ml/fixture_model_config.json"),
                Path("ml/fixture4-source-review.json"),
                Path("ml/fixture_dataset.py"),
                Path("ml/train_fixture_model.py"),
                Path("src/fpl_forfeit/assistant/component_features.py"),
            )
        },
        "packages": {
            name: __import__("importlib.metadata", fromlist=["version"]).version(name)
            for name in ("numpy", "scikit-learn", "pyarrow", "scipy")
        },
        "training_seasons": config["fit_seasons"],
        "schedule_evidence": coverage,
        "validation": validation,
    }
    for path, body in (
        (out / "development.json", dev),
        (out / "validation.json", validation),
        (
            out / "metadata.json",
            {**final["provenance"], "runtime_seconds": perf_counter() - started},
        ),
        (Path("src/fpl_forfeit/assistant/component_v1.json"), final),
    ):
        path.write_text(
            json.dumps(body, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
        )
    print(
        json.dumps(
            {
                "coverage": coverage,
                "validation": validation,
                "enabled": final["enabled"],
                "seconds": perf_counter() - started,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
