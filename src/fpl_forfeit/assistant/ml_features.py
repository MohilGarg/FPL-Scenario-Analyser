"""Frozen v1 historical feature contract, shared by live Ridge inference.

Copied from ml.dataset at 6267d67; parity tests cover the full 216-column schema.
No HTTP, current snapshots, schedule conditioning or learned parameters live here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

Row = dict[str, Any]

INT_STATS = (
    "total_points",
    "minutes",
    "starts",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "saves",
    "bonus",
    "bps",
    "yellow_cards",
    "red_cards",
    "own_goals",
    "penalties_saved",
    "penalties_missed",
    "defensive_contribution",
    "clearances_blocks_interceptions",
    "recoveries",
    "tackles",
)
FLOAT_STATS = (
    "expected_goals",
    "expected_assists",
    "expected_goals_conceded",
    "expected_goal_involvements",
    "influence",
    "creativity",
    "threat",
    "ict_index",
)
STATS = INT_STATS + FLOAT_STATS
ROLLING_STATS = (
    "expected_goals",
    "expected_assists",
    "minutes",
    "starts",
    "goals_scored",
    "assists",
    "total_points",
)
WINDOWS = (3, 5, 10)
ROLLING_PREFIXES = tuple(f"pre_gw_last{n}_{unit}" for unit in ("matches", "gws") for n in WINDOWS)
ROLLING_COLUMNS = tuple(
    c
    for prefix in ROLLING_PREFIXES
    for c in (
        *(
            f"{prefix}_{stat}_{metric}"
            for stat in ROLLING_STATS
            for metric in ("sum", "mean", "missing_count")
        ),
        f"{prefix}_count",
        f"{prefix}_fixture_count",
        f"{prefix}_short_history",
        f"{prefix}_appearance_rate",
        f"{prefix}_start_rate",
    )
)
# Explicit whitelist: neither source columns nor current-GW outcomes are features.
FEATURE_COLUMNS = (
    "pre_gw_history_fixtures",
    "pre_gw_history_gameweeks",
    "pre_gw_points_mean_last3_observed_gws",
) + tuple(f"pre_gw_{stat}_sum" for stat in STATS)
FEATURE_COLUMNS += (
    (
        "pre_gw_no_history",
        "pre_gw_appearance_rate",
        "pre_gw_start_rate",
    )
    + tuple(f"pre_gw_{s}_missing_count" for s in STATS)
    + ROLLING_COLUMNS
)
# A conservative readiness proxy, NOT a recorded publication/finalisation time.
RESULT_LAG = timedelta(hours=24)


def strict_sum(values: list[int | float | None]) -> int | float | None:
    return sum(values) if values and all(v is not None for v in values) else None


def appearance_rate(records: list[Row], stat: str) -> float | None:
    if not records or any(r[stat] is None for r in records):
        return None
    return sum(r[stat] > 0 for r in records) / len(records)


def historical_features(prior: list[Row]) -> Row:
    """Rolling windows use only eligible records, never future schedule completeness.

    Match windows are chronological by actual kickoff, not original fixture ID. GW
    windows group eligible fixtures from the latest observed prior GWs; an unfinished
    second leg cannot enter either window. Missing/short coverage stays explicit.
    """
    prior = sorted(prior, key=lambda r: (r["kickoff_utc"], r["fixture_id"]))
    grouped: dict[int, list[Row]] = defaultdict(list)
    for record in prior:
        grouped[record["gameweek"]].append(record)
    gws = [
        {
            "gameweek": g,
            "fixture_count": len(group),
            **{s: strict_sum([r[s] for r in group]) for s in ROLLING_STATS},
        }
        for g, group in sorted(grouped.items())
    ]
    values: Row = {
        "pre_gw_history_fixtures": len(prior),
        "pre_gw_history_gameweeks": len(gws),
        "pre_gw_no_history": not prior,
        "pre_gw_appearance_rate": appearance_rate(prior, "minutes"),
        "pre_gw_start_rate": appearance_rate(prior, "starts"),
    }
    for stat in STATS:
        values[f"pre_gw_{stat}_sum"] = strict_sum([r[stat] for r in prior])
        values[f"pre_gw_{stat}_missing_count"] = sum(r[stat] is None for r in prior)
    for unit, history in (("matches", prior), ("gws", gws)):
        for n in WINDOWS:
            window = history[-n:]
            prefix = f"pre_gw_last{n}_{unit}"
            values[f"{prefix}_count"] = len(window)
            values[f"{prefix}_fixture_count"] = sum(r.get("fixture_count", 1) for r in window)
            values[f"{prefix}_short_history"] = len(window) < n
            values[f"{prefix}_appearance_rate"] = appearance_rate(window, "minutes")
            values[f"{prefix}_start_rate"] = appearance_rate(window, "starts")
            for stat in ROLLING_STATS:
                total = strict_sum([r[stat] for r in window])
                values[f"{prefix}_{stat}_sum"] = total
                values[f"{prefix}_{stat}_mean"] = total / len(window) if total is not None else None
                values[f"{prefix}_{stat}_missing_count"] = sum(r[stat] is None for r in window)
    values["pre_gw_points_mean_last3_observed_gws"] = values["pre_gw_last3_gws_total_points_mean"]
    return values
