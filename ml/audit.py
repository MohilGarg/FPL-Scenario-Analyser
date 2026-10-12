"""Season-level source profiling and compact, reproducible Markdown audits."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .dataset import CsvData, Row, missing, number, strict_sum


def column_profile(data: CsvData) -> dict[str, Any]:
    return {column: {
        "missing": sum(missing(row[column]) for row in data.rows),
        "rows": len(data.rows),
    } for column in data.columns}


def season_profile(
    season: str, tables: dict[str, CsvData], matches: list[Row], gameweeks: list[Row],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    history = tables["history"]
    players = tables["players"]
    player_index = {int(p["id"]): p for p in players.rows}
    player_matches: dict[int, list[Row]] = defaultdict(list)
    names: dict[int, set[str]] = defaultdict(set)
    codes: dict[str, list[str]] = defaultdict(list)
    for p in players.rows:
        if not missing(p.get("code")):
            codes[p["code"]].append(p["id"])
    for m in matches:
        player_matches[m["element"]].append(m)
        names[m["element"]].add(m["name"])
    mismatches = []
    for element, records in sorted(player_matches.items()):
        official_snapshot = number(player_index[element].get("total_points"), integer=True)
        history_sum = strict_sum([m["total_points"] for m in records])
        if history_sum is not None and official_snapshot is not None and history_sum != official_snapshot:
            mismatches.append({"element": element, "name": records[0]["name"],
                               "history_points": history_sum, "snapshot_points": official_snapshot})
    return {
        "season": season, "source_rows": len(history.rows), "match_records": len(matches),
        "player_gameweeks": len(gameweeks), "players": len(player_matches),
        "metadata_elements": len(players.rows), "fixtures": len(tables["fixtures"].rows),
        "gameweeks": sorted({m["gameweek"] for m in matches}),
        "rows_per_gameweek": dict(sorted(Counter(m["gameweek"] for m in matches).items())),
        "double_gameweeks": sum(g["fixture_records"] > 1 for g in gameweeks),
        "incomplete_targets": sum(not g["target_complete"] for g in gameweeks),
        "deadline_gated_rows": sum(g["feature_cutoff_utc"] is not None for g in gameweeks),
        "missing_player_codes": sum(missing(p.get("code")) for p in players.rows),
        "temporary_player_codes": sum(p.get("has_temporary_code", "").lower() == "true"
                                      for p in players.rows),
        "duplicate_player_codes": {k: v for k, v in codes.items() if len(v) > 1},
        "elements_with_multiple_names": {str(k): sorted(v) for k, v in names.items() if len(v) > 1},
        "season_total_mismatches": mismatches,
        "columns": {name: column_profile(table) for name, table in tables.items()},
        **diagnostics,
    }


def cross_season_identity(profiles: list[dict], tables: dict[str, dict[str, CsvData]]) -> dict:
    ids: dict[str, set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    team_ids: dict[str, set[str]] = defaultdict(set)
    for profile in profiles:
        season = profile["season"]
        for p in tables[season]["players"].rows:
            ids[p["id"]].add(p["code"])
            names[p["code"]].add(p["first_name"] + " " + p["second_name"])
        for t in tables[season]["teams"].rows:
            team_ids[t["id"]].add(t["code"])
    return {
        "element_ids_with_different_codes": sum(len(v) > 1 for v in ids.values()),
        "team_ids_with_different_codes": sum(len(v) > 1 for v in team_ids.values()),
        "player_codes_with_name_variants": {k: sorted(v) for k, v in names.items() if len(v) > 1},
    }


def markdown_report(profiles: list[dict], manifest: dict, identity: dict) -> str:
    lines = ["# Generated historical source audit", "",
             f"Source revision: `{manifest['revision']}`. Downloaded bytes: "
             f"{sum(v['bytes'] for v in manifest['files'].values()):,}.", "",
             "Counts describe observed fixture records, including zero-minute rows; absent rows "
             "are not manufactured as zero-point targets.", "",
             "| Season | Source rows | Unique player matches | Player-GWs | Players | Doubles | GWs |",
             "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for p in profiles:
        lines.append(f"| {p['season']} | {p['source_rows']:,} | {p['match_records']:,} | "
                     f"{p['player_gameweeks']:,} | {p['players']} | {p['double_gameweeks']} | "
                     f"{min(p['gameweeks'], default=0)}–{max(p['gameweeks'], default=0)} |")
    lines += ["", "## History column availability and missingness", "",
              "Each cell is missing / rows. `absent` is different from an observed zero.", "",
              "| Column | " + " | ".join(p["season"] for p in profiles) + " |",
              "| --- | " + " | ".join("---:" for _ in profiles) + " |"]
    columns = sorted({c for p in profiles for c in p["columns"]["history"]})
    for c in columns:
        cells = []
        for p in profiles:
            item = p["columns"]["history"].get(c)
            cells.append(f"{item['missing']:,} / {item['rows']:,}" if item else "absent")
        lines.append(f"| `{c}` | " + " | ".join(cells) + " |")
    lines += ["", "## Integrity and identity", ""]
    for p in profiles:
        lines += [f"- {p['season']}: {p['exact_duplicate_rows']} exact duplicates removed from "
                  f"normalised matches; {p['assistant_manager_rows']} AM rows excluded; "
                  f"{p['incomplete_targets']} incomplete targets; "
                  f"{p['deadline_gated_rows']} rows with supplied deadlines.",
                  f"  Reported team / fixture event / position mismatches: "
                  f"{p['reported_team_mismatches']} / {p['fixture_event_mismatches']} / "
                  f"{p['player_position_mismatches']}. Missing/temporary/duplicate player codes: "
                  f"{p['missing_player_codes']} / {p['temporary_player_codes']} / "
                  f"{len(p['duplicate_player_codes'])}.",
                  f"  Season total discrepancies: {p['season_total_mismatches'] or 'none'}."]
    lines += [f"- Cross-season reused element IDs with different codes: "
              f"{identity['element_ids_with_different_codes']}; reused team IDs with different codes: "
              f"{identity['team_ids_with_different_codes']}.", "",
              "Full metadata column profiles, identity variants and checksums are in the generated "
              "JSON audit/manifest. Scoring definitions, feature timing and Phase 2 recommendations "
              "are documented in `ml/reports/DATA_AUDIT.md`.", ""]
    return "\n".join(lines)
