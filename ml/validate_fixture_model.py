"""Bounded 2024/25 joint-points diagnostics, explicitly using retrospective roster/schedules.

Development model fitted on 2023/24 only; never evaluates the consumed 2025/26 season.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np

from fpl_forfeit.assistant.component_forecast import build_component_forecast
from fpl_forfeit.assistant.component_model import ComponentModel
from fpl_forfeit.assistant.ml_features import STATS
from fpl_forfeit.forecasting.horizon import forecast_horizon
from fpl_forfeit.forecasting.models import TeamMatchResult
from fpl_forfeit.forecasting.statistics import estimate_team_goal_rates
from fpl_forfeit.models import (
    Fixture,
    FPLCatalogue,
    GameweekInfo,
    MatchAppearance,
    Player,
    Position,
)
from fpl_forfeit.points import event_points
from ml.fixture_dataset import LAG, load
from ml.train_fixture_model import metrics


def historical_points(position, stats):
    # 2024/25 had no defensive-contribution points; goalkeeper goals already scored ten.
    return event_points(position, {**stats, "defensive_contribution": 0})


def main():
    import pyarrow.parquet as pq
    from sklearn.metrics import brier_score_loss

    started = perf_counter()
    root = Path("ml/artifacts/fixture-components-v1")
    dev = json.loads((root / "development.json").read_bytes())
    # Gates were measured on validation, so use ungated development components here.
    # Report this separately from the production refit; no further selection from point errors.
    model = ComponentModel(data=dev)
    matches, quarantine, deadlines, fixtures = load()
    season = "2024-25"
    matches = [r for r in matches if r["season"] == season]
    history = defaultdict(list)
    for r in matches:
        history[r["element"]].append(r)
    rows = [
        r
        for r in pq.read_table(
            root / "fixture_dataset.parquet",
            columns=["season", "gameweek", "element", "starts5", "minutes5"],
        ).to_pylist()
        if r["season"] == season
    ]
    eligible = {(r["gameweek"], r["element"]) for r in rows}
    regular = {
        (r["gameweek"], r["element"]) for r in rows if r["starts5"] >= 0.8 and r["minutes5"] >= 60
    }
    results = []
    for gw in sorted({r["gameweek"] for r in rows}):
        cutoff = deadlines[season, gw]
        players, histories = {}, {}
        targets = defaultdict(list)
        for r in matches:
            if r["gameweek"] == gw:
                targets[r["element"]].append(r)
        for pid, target in targets.items():
            current = target[0]
            prior = sorted(
                [
                    r
                    for r in history[pid]
                    if r["gameweek"] < gw and r["finished"] and r["kickoff_utc"] + LAG < cutoff
                ],
                key=lambda r: (r["kickoff_utc"], r["fixture_id"]),
            )

            def total(name, prior=prior):
                return (
                    sum(r[name] for r in prior)
                    if prior and all(r[name] is not None for r in prior)
                    else None
                )

            appearances = tuple(
                MatchAppearance(r["fixture_id"], r["gameweek"], r["minutes"], bool(r["starts"]))
                for r in prior
                if r["minutes"] is not None
            )
            players[pid] = Player(
                pid,
                current["name"],
                current["team_id"],
                str(current["team_id"]),
                Position(current["position"]),
                season_starts=total("starts"),
                season_minutes=total("minutes"),
                season_team_matches=len(prior),
                season_goals=total("goals_scored"),
                season_assists=total("assists"),
                season_saves=total("saves"),
                season_yellow_cards=total("yellow_cards"),
                expected_goals=total("expected_goals"),
                expected_assists=total("expected_assists"),
                appearance_history=appearances,
            )
            histories[pid] = {
                "retrieved_at": cutoff.isoformat(),
                "payload": {
                    "history": [
                        {
                            **{s: r.get(s) for s in STATS},
                            "element": pid,
                            "fixture": r["fixture_id"],
                            "round": r["gameweek"],
                            "was_home": r["was_home"],
                            "opponent_team": r["opponent_id"],
                            "kickoff_time": r["kickoff_utc"].isoformat(),
                        }
                        for r in prior
                    ]
                },
            }
        raw = fixtures[season]
        future = tuple(
            Fixture(
                f["id"],
                gw,
                f["team_h"],
                f["team_a"],
                str(f["team_h"]),
                str(f["team_a"]),
                False,
                False,
                __import__("datetime").datetime.fromisoformat(f["kickoff_time"]),
            )
            for f in raw
            if f["event"] == gw
        )
        completed = [
            TeamMatchResult(f["team_h"], f["team_a"], f["team_h_score"], f["team_a_score"])
            for f in raw
            if f["event"] < gw
            and __import__("datetime").datetime.fromisoformat(f["kickoff_time"]) + LAG < cutoff
        ]
        catalogue = FPLCatalogue(
            players,
            future,
            (GameweekInfo(gw, cutoff, False),),
            estimate_team_goal_rates(completed),
            cutoff,
            True,
            frozenset(),
            {},
            {},
            3,
            5,
            (),
        )
        with (
            patch("fpl_forfeit.forecasting.horizon.event_points", historical_points),
            patch("fpl_forfeit.forecasting.projections.event_points", historical_points),
        ):
            baseline = forecast_horizon(catalogue, (gw,), samples=32, seed=20261014)
            component, _ = build_component_forecast(
                baseline, {"fixtures": raw}, histories, cutoff, model=model
            )
        for pid, targets_for_player in targets.items():
            if (gw, pid) not in eligible or (season, pid, gw) in quarantine:
                continue
            observed = sum(r["total_points"] for r in targets_for_player)
            minutes = sum(r["minutes"] for r in targets_for_player)
            a, b = baseline.players[pid][gw], component.players[pid][gw]
            results.append(
                {
                    "gameweek": gw,
                    "player_id": pid,
                    "position": players[pid].position.value,
                    "regular_starter": (gw, pid) in regular,
                    "actual_points": observed,
                    "actual_minutes": minutes,
                    "statistical": a["expected_points"],
                    "components": b["expected_points"],
                    "statistical_appearance": a["appearance_probability"],
                    "components_appearance": b["appearance_probability"],
                    "statistical_minutes": a["expected_minutes"],
                    "components_minutes": b["expected_minutes"],
                }
            )
    report = {
        "population": "2024/25 only; retrospective roster/fixture proxy, identical buffered histories; 32 shared draws/GW; development components, not the refit/gated production model",
        "point_groups": {},
        "appearance": {},
        "minutes": {},
        "gw_errors": [],
    }
    groups = {
        "all": results,
        "regular_starters": [r for r in results if r["regular_starter"]],
        "actual_appearances_diagnostic": [r for r in results if r["actual_minutes"] > 0],
        "actual_nonappearances_diagnostic": [r for r in results if not r["actual_minutes"]],
    }
    groups.update(
        {p: [r for r in results if r["position"] == p] for p in ("GK", "DEF", "MID", "FWD")}
    )
    for group, subset in groups.items():
        report["point_groups"][group] = {
            name: metrics(
                np.array([r[name] for r in subset]), np.array([r["actual_points"] for r in subset])
            )
            for name in ("statistical", "components")
        }
    for name in ("statistical", "components"):
        report["appearance"][name] = float(
            brier_score_loss(
                [r["actual_minutes"] > 0 for r in results],
                [r[name + "_appearance"] for r in results],
            )
        )
        report["minutes"][name] = metrics(
            np.array([r[name + "_minutes"] for r in results]),
            np.array([r["actual_minutes"] for r in results]),
        )
    for gw in sorted({r["gameweek"] for r in results}):
        subset = [r for r in results if r["gameweek"] == gw]
        report["gw_errors"].append(
            {
                "gw": gw,
                **{
                    name: metrics(
                        np.array([r[name] for r in subset]),
                        np.array([r["actual_points"] for r in subset]),
                    )["mae"]
                    for name in ("statistical", "components")
                },
            }
        )
    delta = np.array([r["components"] - r["statistical"] for r in report["gw_errors"]])
    rng = np.random.default_rng(20261014)
    bootstrap = rng.choice(delta, (2000, len(delta)), replace=True).mean(axis=1)
    report["equal_gw_mae_difference_interval95"] = np.quantile(bootstrap, [0.025, 0.975]).tolist()
    baseline_path = Path("ml/artifacts/baselines-v1/validation_predictions.parquet")
    if baseline_path.exists():
        # Frozen Phase 2b validation predictions were fitted on 2023/24 only.
        import hashlib

        frozen = {
            (r["gameweek"], int(r["player_key"].split(":")[1])): r
            for r in pq.read_table(baseline_path).to_pylist()
            if r["season"] == season
        }
        paired = [
            (r, frozen[r["gameweek"], r["player_id"]])
            for r in results
            if (r["gameweek"], r["player_id"]) in frozen
        ]
        if any(r["actual_points"] != old["actual_points"] for r, old in paired):
            raise ValueError("Frozen baseline and fixture targets disagree; no silent comparison")
        report["frozen_ridge_comparison"] = {
            "source_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "metrics": {
                name: metrics(
                    np.array([old["ridge"] if name == "ridge" else r[name] for r, old in paired]),
                    np.array([r["actual_points"] for r, old in paired]),
                )
                for name in ("ridge", "statistical", "components")
            },
            "note": "Same validation outcomes/cutoffs; different approved predictor schemas. Not a fair replay of historical production availability.",
        }
    report["seconds"] = perf_counter() - started
    (root / "simulation_validation.json").write_text(json.dumps(report, indent=2) + "\n")
    pq.write_table(
        __import__("pyarrow").Table.from_pylist(results),
        root / "simulation_validation.parquet",
        compression="zstd",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
