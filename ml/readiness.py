"""Post-season quality metadata and feature coverage; never predictive inputs."""

from __future__ import annotations

from .dataset import FEATURE_COLUMNS, ROLLING_STATS, WINDOWS, Row
from .deadlines import VERIFIED

CORE_FEATURES = tuple(f"pre_gw_last3_matches_{s}_mean" for s in ROLLING_STATS) + (
    "pre_gw_last3_matches_appearance_rate", "pre_gw_last3_matches_start_rate",
)


def quality_metadata(gameweeks: list[Row], profiles: list[dict]) -> list[Row]:
    # The bad fixture is unknown for Ferguson (27 vs 28): quarantine the entire
    # affected player-season rather than guess which recorded points to repair.
    inconsistencies = {(p["season"], m["element"]): m for p in profiles
                       for m in p["season_total_mismatches"]}
    rows = []
    for g in gameweeks:
        mismatch = inconsistencies.get((g["season"], g["element"]))
        reasons = []
        if mismatch:
            reasons.append("player_season_points_inconsistent_unlocalized")
        if not g["target_complete"]:
            reasons.append("incomplete_target")
        rows.append({
            "season": g["season"], "player_key": g["player_key"], "element": g["element"],
            "gameweek": g["gameweek"], "evaluation_eligible": not reasons,
            "quality_reasons": reasons, "quality_timing": "retrospective_end_of_season_audit",
            "season_history_points": mismatch["history_points"] if mismatch else None,
            "season_snapshot_points": mismatch["snapshot_points"] if mismatch else None,
        })
    return rows


def readiness_report(gameweeks: list[Row], quality: list[Row], deadlines: list[dict],
                     *, verified_archive: bool) -> dict:
    verified = {(d["season"], int(d["gameweek"])) for d in deadlines
                if verified_archive and d.get("verification_status") == VERIFIED and d["deadline_utc"]}
    quality_by_key = {(q["player_key"], q["gameweek"]): q for q in quality}
    seasons = []
    for season in sorted({g["season"] for g in gameweeks}):
        rows = [g for g in gameweeks if g["season"] == season]
        def valid(g: Row) -> bool:
            return ((season, g["gameweek"]) in verified and g["pre_gw_history_fixtures"] is not None
                    and g["pre_gw_history_fixtures"] > 0 and all(g[c] is not None for c in CORE_FEATURES))
        coverage = {c: {"non_null": sum(g[c] is not None for g in rows), "rows": len(rows)}
                    for c in FEATURE_COLUMNS}
        rolling = {}
        for unit in ("matches", "gws"):
            for n in WINDOWS:
                prefix = f"pre_gw_last{n}_{unit}"
                fields = {s: {
                    "non_null": sum(g[f"{prefix}_{s}_mean"] is not None for g in rows),
                    "full_window": sum(g[f"{prefix}_{s}_mean"] is not None
                                       and g[f"{prefix}_count"] == n for g in rows),
                } for s in ROLLING_STATS}
                for s in ("appearance_rate", "start_rate"):
                    fields[s] = {
                        "non_null": sum(g[f"{prefix}_{s}"] is not None for g in rows),
                        "full_window": sum(g[f"{prefix}_{s}"] is not None and g[f"{prefix}_count"] == n
                                           for g in rows),
                    }
                rolling[f"last{n}_{unit}"] = fields
        seasons.append({
            "season": season, "player_gameweeks": len(rows),
            "verified_deadlines": sum((season, gw) in verified for gw in range(1, 39)),
            "missing_deadlines": [gw for gw in range(1, 39) if (season, gw) not in verified],
            "rows_with_deadlines": sum(g["feature_cutoff_utc"] is not None for g in rows),
            "rows_with_valid_features": sum(valid(g) for g in rows),
            "cold_start_rows": sum(g["pre_gw_no_history"] is True for g in rows),
            "evaluation_excluded_rows": sum(not quality_by_key[g["player_key"], g["gameweek"]]
                                            ["evaluation_eligible"] for g in rows),
            "baseline_candidate_rows": sum(valid(g) and quality_by_key[g["player_key"], g["gameweek"]]
                                           ["evaluation_eligible"] for g in rows),
            "feature_coverage": coverage, "rolling_coverage": rolling,
        })
    sufficient = all(s["verified_deadlines"] == 38 and s["baseline_candidate_rows"] > 0 for s in seasons)
    return {
        "seasons": seasons, "core_features": CORE_FEATURES,
        "valid_feature_definition": "verified deadline, positive eligible history and all core rolling means/rates known; partial windows allowed",
        "readiness": "sufficient_for_retrospective_baselines_with_assumptions" if sufficient else "not_ready",
        "point_in_time_replay_certified": False, "result_readiness_lag_hours": 24,
        "limitations": ["final result/stat revisions lack publication timestamps",
                        "24h result availability is a conservative proxy",
                        "historic full eligible rosters and blank-GW population are not reconstructed",
                        "deadline mirrors were verified; original Wayback replay was unavailable",
                        "prices, injuries, source xP and future schedule fields excluded"],
    }


def readiness_markdown(report: dict) -> str:
    lines = ["# Generated training readiness — Phase 2a", "",
             "Verdict: `" + report["readiness"] + "`. No training was performed.", "",
             "Valid features require a verified deadline and at least one eligible historical match "
             "with all core rolling statistics/rates known. This permits partial windows; full-window "
             "counts are reported separately. It does not certify original point-in-time stat vintages.", "",
             "| Season | Verified deadlines | Rows | Valid features | Cold starts | QA excluded | Baseline candidates |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for s in report["seasons"]:
        lines.append(f"| {s['season']} | {s['verified_deadlines']}/38 | {s['player_gameweeks']:,} | "
                     f"{s['rows_with_valid_features']:,} | {s['cold_start_rows']:,} | "
                     f"{s['evaluation_excluded_rows']:,} | {s['baseline_candidate_rows']:,} |")
    lines += ["", "## Every rolling statistic's coverage", "",
              "Cells are non-null / full-window rows. Short windows are never padded with zeros.", "",
              "| Window | Statistic | " + " | ".join(s["season"] for s in report["seasons"]) + " |",
              "| --- | --- | " + " | ".join("---:" for _ in report["seasons"]) + " |"]
    for window in report["seasons"][0]["rolling_coverage"]:
        for stat in report["seasons"][0]["rolling_coverage"][window]:
            cells = [s["rolling_coverage"][window][stat] for s in report["seasons"]]
            lines.append(f"| {window} | {stat} | " + " | ".join(
                f"{c['non_null']:,} / {c['full_window']:,}" for c in cells) + " |")
    lines += ["", "## Remaining limitations", ""]
    lines += [f"- {item}." for item in report["limitations"]]
    for s in report["seasons"]:
        lines.append(f"- {s['season']} unresolved deadline GWs: {s['missing_deadlines'] or 'none'}.")
    lines += ["", "Read `ml/reports/TRAINING_READINESS.md` for source provenance, excluded features, "
              "Ferguson quarantine, window definitions and the limits of baseline readiness. "
              "Per-column coverage, including every season-to-date field, is in `readiness.json`.", ""]
    return "\n".join(lines)
