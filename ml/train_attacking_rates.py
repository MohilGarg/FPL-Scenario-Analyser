"""Past-only conditional attacking-rate likelihood; final points require a joint ablation."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_poisson_deviance
from threadpoolctl import threadpool_limits

from fpl_forfeit.assistant.attacking_rates import FEATURES, features
from fpl_forfeit.forecasting.statistics import player_rates
from ml.minutes_data import eligible_history, historical_player, load_matches
from ml.prediction_benchmark import write_json


def load_rows():
    matches, quarantine, deadlines = load_matches(Path("ml/data/processed"), football=True)
    histories = defaultdict(list)
    ready = {}
    for row in matches:
        histories[row["player_key"]].append(row)
        key = row["season"], row["gameweek"]
        ready[key] = max(ready.get(key, row["kickoff_utc"]), row["kickoff_utc"])
    result = []
    cache = {}
    from datetime import timedelta

    for target in matches:
        if (
            not target["minutes"]
            or (target["season"], target["element"], target["gameweek"]) in quarantine
        ):
            continue
        key = target["player_key"], target["gameweek"]
        cutoff = deadlines[target["season"], target["gameweek"]]
        if key not in cache:
            prior = eligible_history(histories[target["player_key"]], target["gameweek"], cutoff)
            player = historical_player(target, prior, football=True)
            cache[key] = player
        player = cache[key]
        base = player_rates(player)
        result.append(
            {
                "season": target["season"],
                "gw": target["gameweek"],
                "deadline": cutoff,
                "ready": ready[target["season"], target["gameweek"]] + timedelta(hours=24),
                "x": features(player),
                "exposure": target["minutes"] / 90,
                "goal": target["goals_scored"],
                "assist": target["assists"],
                "base_goal": base.goal,
                "base_assist": base.assist,
            }
        )
    return result


def fitted(rows, target, alpha):
    return PoissonRegressor(alpha=alpha, max_iter=400).fit(
        [r["x"] for r in rows],
        np.asarray([r[target] for r in rows]) / np.asarray([r["exposure"] for r in rows]),
        sample_weight=[r["exposure"] for r in rows],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("ml/artifacts/attacking-sprint-v2"))
    args = parser.parse_args()
    if (args.output_dir / "results.json").exists():
        raise ValueError("Preserve prior results; use a fresh directory")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    report = {
        "scope": "Conditional on realised playing exposure for rate fitting only; full-population points evaluation separately retains nonappearances. Past xA and recorded FPL assists are distinct inputs/targets.",
        "outer": [],
        "metrics": {},
    }
    predictions = {name: [] for name in ("goal", "assist")}
    with threadpool_limits(limits=1):
        for first, stop in ((1, 11), (11, 21), (21, 30), (30, 39)):
            test = [r for r in rows if r["season"] == "2024-25" and first <= r["gw"] < stop]
            cutoff = min(r["deadline"] for r in test)
            train = [r for r in rows if r["deadline"] < cutoff and r["ready"] < cutoff]
            ds = sorted({r["deadline"] for r in train})
            split = ds[3 * len(ds) // 4]
            inner = [r for r in train if r["deadline"] < split and r["ready"] < split]
            valid = [r for r in train if r["deadline"] >= split]
            data = {
                "version": "attacking-rates-v2",
                "features": list(FEATURES),
                "models": {},
                "training_latest_ready": max(r["ready"] for r in train).isoformat(),
            }
            chosen = {}
            for target, predicted_rows in predictions.items():
                scores = []
                for alpha in (0.01, 0.1, 1.0):
                    model = fitted(inner, target, alpha)
                    pred = model.predict([r["x"] for r in valid]) * [r["exposure"] for r in valid]
                    scores.append(float(mean_poisson_deviance([r[target] for r in valid], pred)))
                alpha = (0.01, 0.1, 1.0)[int(np.argmin(scores))]
                chosen[target] = {"alpha": alpha, "inner_deviance": scores}
                model = fitted(train, target, alpha)
                data["models"][target] = {
                    "intercept": float(model.intercept_),
                    "coefficients": model.coef_.tolist(),
                    "alpha": alpha,
                }
                prediction = np.clip(model.predict([r["x"] for r in test]), 0.00001, 3.0)
                predicted_rows.extend(
                    {
                        "actual": r[target],
                        "baseline": r["base_" + target] * r["exposure"],
                        "model": float(v * r["exposure"]),
                    }
                    for r, v in zip(test, prediction)
                )
            write_json(args.output_dir / f"GW{first:02}.json", data)
            report["outer"].append({"first_gw": first, "chosen": chosen})
        for target, predicted_rows in predictions.items():
            model = fitted(rows, target, data["models"][target]["alpha"])
            data["models"][target].update(
                intercept=float(model.intercept_), coefficients=model.coef_.tolist()
            )
            report["metrics"][target] = {
                method: {
                    "poisson_deviance": float(
                        mean_poisson_deviance(
                            [r["actual"] for r in predicted_rows],
                            [r[method] for r in predicted_rows],
                        )
                    ),
                    "bias": float(np.mean([r[method] - r["actual"] for r in predicted_rows])),
                }
                for method in ("baseline", "model")
            }
        data["training_latest_ready"] = max(r["ready"] for r in rows).isoformat()
        write_json(Path("src/fpl_forfeit/assistant/attacking_v2.json"), data)
    write_json(args.output_dir / "results.json", report)
    write_json(Path("ml/reports/ATTACKING_RATES_SPRINT.json"), report)
    print(report["metrics"])


if __name__ == "__main__":
    main()
