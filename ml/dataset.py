"""Pure CSV normalisation and deadline-gated player-Gameweek preparation.

No network, application imports, training or reconstruction of missing roster rows.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

Row = dict[str, Any]
MISSING = {"", "none", "null", "nan", "na", "n/a"}
POSITIONS = {"1": "GK", "2": "DEF", "3": "MID", "4": "FWD", "5": "AM"}
INT_STATS = (
    "total_points", "minutes", "starts", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "saves", "bonus", "bps", "yellow_cards", "red_cards",
    "own_goals", "penalties_saved", "penalties_missed", "defensive_contribution",
    "clearances_blocks_interceptions", "recoveries", "tackles",
)
FLOAT_STATS = (
    "expected_goals", "expected_assists", "expected_goals_conceded",
    "expected_goal_involvements", "influence", "creativity", "threat", "ict_index",
)
STATS = INT_STATS + FLOAT_STATS
ROLLING_STATS = ("expected_goals", "expected_assists", "minutes", "starts", "goals_scored",
                 "assists", "total_points")
WINDOWS = (3, 5, 10)
ROLLING_PREFIXES = tuple(f"pre_gw_last{n}_{unit}" for unit in ("matches", "gws") for n in WINDOWS)
ROLLING_COLUMNS = tuple(c for prefix in ROLLING_PREFIXES for c in (
    *(f"{prefix}_{stat}_{metric}" for stat in ROLLING_STATS for metric in ("sum", "mean", "missing_count")),
    f"{prefix}_count", f"{prefix}_fixture_count", f"{prefix}_short_history",
    f"{prefix}_appearance_rate", f"{prefix}_start_rate",
))
# Explicit whitelist: neither source columns nor current-GW outcomes are features.
FEATURE_COLUMNS = (
    "pre_gw_history_fixtures", "pre_gw_history_gameweeks",
    "pre_gw_points_mean_last3_observed_gws",
) + tuple(f"pre_gw_{stat}_sum" for stat in STATS)
FEATURE_COLUMNS += (
    "pre_gw_no_history", "pre_gw_appearance_rate", "pre_gw_start_rate",
) + tuple(f"pre_gw_{s}_missing_count" for s in STATS) + ROLLING_COLUMNS
# A conservative readiness proxy, NOT a recorded publication/finalisation time.
RESULT_LAG = timedelta(hours=24)


@dataclass
class CsvData:
    columns: list[str]
    rows: list[dict[str, str]]


def missing(value: Any) -> bool:
    return value is None or str(value).strip().lower() in MISSING


def read_csv(path: Path) -> CsvData:
    """Keep every source token, including blank strings and zero, unchanged."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if not columns or len(set(columns)) != len(columns):
            raise ValueError(f"Empty or duplicate CSV header: {path}")
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError(f"Malformed CSV row: {path}")
    return CsvData(columns, rows)


def number(value: Any, *, integer: bool = False) -> int | float | None:
    if missing(value):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or (integer and not parsed.is_integer()):
        raise ValueError(f"Invalid {'integer' if integer else 'number'}: {value!r}")
    return int(parsed) if integer else parsed


def required_int(value: Any, label: str) -> int:
    parsed = number(value, integer=True)
    if parsed is None:
        raise ValueError(f"Missing {label}")
    return int(parsed)


def timestamp(value: Any) -> datetime | None:
    if missing(value):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp needs a UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def boolean(value: Any) -> bool | None:
    if missing(value):
        return None
    if str(value).lower() in {"true", "1"}:
        return True
    if str(value).lower() in {"false", "0"}:
        return False
    raise ValueError(f"Invalid boolean: {value!r}")


def strict_sum(values: list[int | float | None]) -> int | float | None:
    return sum(values) if values and all(v is not None for v in values) else None


def index_unique(rows: list[dict[str, str]], label: str) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        key = required_int(row.get("id"), f"{label} id")
        if key in result:
            raise ValueError(f"Duplicate {label} id: {key}")
        result[key] = row
    return result


