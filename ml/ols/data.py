"""Small, explicit pre-deadline feature builder using the existing prepared data.

Targets identify the next GW to predict, not the next row in a player's history.
No target outcomes, final club, opponent or venue are allowed into the feature builder.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from ml.dataset import RESULT_LAG, load_deadlines, timestamp
from ml.deadlines import VERIFIED, reconstruct_deadlines
from ml.sources import digest, source_lock, verify_cache

ROOT = Path(__file__).resolve().parent
PROTOCOL = json.loads((ROOT / "protocol.json").read_text(encoding="utf-8"))
STATS = (
    "goals_scored",
    "assists",
    "expected_goals",
    "expected_assists",
    "minutes",
    "starts",
    "total_points",
)
FEATURES = tuple(f"recent_{stat}" for stat in STATS) + (
    "is_defender",
    "is_midfielder",
    "is_forward",
    "days_since_last_match",
)
DEFINITIONS = (
    ("Goals per observed GW", "Recent scoring record; may differ from underlying xG."),
    ("FPL assists per observed GW", "Recent credited assists; not identical to xA."),
    ("Official FPL xG per observed GW", "Recent quality/quantity of scoring chances."),
    ("Official FPL xA per observed GW", "Recent quality/quantity of chances created."),
    ("Minutes per observed GW", "Recent opportunity to earn points, including non-appearances."),
    ("Starts per observed GW", "Recent starting role, distinct from substitute minutes."),
    ("FPL points per observed GW", "Simple summary of recent scoring; also the baseline."),
    ("1 if last eligible record says DEF, else 0", "Allows a defensive-position offset."),
    ("1 if last eligible record says MID, else 0", "Allows a midfield-position offset."),
    (
        "1 if last eligible record says FWD, else 0",
        "Allows a forward-position offset; GK is reference.",
    ),
    ("Days from last eligible kickoff to deadline", "Describes how stale the observed history is."),
)
Row = dict[str, Any]


def features_before(
    history: list[Row], season: str, gameweek: int, deadline: datetime
) -> Row | None:
    """Use only this player's same-season, earlier-GW buffered completed results.

    Sum DGW legs first, then average the latest three observed eligible GWs.
    Missing GWs are unknown, not invented zeros; 1/2-GW windows remain unpadded.
    """
    if len({r["player_key"] for r in history}) > 1:
        raise ValueError("Feature history must belong to one player")
    prior = [
        r
        for r in history
        if r["season"] == season
        and r["gameweek"] < gameweek
        and r["finished"]
        and r["kickoff_utc"] is not None
        and r["kickoff_utc"] + RESULT_LAG < deadline
    ]
    if not prior:
        return None
    latest = max(prior, key=lambda r: (r["kickoff_utc"], r["fixture_id"]))
    if latest["position"] not in {"GK", "DEF", "MID", "FWD"}:
        return None
    window_gws = sorted({r["gameweek"] for r in prior})[-PROTOCOL["rolling_observed_gameweeks"] :]
    window = [r for r in prior if r["gameweek"] in window_gws]
    if any(r[s] is None or not math.isfinite(r[s]) for r in window for s in STATS):
        return None
    values = {f"recent_{s}": sum(r[s] for r in window) / len(window_gws) for s in STATS}
    values.update(
        {
            "is_defender": float(latest["position"] == "DEF"),
            "is_midfielder": float(latest["position"] == "MID"),
            "is_forward": float(latest["position"] == "FWD"),
            "days_since_last_match": (deadline - latest["kickoff_utc"]).total_seconds() / 86400,
        }
    )
    return {
        "values": values,
        "history_gws": len(window_gws),
        "history_latest_ready_utc": max(r["kickoff_utc"] + RESULT_LAG for r in prior),
    }


def keyed(rows: list[Row]) -> dict[tuple[str, int], Row]:
    result = {(r["player_key"], r["gameweek"]): r for r in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate player-Gameweek key")
    return result


def align_quality(outcomes: list[Row], quality: list[Row]) -> dict[tuple[str, int], Row]:
    """Join by identity, never by an assumed matching row order."""
    targets, flags = keyed(outcomes), keyed(quality)
    if targets.keys() != flags.keys():
        raise ValueError("Outcome/quality keys differ")
    return flags


def build_rows(
    matches: list[Row],
    outcomes: list[Row],
    quality: list[Row],
    deadlines: dict[tuple[str, int], datetime],
) -> tuple[list[Row], list[Row]]:
    flags = align_quality(outcomes, quality)
    players: dict[str, list[Row]] = defaultdict(list)
    targets: dict[tuple[str, int], list[Row]] = defaultdict(list)
    match_keys = {(m["player_key"], m["fixture_id"]) for m in matches}
    if len(match_keys) != len(matches):
        raise ValueError("Duplicate player-fixture key")
    for match in matches:
        players[match["player_key"]].append(match)
        targets[match["player_key"], match["gameweek"]].append(match)
    if targets.keys() != keyed(outcomes).keys():
        raise ValueError("Match/outcome keys differ")
    rows, coverage = [], []
    for target in sorted(outcomes, key=lambda r: (r["season"], r["gameweek"], r["player_key"])):
        season, gw, player = target["season"], target["gameweek"], target["player_key"]
        deadline = deadlines.get((season, gw))
        group = targets[player, gw]
        # These are target checks, never predictors or appearance-based selection.
        if deadline and any(
            m["kickoff_utc"] is not None and m["kickoff_utc"] <= deadline for m in group
        ):
            raise ValueError("Target fixture starts before its prediction deadline")
        known_target = (
            target["target_complete"]
            and target["target_points"] is not None
            and target["fixture_coverage"] == "complete_final_schedule"
        )
        if known_target and (
            any(not m["finished"] or m["total_points"] is None for m in group)
            or sum(m["total_points"] for m in group) != target["target_points"]
        ):
            raise ValueError("Target points disagree with match records")
        features = features_before(players[player], season, gw, deadline) if deadline else None
        reason = (
            "missing_deadline"
            if deadline is None
            else "incomplete_target"
            if not known_target
            else "quality_quarantine"
            if not flags[player, gw]["evaluation_eligible"]
            else "no_usable_prior_history"
            if features is None
            else "included"
        )
        coverage.append({"season": season, "gameweek": gw, "reason": reason})
        if reason != "included":
            continue
        if any(m["kickoff_utc"] is None for m in group):
            raise ValueError("Target result readiness needs known kickoffs")
        rows.append(
            {
                "season": season,
                "gameweek": gw,
                "player_key": player,
                "name": target["name"],
                "deadline_utc": deadline,
                "actual": float(target["target_points"]),
                "observed_minutes": target["observed_minutes"],
                "target_ready_utc": max(m["kickoff_utc"] + RESULT_LAG for m in group),
                **features,
            }
        )
    return rows, coverage


def load_rows(data_dir: Path, seasons: list[str]) -> tuple[list[Row], list[Row], Row]:
    """Offline only. Reuse preparation without writing to the existing pipeline."""
    if RESULT_LAG.total_seconds() != PROTOCOL["result_buffer_hours"] * 3600:
        raise ValueError("Shared result buffer differs from the frozen protocol")
    raw_dir = data_dir.parent / "raw"
    manifest = verify_cache(raw_dir, seasons)
    metadata = json.loads((data_dir / "preparation_metadata.json").read_text(encoding="utf-8"))
    deadline_file = data_dir / "deadlines.csv"
    if (
        not metadata["verified_archive"]
        or digest(deadline_file.read_bytes()) != metadata["deadline_sha256"]
    ):
        raise ValueError("Prepared calendar needs verified archive provenance")
    proof = reconstruct_deadlines(raw_dir, seasons)
    deadlines = {
        (r["season"], r["gameweek"]): timestamp(r["deadline_utc"])
        for r in proof
        if r["verification_status"] == VERIFIED
    }
    supplied = {k: v for k, v in load_deadlines(deadline_file).items() if k[0] in seasons}
    if deadlines != supplied or any(v is None for v in deadlines.values()):
        raise ValueError("Prepared calendar disagrees with pinned official archive")
    filters = [("season", "in", seasons)]
    filenames = ("match_records.parquet", "player_gameweeks.parquet", "quality.parquet")
    columns = (
        [
            "season",
            "player_key",
            "gameweek",
            "fixture_id",
            "position",
            "kickoff_utc",
            "finished",
            *STATS,
        ],
        [
            "season",
            "player_key",
            "gameweek",
            "name",
            "target_complete",
            "target_points",
            "fixture_coverage",
            "observed_minutes",
        ],
        ["player_key", "gameweek", "evaluation_eligible"],
    )
    matches, outcomes, quality = [
        pq.read_table(data_dir / f, columns=c, filters=filters).to_pylist()
        for f, c in zip(filenames, columns)
    ]
    rows, coverage = build_rows(matches, outcomes, quality, deadlines)
    if {r["season"] for r in rows} != set(seasons):
        raise ValueError("A requested season has no usable observations")
    provenance = {
        "source": source_lock(),
        "deadline_sha256": metadata["deadline_sha256"],
        "prepared_sha256": {f: digest((data_dir / f).read_bytes()) for f in filenames},
        "raw_sha256": {
            k: v["sha256"] for k, v in manifest["files"].items() if k.split("/")[0] in seasons
        },
    }
    return rows, coverage, provenance


def matrix(rows: list[Row]) -> np.ndarray:
    """Explicit ordered whitelist prevents outcome/metadata columns entering X."""
    if not rows:
        raise ValueError("No observations")
    if any(set(r["values"]) != set(FEATURES) for r in rows):
        raise ValueError("Missing or unexpected regressors")
    x = np.asarray([[r["values"][f] for f in FEATURES] for r in rows], dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("Regressors must be finite; missing values are not zero")
    return x


def split_rows(rows: list[Row], phase: str) -> tuple[list[Row], list[Row], int]:
    train_season = PROTOCOL["training_season"]
    if phase == "develop":
        boundary = PROTOCOL["development_train_last_gw"]
        train = [r for r in rows if r["season"] == train_season and r["gameweek"] <= boundary]
        evaluation = [r for r in rows if r["season"] == train_season and r["gameweek"] > boundary]
    elif phase == "evaluate":
        train = [r for r in rows if r["season"] == train_season]
        evaluation = [r for r in rows if r["season"] in PROTOCOL["evaluation_seasons"]]
    else:
        raise ValueError("Unknown phase")
    if not train or not evaluation:
        raise ValueError("Empty chronological split")
    first_deadline = min(r["deadline_utc"] for r in evaluation)
    ready_train = [
        r
        for r in train
        if r["target_ready_utc"] < first_deadline and r["deadline_utc"] < first_deadline
    ]
    if not ready_train:
        raise ValueError("No training labels available before evaluation")
    return ready_train, evaluation, len(train) - len(ready_train)
