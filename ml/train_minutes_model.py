"""Chronological coherent playing-time fitting and portable export; no final test or promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from fpl_forfeit.assistant.minutes_model import FEATURES, STATES, MinutesModel, playing_state
from fpl_forfeit.assistant.playing_time import estimate_minutes
from fpl_forfeit.forecasting.statistics import player_rates
from ml.evaluation.metrics import metrics
from ml.minutes_data import training_rows
from ml.prediction_benchmark import paired, probability_scores, write_json

OUTPUT = Path("ml/artifacts/minutes-sprint-v2")


def state_priors(rows, *, cold=False):
    global_counts = (
        np.bincount([r["state"] for r in rows if not cold or not r["history"]], minlength=5) + 0.5
    )
    overall = global_counts / global_counts.sum()
    result = {}
    for position in ("GK", "DEF", "MID", "FWD"):
        subset = [
            r
            for r in rows
            if r["player"].position.value == position and (not cold or not r["history"])
        ]
        counts = np.bincount([r["state"] for r in subset], minlength=5) + 20 * overall
        result[position] = (counts / counts.sum()).tolist()
    return result


def fit(
    rows,
    *,
    kind="softmax",
    C=0.1,
    temperature=1.0,
    prior_strength=2.0,
    half_life=6.0,
    duration_strength=4.0,
):
    durations = {}
    for position in ("GK", "DEF", "MID", "FWD"):
        durations[position] = {}
        for state in range(1, 5):
            values = [
                r["minutes"]
                for r in rows
                if r["state"] == state and r["player"].position.value == position
            ]
            if not values:
                values = [r["minutes"] for r in rows if r["state"] == state]
            if not values:
                raise ValueError("Conditional duration class absent from training")
            durations[position][str(state)] = sorted(Counter(values).items())
    data = {
        "format_version": 1,
        "version": "minutes-fitted-v2" if kind == "softmax" else "minutes-empirical-v2",
        "kind": kind,
        "features": list(FEATURES),
        "states": list(STATES),
        "priors": state_priors(rows),
        "cold_priors": state_priors(rows, cold=True),
        "durations": durations,
        "config": {
            "C": C,
            "temperature": temperature,
            "prior_strength": prior_strength,
            "half_life": half_life,
            "duration_strength": duration_strength,
        },
        "training_rows": len(rows),
        "latest_training_result_ready": max(r["ready"] for r in rows).isoformat(),
    }
    if kind == "softmax":
        x = np.asarray([[r["features"][f] for f in FEATURES] for r in rows])
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale[scale < 1e-12] = 1
        model = LogisticRegression(C=C, max_iter=700, solver="lbfgs").fit(
            (x - mean) / scale, [r["state"] for r in rows]
        )
        if list(model.classes_) != list(range(5)):
            raise ValueError("All five states required for fitted export")
        data["model"] = {
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "coef": model.coef_.tolist(),
            "intercept": model.intercept_.tolist(),
        }
    return data


def proposals(data, rows):
    """Vectorized fitting diagnostics; final reported forecasts use portable runtime inference."""
    if data["kind"] == "softmax":
        model = data["model"]
        x = np.asarray([[r["features"][f] for f in FEATURES] for r in rows])
        logits = (
            ((x - model["mean"]) / model["scale"]) @ np.asarray(model["coef"]).T
            + model["intercept"]
        ) / data["config"]["temperature"]
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        p = exp / exp.sum(axis=1, keepdims=True)
    else:
        model = MinutesModel(data=data)
        p = np.asarray([model.state_probabilities(r["player"], r["history"]) for r in rows])
    for i, row in enumerate(rows):
        if not row["history"]:
            p[i] = data["cold_priors"][row["player"].position.value]
    means = np.zeros((len(rows), 5))
    strength = data["config"]["duration_strength"]
    for i, row in enumerate(rows):
        weights, totals = [0.0] * 5, [0.0] * 5
        for age, item in enumerate(reversed(row["history"][-20:])):
            state = playing_state(item["minutes"], item["starts"])
            weight = 2 ** (-age / 4)
            weights[state] += weight
            totals[state] += weight * item["minutes"]
        for state in range(1, 5):
            hist = data["durations"][row["player"].position.value][str(state)]
            prior = sum(m * w for m, w in hist) / sum(w for _, w in hist)
            means[i, state] = (strength * prior + totals[state]) / (strength + weights[state])
    return p, (p * means).sum(axis=1)


def state_score(p, rows):
    actual = np.asarray([r["state"] for r in rows])
    losses = -np.log(np.maximum(1e-12, p[np.arange(len(rows)), actual]))
    candidates = np.asarray([r["groups"]["selection_candidates"] for r in rows])
    return (
        0.7 * losses[candidates].mean() + 0.3 * losses.mean() if candidates.any() else losses.mean()
    )


def select(rows):
    deadlines = sorted({r["deadline"] for r in rows})
    splits = [deadlines[len(deadlines) * k // 4] for k in (2, 3)]
    folds = [
        (
            [r for r in rows if r["deadline"] < split and r["ready"] < split],
            [
                r
                for r in rows
                if split
                <= r["deadline"]
                < (
                    splits[1]
                    if i == 0
                    else deadlines[-1] + __import__("datetime").timedelta(days=1)
                )
            ],
        )
        for i, split in enumerate(splits)
    ]
    scores = []
    for kind in ("empirical", "softmax"):
        configurations = (
            [
                {"kind": kind, "prior_strength": a, "half_life": h}
                for a in (0.5, 2.0, 8.0)
                for h in (2.0, 6.0)
            ]
            if kind == "empirical"
            else [
                {"kind": kind, "C": c, "temperature": t}
                for c in (0.01, 0.1, 1.0)
                for t in (0.85, 1.0, 1.15)
            ]
        )
        values = []
        for config in configurations:
            scored = []
            for train, valid in folds:
                artifact = fit(train, **config)
                probabilities, _ = proposals(artifact, valid)
                scored.append(state_score(probabilities, valid))
            values.append(float(np.mean(scored)))
        chosen = configurations[int(np.argmin(values))]
        duration_scores = []
        for strength in (1.0, 4.0, 16.0):
            losses = []
            for train, valid in folds:
                artifact = fit(train, **chosen, duration_strength=strength)
                _, minutes = proposals(artifact, valid)
                candidate = np.asarray([r["groups"]["selection_candidates"] for r in valid])
                error = (minutes - [r["minutes"] for r in valid]) ** 2
                losses.append(float(0.7 * error[candidate].mean() + 0.3 * error.mean()))
            duration_scores.append(float(np.mean(losses)))
        chosen = {**chosen, "duration_strength": (1.0, 4.0, 16.0)[int(np.argmin(duration_scores))]}
        scores.append(
            {
                "kind": kind,
                "chosen": chosen,
                "state_grid": configurations,
                "state_scores": values,
                "duration_grid": [1, 4, 16],
                "duration_mse": duration_scores,
                "inner_cutoffs": [s.isoformat() for s in splits],
            }
        )
    return scores


def baseline(row):
    rate = player_rates(row["player"])
    p_sub = (1 - rate.start) * (rate.cameo_probability or 0)
    start_minutes = sum(rate.starting_minutes) / len(rate.starting_minutes)
    sub_minutes = sum(rate.cameo_minutes) / len(rate.cameo_minutes)
    return {
        "appearance": rate.start + p_sub,
        "start": rate.start,
        "sixty": rate.start * np.mean(np.asarray(rate.starting_minutes) >= 60)
        + p_sub * np.mean(np.asarray(rate.cameo_minutes) >= 60),
        "minutes": rate.start * start_minutes + p_sub * sub_minutes,
        "start_duration": start_minutes,
        "sub_duration": sub_minutes,
    }


def as_values(estimate):
    return {
        "appearance": 1 - estimate.no_show_probability,
        "start": estimate.start_probability,
        "sixty": estimate.sixty_probability,
        "minutes": estimate.expected_minutes,
        "start_duration": sum(estimate.starting_minutes) / len(estimate.starting_minutes),
        "sub_duration": sum(estimate.substitute_minutes) / len(estimate.substitute_minutes),
    }


def run(output: Path, root: Path, *, export_runtime: bool = False):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "results.json").exists():
        raise ValueError("Use a fresh output directory; preserve prior results")
    rows = training_rows(root)
    records, selections = [], []
    for first, stop in ((1, 11), (11, 21), (21, 30), (30, 39)):
        test = [r for r in rows if r["season"] == "2024-25" and first <= r["gameweek"] < stop]
        cutoff = min(r["deadline"] for r in test)
        train = [r for r in rows if r["deadline"] < cutoff and r["ready"] < cutoff]
        selection = select(train)
        models = {}
        for item in selection:
            data = fit(train, **item["chosen"])
            write_json(output / f"{item['kind']}-GW{first:02}.json", data)
            models[item["kind"]] = MinutesModel(data=data)
        selections.append(
            {
                "first_gameweek": first,
                "cutoff": cutoff.isoformat(),
                "training_rows": len(train),
                "search": selection,
            }
        )
        for row in test:
            previous = baseline(row)
            try:
                if not row["history"] or row["groups"]["club_change_proxy_diagnostic"]:
                    raise ValueError("Legacy fallback")
                old = as_values(estimate_minutes(row["player"], row["history"]))
            except ValueError:
                old = previous
            predictions = {
                "statistical": previous,
                "minutes_v1": old,
                **{
                    kind: as_values(model.predict(row["player"], row["history"]))
                    for kind, model in models.items()
                },
            }
            records.append(
                {
                    "gameweek": row["gameweek"],
                    "player_id": row["player_id"],
                    "fixture_id": row["fixture_id"],
                    "deadline": row["deadline"],
                    "groups": row["groups"],
                    "actual": {
                        "appearance": int(row["minutes"] > 0),
                        "start": row["starts"],
                        "sixty": int(row["minutes"] >= 60),
                        "minutes": row["minutes"],
                    },
                    "predictions": predictions,
                }
            )
        print(f"Scored GW{first}-{stop - 1}: {len(test)} rows", flush=True)
    report = {
        "status": "retrospective_development_no_promotion",
        "population": len(records),
        "selections": selections,
        "groups": {},
        "uncertainty": {},
    }
    for group in records[0]["groups"]:
        selected = [r for r in records if r["groups"][group]]
        report["groups"][group] = {method: {} for method in records[0]["predictions"]}
        for method in report["groups"][group]:
            for field in ("minutes", "appearance", "start", "sixty"):
                actual = np.asarray([r["actual"][field] for r in selected])
                pred = np.asarray([r["predictions"][method][field] for r in selected])
                report["groups"][group][method][field] = (
                    metrics if field == "minutes" else probability_scores
                )(actual, pred)
            for field, condition in (
                ("start_duration", lambda r: r["actual"]["start"]),
                ("sub_duration", lambda r: r["actual"]["appearance"] and not r["actual"]["start"]),
            ):
                appeared = [r for r in selected if condition(r)]
                report["groups"][group][method][field] = (
                    metrics(
                        np.asarray([r["actual"]["minutes"] for r in appeared]),
                        np.asarray([r["predictions"][method][field] for r in appeared]),
                    )
                    if appeared
                    else {"n": 0}
                )
        for method in ("empirical", "softmax"):
            report["uncertainty"][f"{group}:{method}"] = paired(
                np.asarray([r["actual"]["minutes"] for r in selected]),
                np.asarray([r["predictions"]["statistical"]["minutes"] for r in selected]),
                np.asarray([r["predictions"][method]["minutes"] for r in selected]),
                np.asarray([r["deadline"] for r in selected], dtype=object),
            )
    final = select(rows)
    for item in final:
        data = fit(rows, **item["chosen"])
        data["provenance"] = {
            "protocol": "nested_past_deadline_development_v2",
            "seasons": ["2023-24", "2024-25"],
            "training_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runtime_code_sha256": hashlib.sha256(
                Path("src/fpl_forfeit/assistant/minutes_model.py").read_bytes()
            ).hexdigest(),
            "selection": item,
        }
        write_json(output / f"{item['kind']}-final.json", data)
        if export_runtime:
            write_json(
                Path("src/fpl_forfeit/assistant")
                / ("minutes_v2.json" if item["kind"] == "softmax" else "minutes_empirical_v2.json"),
                data,
            )
    report["protocol"] = {
        "state_selection": ".7 candidate + .3 all-population multinomial log loss; two expanding chronological inner folds",
        "duration_selection": ".7 candidate + .3 all-population minutes MSE on the same past inner folds",
        "candidate_rule": "five previous observed eligible fixtures: mean minutes>=60 and start rate>=.8",
        "availability": "neutral; historical news/registration vintages unproved",
        "group_ready": "entire prior deadline's final fixture kickoff+24h before training cutoff",
        "outer": "2024-25 four rolling blocks; previously consumed development; no 2025-26 outcomes",
    }
    write_json(output / "results.json", report)
    pq.write_table(
        pa.Table.from_pylist(records), output / "predictions.parquet", compression="zstd"
    )
    write_json(Path("ml/reports/MINUTES_MODEL_SPRINT.json"), report)
    print(
        json.dumps(
            {
                g: {m: v["minutes"]["rmse"] for m, v in report["groups"][g].items()}
                for g in ("all", "selection_candidates")
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--data-dir", type=Path, default=Path("ml/data/processed"))
    parser.add_argument(
        "--export-runtime",
        action="store_true",
        help="Explicitly replace the two packaged private challenger artifacts",
    )
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        run(args.output_dir, args.data_dir, export_runtime=args.export_runtime)


if __name__ == "__main__":
    main()