def normalise_matches(
    season: str, history: CsvData, players: CsvData, fixtures: CsvData, teams: CsvData,
) -> tuple[list[Row], dict[str, Any]]:
    """Deduplicate identical fixture rows; conflicting duplicates fail explicitly."""
    player_index = index_unique(players.rows, "player")
    fixture_index = index_unique(fixtures.rows, "fixture")
    team_index = index_unique(teams.rows, "team")
    team_gw_fixtures: dict[tuple[int, int], list[int]] = defaultdict(list)
    for fixture_id, fixture in fixture_index.items():
        event = number(fixture.get("event"), integer=True)
        if event is not None:
            for team in ("team_h", "team_a"):
                team_gw_fixtures[required_int(fixture[team], "team"), int(event)].append(fixture_id)
    seen: dict[tuple[int, int], dict[str, str]] = {}
    matches: list[Row] = []
    diagnostics: dict[str, Any] = {
        "exact_duplicate_rows": 0, "assistant_manager_rows": 0,
        "reported_team_mismatches": 0, "fixture_event_mismatches": 0,
        "player_position_mismatches": 0, "unfinished_fixture_rows": 0,
    }
    for source_row, raw in enumerate(history.rows, start=2):
        element = required_int(raw.get("element"), "element")
        fixture_id = required_int(raw.get("fixture"), "fixture")
        gw = required_int(raw.get("GW"), "GW")
        if not 1 <= gw <= 38 or required_int(raw.get("round"), "round") != gw:
            raise ValueError(f"Invalid/inconsistent GW: {season}, CSV row {source_row}")
        key = (element, fixture_id)
        if key in seen:
            if seen[key] != raw:
                raise ValueError(f"Conflicting player-fixture duplicates: {season}/{key}")
            diagnostics["exact_duplicate_rows"] += 1
            continue
        seen[key] = raw
        player = player_index[element]
        position = raw.get("position")
        if position != POSITIONS.get(player["element_type"]):
            diagnostics["player_position_mismatches"] += 1
        if position == "AM" or player["element_type"] == "5":
            diagnostics["assistant_manager_rows"] += 1
            continue
        if position not in {"GK", "DEF", "MID", "FWD"}:
            raise ValueError(f"Unknown player position: {position!r}")
        fixture = fixture_index[fixture_id]
        home = boolean(raw.get("was_home"))
        if home is None:
            raise ValueError(f"Missing was_home: {season}/{key}")
        team_id = required_int(fixture["team_h" if home else "team_a"], "team")
        opponent = required_int(fixture["team_a" if home else "team_h"], "opponent")
        if required_int(raw.get("opponent_team"), "opponent_team") != opponent:
            raise ValueError(f"Fixture/opponent mapping conflict: {season}/{key}")
        if raw.get("team") != team_index[team_id]["name"]:
            diagnostics["reported_team_mismatches"] += 1
        if number(fixture.get("event"), integer=True) != gw:
            diagnostics["fixture_event_mismatches"] += 1
        finished = boolean(fixture.get("finished")) is True
        diagnostics["unfinished_fixture_rows"] += not finished
        stats = {s: number(raw.get(s), integer=s in INT_STATS) for s in STATS}
        if stats["minutes"] is not None and not 0 <= stats["minutes"] <= 130:
            raise ValueError(f"Invalid minutes: {season}/{key}")
        if stats["starts"] not in (None, 0, 1):
            raise ValueError(f"Invalid starts: {season}/{key}")
        if not finished:
            stats = dict.fromkeys(STATS)  # unplayed/partial outcomes remain unknown
        matches.append({
            "season": season, "player_key": f"{season}:{element}", "element": element,
            "player_code": number(player.get("code"), integer=True),
            "name": raw.get("name"), "position": position, "gameweek": gw,
            "fixture_id": fixture_id, "source_row": source_row, "team_id": team_id,
            "team_code": number(team_index[team_id].get("code"), integer=True),
            "opponent_id": opponent, "was_home": home,
            "team_gw_fixture_ids": sorted(team_gw_fixtures[team_id, gw]),
            "kickoff_utc": timestamp(raw.get("kickoff_time")), "finished": finished,
            **stats,
        })
    matches.sort(key=lambda r: (r["season"], r["element"], r["gameweek"], r["fixture_id"]))
    return matches, diagnostics


