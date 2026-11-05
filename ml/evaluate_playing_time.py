"""Compare private minutes proposals with unchanged statistical proposals offline.

Historical registration and availability vintages are unavailable: these are
retrospective development diagnostics of fixture proposals, not calibrated joint
simulation predictions. Every history uses earlier-GW, 24-hour-buffered results.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from fpl_forfeit.assistant.playing_time import estimate_minutes
from fpl_forfeit.forecasting.statistics import player_rates
from fpl_forfeit.models import MatchAppearance, Player, Position
from ml.dataset import load_deadlines
from ml.evaluation.metrics import metrics
from ml.prediction_benchmark import paired, probability_scores, sha, write_json


def prior_history(rows: list[dict], gameweek: int, cutoff) -> list[dict]:
    return sorted(
        (
            r
            for r in rows
            if r["gameweek"] < gameweek
            and r["finished"]
            and r["kickoff_utc"] is not None
            and r["kickoff_utc"] + timedelta(hours=24) < cutoff
        ),
        key=lambda r: (r["kickoff_utc"], r["fixture_id"]),
    )


def evaluate(data_dir: Path) -> dict:
    season = "2024-25"
    columns = [
        "season",
        "player_key",
        "element",
        "name",
        "position",
        "gameweek",
        "fixture_id",
        "team_id",
        "minutes",
        "starts",
        "kickoff_utc",
        "finished",
    ]
    matches = pq.read_table(
        data_dir / "match_records.parquet", filters=[("season", "=", season)], columns=columns
    ).to_pylist()
    quarantined = {
        (r["player_key"], r["gameweek"])
        for r in pq.read_table(
            data_dir / "quality.parquet",
            filters=[("season", "=", season)],
            columns=["player_key", "gameweek", "evaluation_eligible"],
        ).to_pylist()
        if not r["evaluation_eligible"]
    }
    deadlines = load_deadlines(data_dir / "deadlines.csv")
    history, targets = defaultdict(list), defaultdict(list)
    for r in matches:
        history[r["player_key"]].append(r)
        if (r["player_key"], r["gameweek"]) not in quarantined:
            targets[r["player_key"], r["gameweek"]].append(r)
    actual = {n: [] for n in ("appearance", "start", "sixty", "minutes")}
    forecasts = {method: {n: [] for n in actual} for method in ("statistical", "minutes_states")}
    fallback, cold = 0, 0
    evaluated_deadlines = []
    groups = {n: [] for n in ("cold", "sparse_1_to3", "regular_starters", "all")}
    for (key, gw), group in targets.items():
        cutoff = deadlines[season, gw]
        prior = prior_history(history[key], gw, cutoff)
        if not prior:
            cold += 1
        current = group[0]
        player = Player(
            current["element"],
            current["name"],
            current["team_id"],
            str(current["team_id"]),
            Position(current["position"]),
            season_starts=sum(r["starts"] for r in prior),
            season_minutes=sum(r["minutes"] for r in prior),
            season_team_matches=len(prior),
            appearance_history=tuple(
                MatchAppearance(r["fixture_id"], r["gameweek"], r["minutes"], bool(r["starts"]))
                for r in prior
            ),
        )
        rate = player_rates(player)
        sub = (1 - rate.start) * rate.cameo_probability
        baseline = {
            "appearance": rate.start + sub,
            "start": rate.start,
            "sixty": rate.start * np.mean(np.asarray(rate.starting_minutes) >= 60)
            + sub * np.mean(np.asarray(rate.cameo_minutes) >= 60),
            "minutes": rate.start * np.mean(rate.starting_minutes)
            + sub * np.mean(rate.cameo_minutes),
        }
        try:
            if not prior or prior[-1]["team_id"] != player.team_id:
                raise ValueError("Missing or transferred history: runtime statistical fallback")
            estimate = estimate_minutes(player, prior)
            contender = {
                "appearance": 1 - estimate.no_show_probability,
                "start": estimate.start_probability,
                "sixty": estimate.sixty_probability,
                "minutes": estimate.expected_minutes,
            }
        except ValueError:
            # A rejected historical state remains in paired evaluation using the
            # identical explicitly counted statistical fallback.
            contender = baseline
            fallback += 1
        for target in group:
            if target["kickoff_utc"] <= cutoff:
                raise ValueError("Target fixture kickoff is not after prediction cutoff")
            evaluated_deadlines.append(cutoff)
            recent = prior[-5:]
            groups["all"].append(True)
            groups["cold"].append(not prior)
            groups["sparse_1_to3"].append(0 < len(prior) <= 3)
            groups["regular_starters"].append(
                len(recent) == 5
                and np.mean([r["starts"] for r in recent]) >= 0.8
                and np.mean([r["minutes"] for r in recent]) >= 60
            )
            for name, value in (
                ("appearance", target["minutes"] > 0),
                ("start", target["starts"] > 0),
                ("sixty", target["minutes"] >= 60),
                ("minutes", target["minutes"]),
            ):
                actual[name].append(value)
                forecasts["statistical"][name].append(baseline[name])
                forecasts["minutes_states"][name].append(contender[name])
    actual = {k: np.asarray(v) for k, v in actual.items()}
    scored = {
        method: {
            name: (metrics if name == "minutes" else probability_scores)(
                actual[name], np.asarray(prediction)
            )
            for name, prediction in values.items()
        }
        for method, values in forecasts.items()
    }
    grouped = {}
    for group, values in groups.items():
        mask = np.asarray(values, dtype=bool)
        grouped[group] = {
            "n": int(mask.sum()),
            "methods": {
                method: {
                    name: (metrics if name == "minutes" else probability_scores)(
                        actual[name][mask], np.asarray(p)[mask]
                    )
                    for name, p in predictions.items()
                }
                for method, predictions in forecasts.items()
            },
        }
    uncertainty = paired(
        actual["minutes"],
        np.asarray(forecasts["statistical"]["minutes"]),
        np.asarray(forecasts["minutes_states"]["minutes"]),
        np.asarray(evaluated_deadlines, dtype=object),
    )
    return {
        "season": season,
        "status": "retrospective_fixture_proposal_development_diagnostic",
        "fixture_rows": len(actual["minutes"]),
        "player_gameweeks": len(targets),
        "cold_start_player_gameweeks": cold,
        "fallback_player_gameweeks": fallback,
        "metrics": scored,
        "groups": grouped,
        "minutes_paired_uncertainty": uncertainty,
        "limitations": [
            "Current targets identify the source population; historic registration/position is unverified.",
            "No archived injuries or lineup reports: neutral availability assumed for both methods.",
            "These are pre-constraint fixture proposals, not realised shared-lineup simulation probabilities.",
            "Target DGW legs are scored separately; no sum of fixture probabilities becomes a GW probability.",
            "All cutoff histories exclude same-GW fixtures, and require result kickoff plus 24 hours strictly before deadline.",
            "No held-out final test or model promotion; 2024/25 was previously inspected.",
        ],
        "provenance": {
            "code_sha256": sha(Path(__file__)),
            "model_sha256": sha(Path("src/fpl_forfeit/assistant/playing_time.py")),
            "matches_sha256": sha(data_dir / "match_records.parquet"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("ml/data/processed"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ml/artifacts/prediction-benchmark-v2-verified/playing_time.json"),
    )
    args = parser.parse_args()
    result = evaluate(args.data_dir)
    write_json(args.output, result)
    print({"fixture_rows": result["fixture_rows"], "output": str(args.output)})


if __name__ == "__main__":
    main()
