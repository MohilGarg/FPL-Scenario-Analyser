"""Fixture targets and explicit historical schedule evidence/proxies; no source xP."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fpl_forfeit.assistant.component_features import (
    FEATURE_COLUMNS,
    LAG,
    fixture_features,
    player_features,
    team_features,
)


def load(
    seasons: tuple[str, ...] = ("2023-24", "2024-25"),
) -> tuple[list[dict], set[tuple], dict, dict]:
    import pyarrow.parquet as pq

    from ml.dataset import load_deadlines, read_csv

    root = Path("ml/data/processed")
    matches = pq.read_table(
        root / "match_records.parquet", filters=[("season", "in", seasons)]
    ).to_pylist()
    quality = pq.read_table(
        root / "quality.parquet", filters=[("season", "in", seasons)]
    ).to_pylist()
    quarantine = {
        (r["season"], r["element"], r["gameweek"]) for r in quality if not r["evaluation_eligible"]
    }
    deadlines = load_deadlines(root / "deadlines.csv")
    fixtures = {}
    for season in seasons:
        data = read_csv(Path("ml/data/raw") / season / "fixtures.csv").rows
        fixtures[season] = [
            {
                "id": int(f["id"]),
                "event": int(f["event"]),
                "team_h": int(f["team_h"]),
                "team_a": int(f["team_a"]),
                "kickoff_time": f["kickoff_time"],
                "finished": f["finished"].lower() == "true",
                "team_h_score": int(f["team_h_score"]),
                "team_a_score": int(f["team_a_score"]),
            }
            for f in data
        ]
    return matches, quarantine, deadlines, fixtures


def archived_schedules() -> list[tuple[datetime, list[dict]]]:
    review = json.loads(Path("ml/fixture4-source-review.json").read_bytes())
    result = []
    for item in review["snapshots"]:
        if not item["success"]:
            continue
        path = Path("ml/data/raw/fixture_snapshots") / (item["capture_utc"] + ".json")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != item["sha256"]:
            raise ValueError("Archived fixture bytes mismatch")
        result.append(
            (
                datetime.strptime(item["capture_utc"], "%Y%m%d%H%M%S").replace(tzinfo=UTC),
                json.loads(body),
            )
        )
    return result


def build(
    matches: list[dict],
    quarantine: set,
    deadlines: dict,
    fixtures: dict,
    archives: list,
    seasons: tuple,
) -> tuple[list[dict], list[dict], dict]:
    histories = defaultdict(list)
    for m in matches:
        histories[m["season"], m["element"]].append(m)
    for history in histories.values():
        history.sort(key=lambda m: (m["kickoff_utc"], m["fixture_id"]))
    rows, teams = [], []
    counters = defaultdict(int)
    context = {}
    for season in seasons:
        for gw in range(1, 39):
            cutoff = deadlines[season, gw]
            strengths = team_features(fixtures[season], gw, cutoff)
            candidates = [
                (t, fs)
                for t, fs in archives
                if 0 < (cutoff - t).total_seconds() <= 86400
                and min(f["kickoff_time"] for f in fs if f.get("kickoff_time"))[:4] == season[:4]
            ]
            schedule = max(candidates, key=lambda x: x[0])[1] if candidates else fixtures[season]
            evidence = (
                "timestamped_archive_within_24h"
                if candidates
                else "retrospective_final_schedule_proxy"
            )
            index = {f["id"]: f for f in schedule}
            counters[season + ":" + evidence + ":gameweeks"] += 1
            for actual in fixtures[season]:
                if actual["event"] != gw:
                    continue
                known = index.get(actual["id"])
                if known is None or known.get("event") != gw or not known.get("kickoff_time"):
                    counters[season + ":archive_assignment_conflicts"] += 1
                    continue
                for team in (actual["team_h"], actual["team_a"]):
                    f = fixture_features(known, team, schedule, strengths)
                    context[season, gw, actual["id"], team] = f, evidence
                    teams.append(
                        {
                            "season": season,
                            "gameweek": gw,
                            "fixture_id": actual["id"],
                            "team_id": team,
                            "schedule_evidence": evidence,
                            **f,
                            "target_goals": actual["team_h_score"]
                            if team == actual["team_h"]
                            else actual["team_a_score"],
                        }
                    )
    for m in matches:
        season, pid, gw = m["season"], m["element"], m["gameweek"]
        if season not in seasons:
            continue
        cutoff = deadlines[season, gw]
        history = [
            r
            for r in histories[season, pid]
            if r["gameweek"] < gw and r["finished"] and r["kickoff_utc"] + LAG < cutoff
        ]
        reason = (
            "quarantined"
            if (season, pid, gw) in quarantine
            else "short_history"
            if len(history) < 3
            else "transfer_proxy"
            if history[-1]["team_id"] != m["team_id"]
            else None
        )
        if reason:
            counters[season + ":excluded:" + reason] += 1
            continue
        key = season, gw, m["fixture_id"], m["team_id"]
        if key not in context or m["minutes"] is None or m["starts"] is None:
            counters[season + ":excluded:missing"] += 1
            continue
        f, evidence = context[key]
        p = player_features(history, m["position"])
        row = {
            "season": season,
            "gameweek": gw,
            "fixture_id": m["fixture_id"],
            "element": pid,
            "cutoff_utc": cutoff,
            "schedule_evidence": evidence,
            "position_provenance": "recorded_match_position_unverified_deadline_registration",
            **p,
            **f,
            **{
                f"target_{s}": m[s]
                for s in (
                    "minutes",
                    "starts",
                    "goals_scored",
                    "assists",
                    "saves",
                    "yellow_cards",
                    "total_points",
                )
            },
        }
        if not set(FEATURE_COLUMNS) <= row.keys():
            raise ValueError("Feature schema mismatch")
        rows.append(row)
    return rows, teams, dict(counters)
