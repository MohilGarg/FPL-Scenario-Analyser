"""Chronological team-scoring ablation, selected on past goal likelihood only."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_poisson_deviance
from threadpoolctl import threadpool_limits

from fpl_forfeit.forecasting.models import TeamMatchResult
from fpl_forfeit.forecasting.statistics import estimate_team_goal_rates, team_goal_rate
from ml.fixture_dataset import load
from ml.prediction_benchmark import write_json
from ml.team_goals import covariates, team_features


def rows_for(fixtures, deadlines, strength, half_life):
    from datetime import datetime, timedelta

    rows = []
    for season, schedule in fixtures.items():
        for gw in range(1, 39):
            cutoff = deadlines[season, gw]
            f = team_features(schedule, gw, cutoff, strength=strength, half_life=half_life)
            completed = [
                TeamMatchResult(r["team_h"], r["team_a"], r["team_h_score"], r["team_a_score"])
                for r in schedule
                if r["event"] < gw
                and datetime.fromisoformat(r["kickoff_time"]) + timedelta(hours=24) < cutoff
            ]
            baseline = estimate_team_goal_rates(completed)
            target = [r for r in schedule if r["event"] == gw]
            ready = max(datetime.fromisoformat(r["kickoff_time"]) for r in target) + timedelta(
                hours=24
            )
            for match in target:
                for own, opponent, home, y in (
                    (match["team_h"], match["team_a"], True, match["team_h_score"]),
                    (match["team_a"], match["team_h"], False, match["team_a_score"]),
                ):
                    rows.append(
                        {
                            "season": season,
                            "gw": gw,
                            "deadline": cutoff,
                            "ready": ready,
                            "x": covariates(f, own, opponent, home),
                            "y": y,
                            "baseline": team_goal_rate(baseline, own, opponent, home),
                        }
                    )
    return rows


def fitted(rows, alpha):
    return PoissonRegressor(alpha=alpha, max_iter=400).fit(
        np.asarray([r["x"] for r in rows]), [r["y"] for r in rows]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("ml/artifacts/team-goals-sprint-v2")
    )
    args = parser.parse_args()
    if (args.output_dir / "results.json").exists():
        raise ValueError("Preserve evidence; use a fresh output directory")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _, _, deadlines, fixtures = load()
    configs = [
        (strength, half, alpha) for strength in (4, 8) for half in (6, 12) for alpha in (0.1, 1.0)
    ]
    datasets = {(s, h): rows_for(fixtures, deadlines, s, h) for s, h, _ in configs}
    report = {
        "outer": [],
        "scope": "2024-25 development; goal calibration selected on prior chronological goal likelihood, no inspected outer points used in fitting",
        "predictions": [],
    }
    with threadpool_limits(limits=2):
        for first, stop in ((1, 11), (11, 21), (21, 30), (30, 39)):
            cutoff = deadlines["2024-25", first]
            scores = []
            for strength, half, alpha in configs:
                past = [
                    r
                    for r in datasets[strength, half]
                    if r["deadline"] < cutoff and r["ready"] < cutoff
                ]
                ds = sorted({r["deadline"] for r in past})
                split = ds[3 * len(ds) // 4]
                train = [r for r in past if r["deadline"] < split and r["ready"] < split]
                valid = [r for r in past if r["deadline"] >= split]
                model = fitted(train, alpha)
                pred = model.predict([r["x"] for r in valid])
                scores.append(float(mean_poisson_deviance([r["y"] for r in valid], pred)))
            strength, half, alpha = configs[int(np.argmin(scores))]
            rows = datasets[strength, half]
            train = [r for r in rows if r["deadline"] < cutoff and r["ready"] < cutoff]
            model = fitted(train, alpha)
            data = {
                "version": "team-goals-v2",
                "strength": strength,
                "half_life": half,
                "alpha": alpha,
                "intercept": float(model.intercept_),
                "coefficients": model.coef_.tolist(),
                "training_latest_result_ready": max(r["ready"] for r in train).isoformat(),
            }
            write_json(args.output_dir / f"GW{first:02}.json", data)
            test = [r for r in rows if r["season"] == "2024-25" and first <= r["gw"] < stop]
            prediction = np.clip(model.predict([r["x"] for r in test]), 0.15, 4.5)
            report["predictions"].extend(
                {"gw": r["gw"], "actual": r["y"], "baseline": r["baseline"], "model": float(v)}
                for r, v in zip(test, prediction)
            )
            report["outer"].append(
                {"first_gw": first, "selected": [strength, half, alpha], "inner_deviance": scores}
            )
        all_rows = datasets[strength, half]
        final = fitted(all_rows, alpha)
        data = {
            **data,
            "intercept": float(final.intercept_),
            "coefficients": final.coef_.tolist(),
            "training_latest_result_ready": max(r["ready"] for r in all_rows).isoformat(),
            "selection_note": "Runtime refit uses the last outer fold selected hyperparameters; no retrospective point-error promotion",
        }
        write_json(args.output_dir / "runtime-rejected.json", data)
    observed = [r["actual"] for r in report["predictions"]]
    report["metrics"] = {
        m: {
            "poisson_deviance": float(
                mean_poisson_deviance(observed, [r[m] for r in report["predictions"]])
            ),
            "rmse": float(
                np.mean([(r[m] - r["actual"]) ** 2 for r in report["predictions"]]) ** 0.5
            ),
            "bias": float(np.mean([r[m] - r["actual"] for r in report["predictions"]])),
        }
        for m in ("baseline", "model")
    }
    write_json(args.output_dir / "results.json", report)
    write_json(
        Path("ml/reports/TEAM_GOALS_SPRINT.json"),
        {k: v for k, v in report.items() if k != "predictions"},
    )
    print(report["metrics"])


if __name__ == "__main__":
    main()