def load_deadlines(path: Path | None) -> dict[tuple[str, int], datetime]:
    """Accept a separately sourced calendar, never derive deadlines from final fixtures."""
    if path is None:
        return {}
    data = read_csv(path)
    required = {"season", "gameweek", "deadline_utc", "source_url"}
    if not required <= set(data.columns):
        raise ValueError(f"Deadline CSV requires {sorted(required)}")
    deadlines: dict[tuple[str, int], datetime] = {}
    for row in data.rows:
        key = (row["season"], required_int(row["gameweek"], "deadline gameweek"))
        if "verification_status" in row and row["verification_status"] != "verified_archived_official_api":
            if not missing(row["deadline_utc"]):
                raise ValueError("Unverified deadline must not contain a usable cutoff")
            continue
        deadline = timestamp(row["deadline_utc"])
        if key in deadlines or deadline is None or not 1 <= key[1] <= 38:
            raise ValueError(f"Invalid/duplicate deadline: {key}")
        if not row["source_url"].startswith("https://"):
            raise ValueError("Deadline rows require an HTTPS provenance source_url")
        deadlines[key] = deadline
    for season in {s for s, _ in deadlines}:
        ordered = sorted((g, d) for (s, g), d in deadlines.items() if s == season)
        if any(b[1] <= a[1] for a, b in zip(ordered, ordered[1:])):
            raise ValueError(f"Deadlines must increase with numeric GW: {season}")
    return deadlines


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
    gws = [{"gameweek": g, "fixture_count": len(group),
            **{s: strict_sum([r[s] for r in group]) for s in ROLLING_STATS}}
           for g, group in sorted(grouped.items())]
    values: Row = {
        "pre_gw_history_fixtures": len(prior), "pre_gw_history_gameweeks": len(gws),
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


def gameweek_dataset(
    matches: list[Row], deadlines: dict[tuple[str, int], datetime] | None = None,
) -> list[Row]:
    deadlines = deadlines or {}
    players: dict[str, list[Row]] = defaultdict(list)
    groups: dict[tuple[str, int], list[Row]] = defaultdict(list)
    for match in matches:
        players[match["player_key"]].append(match)
        groups[match["player_key"], match["gameweek"]].append(match)
    result: list[Row] = []
    for (player_key, gw), group in sorted(groups.items()):
        group.sort(key=lambda r: (r["kickoff_utc"] is None,
                                  r["kickoff_utc"] or datetime.max.replace(tzinfo=timezone.utc),
                                  r["fixture_id"]))
        first = group[0]
        cutoff = deadlines.get((first["season"], gw))
        known_kickoffs = [m["kickoff_utc"] for m in group if m["kickoff_utc"] is not None]
        if cutoff and known_kickoffs and cutoff >= min(known_kickoffs):
            raise ValueError(f"Deadline is after a target fixture kickoff: {player_key}/{gw}")
        observed_points = strict_sum([m["total_points"] for m in group])
        same_team = len({m["team_id"] for m in group}) == 1
        fixture_coverage = same_team and set(first["team_gw_fixture_ids"]) == {
            m["fixture_id"] for m in group
        }
        target = observed_points if fixture_coverage else None
        row: Row = {
            "season": first["season"], "player_key": player_key, "element": first["element"],
            "player_code": first["player_code"], "name": first["name"],
            "position": first["position"], "gameweek": gw,
            "fixture_ids": [m["fixture_id"] for m in group],
            "team_ids": [m["team_id"] for m in group],
            "opponent_ids": [m["opponent_id"] for m in group],
            "was_home": [m["was_home"] for m in group],
            "fixture_records": len(group), "target_points": target,
            "target_complete": target is not None,
            "observed_points": observed_points,
            "fixture_coverage": "complete_final_schedule" if fixture_coverage else "unknown",
            "feature_cutoff_utc": cutoff,
            "feature_status": "deadline_supplied_results_lag_24h" if cutoff else "no_deadline",
            **{f"observed_{s}": strict_sum([m[s] for m in group]) for s in STATS
               if s != "total_points"},
            **dict.fromkeys(FEATURE_COLUMNS),
        }
        if cutoff:
            # No same-GW fixture (including an earlier DGW leg), later GW, other season,
            # missing kickoff or result too close to the supplied deadline can enter.
            prior = [m for m in players[player_key]
                     if m["gameweek"] < gw and m["finished"] and m["kickoff_utc"] is not None
                     and m["kickoff_utc"] + RESULT_LAG < cutoff]
            row.update(historical_features(prior))
        result.append(row)
    result.sort(key=lambda r: (r["season"], r["element"], r["gameweek"]))
    return result


def feature_values(row: Row) -> Row:
    """The only supported entry point for future model inputs; null without a cutoff."""
    return {key: row[key] for key in FEATURE_COLUMNS}
