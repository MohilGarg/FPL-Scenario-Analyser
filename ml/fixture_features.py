"""Pure deadline-gated team history and explicitly retrospective fixture context.

This does not import training code, infer schedule versions, or read final team strengths.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterator

from ml.dataset import FEATURE_COLUMNS, RESULT_LAG, Row, boolean, number, required_int, timestamp

VENUES = ("overall", "home", "away")
PERIODS = ("season", "last3", "last5", "last10")
MEASURES = ("match_count", "goals_scored_sum", "goals_conceded_sum", "goals_scored_mean",
            "goals_conceded_mean", "clean_sheet_frequency", "goals_scored_smoothed",
            "goals_conceded_smoothed", "clean_sheet_frequency_smoothed", "attack_ratio", "concession_ratio")
TEAM_FEATURE_COLUMNS = tuple(f"pre_team_{v}_{p}_{m}" for v in VENUES for p in PERIODS for m in MEASURES)
TEAM_FEATURE_COLUMNS += tuple(f"pre_team_{v}_{p}_short_history" for v in VENUES for p in PERIODS if p != "season")
TEAM_FEATURE_COLUMNS += (
    "pre_team_no_history", "pre_team_last_match_age_days", "pre_team_matches_past7_days",
    "pre_team_matches_past14_days", "pre_team_league_history_matches",
    "pre_team_league_home_goal_mean", "pre_team_league_away_goal_mean",
    "pre_team_league_home_clean_sheet_frequency", "pre_team_league_away_clean_sheet_frequency",
)
PLAYER_TEAM_FEATURE_COLUMNS = tuple(c.replace("pre_team_", "pre_gw_last_observed_club_", 1) for c in TEAM_FEATURE_COLUMNS)
PLAYER_TEAM_FEATURE_COLUMNS += ("pre_gw_last_observed_club_record_age_days", "pre_gw_last_observed_club_missing")


def schema_document() -> dict[str, Any]:
    return {"version": "fixture-context-v1", "frozen_player_v1": list(FEATURE_COLUMNS),
            "team_deadline_inputs": list(TEAM_FEATURE_COLUMNS), "player_context_sidecar_inputs": list(PLAYER_TEAM_FEATURE_COLUMNS),
            "approved_player_v2_inputs": [*FEATURE_COLUMNS, *PLAYER_TEAM_FEATURE_COLUMNS],
            "upcoming_fixture_inputs": [], "team_xg_inputs": [],
            "join_keys": ["season", "player_key", "gameweek", "feature_cutoff_utc"],
            "excluded": ["team_id", "team_code", "last_observed_team_id", "current_player_club",
                         "retro_*", "quality_*", "source_xP", "final_team_strength", "observed_*", "target_*"],
            "conditions": "Verified deadline; earlier same-season GWs; kickoff + 24h strictly before cutoff. Player club is last eligible observed club, not current membership."}


def validate_config(config: dict[str, Any]) -> None:
    if (config["schema_version"] != "fixture-context-v1" or config["rolling_matches"] != [3, 5, 10]
            or config["result_buffer_hours"] != RESULT_LAG.total_seconds() / 3600 or config["season_carryover"]
            or config["schedule_policy"] != "final_assignments_research_only"
            or config["player_club_policy"] != "last_eligible_observed_club_proxy"):
        raise ValueError("Unsupported fixture feature protocol")
    if (not 0 < config["smoothing_pseudo_matches"] <= 20 or not 0 < config["opening_goal_prior"] < 5
            or not 0 <= config["opening_clean_sheet_prior"] <= 1):
        raise ValueError("Invalid fixed smoothing priors")


def team_index(season: str, source: list[Row]) -> dict[int, Row]:
    result = {}
    codes = set()
    for row in source:
        identity = required_int(row.get("id"), "team id")
        code = required_int(row.get("code"), "team code")
        if identity < 1 or identity in result or code in codes or not row.get("name"):
            raise ValueError("Invalid/duplicate team identity")
        result[identity] = {"season": season, "team_id": identity, "team_code": code, "team_name": row["name"]}
        codes.add(code)
    return result


def normalise_fixtures(season: str, source: list[Row], teams: dict[int, Row]) -> list[Row]:
    result, seen = [], set()
    for raw in source:
        identity = required_int(raw.get("id"), "fixture id")
        if identity < 1 or identity in seen:
            raise ValueError("Invalid/duplicate fixture id")
        seen.add(identity)
        event = number(raw.get("event"), integer=True)
        if event is not None and not 1 <= event <= 38:
            raise ValueError("Invalid fixture GW")
        home, away = (number(raw.get(c), integer=True) for c in ("team_h", "team_a"))
        h_score, a_score = (number(raw.get(c), integer=True) for c in ("team_h_score", "team_a_score"))
        if any(v is not None and v < 0 for v in (h_score, a_score)):
            raise ValueError("Negative fixture score")
        identities_known = home in teams and away in teams and home != away
        kickoff = timestamp(raw.get("kickoff_time"))
        finished = boolean(raw.get("finished")) is True
        result.append({"season": season, "fixture_id": identity, "gameweek": event, "home_id": home,
                       "away_id": away, "kickoff_utc": kickoff, "home_score": h_score, "away_score": a_score,
                       "finished": finished, "identity_complete": identities_known,
                       "result_complete": identities_known and finished and h_score is not None and a_score is not None})
    return result


def eligible(fixtures: list[Row], season: str, gw: int, cutoff: datetime) -> list[Row]:
    # Final event is used only as an additional exclusion gate, never to infer an upcoming schedule.
    return sorted([f for f in fixtures if f["season"] == season and f["result_complete"]
                   and f["gameweek"] is not None and f["gameweek"] < gw and f["kickoff_utc"] is not None
                   and f["kickoff_utc"] + RESULT_LAG < cutoff], key=lambda f: (f["kickoff_utc"], f["fixture_id"]))


def validate_match_links(fixtures: list[Row], matches: list[Row]) -> None:
    """A contradictory prepared kickoff/club must not choose the earlier information gate."""
    index = {(f["season"], f["fixture_id"]): f for f in fixtures}
    for m in matches:
        f = index.get((m["season"], m["fixture_id"]))
        if f is None:
            raise ValueError("Player match references a missing fixture")
        if ((f["identity_complete"] and m["team_id"] not in (f["home_id"], f["away_id"]))
                or (f["gameweek"] is not None and m["gameweek"] != f["gameweek"])
                or (f["kickoff_utc"] is not None and m["kickoff_utc"] is not None and f["kickoff_utc"] != m["kickoff_utc"])
                or m["finished"] != f["finished"]):
            raise ValueError("Prepared match contradicts fixture identity, round, kickoff or completion")


def side(fixture: Row, team: int) -> Row:
    home = fixture["home_id"] == team
    if not home and fixture["away_id"] != team:
        raise ValueError("Team not in fixture")
    return {"kickoff_utc": fixture["kickoff_utc"], "was_home": home,
            "scored": fixture["home_score"] if home else fixture["away_score"],
            "conceded": fixture["away_score"] if home else fixture["home_score"]}


def team_values(history: list[Row], league: list[Row], cutoff: datetime, config: dict) -> Row:
    home_mean = sum(f["home_score"] for f in league) / len(league) if league else config["opening_goal_prior"]
    away_mean = sum(f["away_score"] for f in league) / len(league) if league else config["opening_goal_prior"]
    home_cs = sum(f["away_score"] == 0 for f in league) / len(league) if league else config["opening_clean_sheet_prior"]
    away_cs = sum(f["home_score"] == 0 for f in league) / len(league) if league else config["opening_clean_sheet_prior"]
    values = {"pre_team_no_history": not history, "pre_team_league_history_matches": len(league),
              "pre_team_league_home_goal_mean": home_mean, "pre_team_league_away_goal_mean": away_mean,
              "pre_team_league_home_clean_sheet_frequency": home_cs,
              "pre_team_league_away_clean_sheet_frequency": away_cs,
              "pre_team_last_match_age_days": (cutoff - history[-1]["kickoff_utc"]).total_seconds() / 86400 if history else None}
    for days in (7, 14):
        values[f"pre_team_matches_past{days}_days"] = sum(cutoff - h["kickoff_utc"] <= timedelta(days=days) for h in history)
    pseudo = config["smoothing_pseudo_matches"]
    for venue in VENUES:
        records = history if venue == "overall" else [h for h in history if h["was_home"] == (venue == "home")]
        priors = {"home": (home_mean, away_mean, home_cs), "away": (away_mean, home_mean, away_cs),
                  "overall": ((home_mean + away_mean) / 2, (home_mean + away_mean) / 2, (home_cs + away_cs) / 2)}[venue]
        for period in PERIODS:
            window = records if period == "season" else records[-int(period[4:]):]
            n = len(window)
            scored, conceded = (sum(h[key] for h in window) for key in ("scored", "conceded"))
            cs = sum(h["conceded"] == 0 for h in window)
            gf, ga, clean = ((value + pseudo * prior) / (n + pseudo) for value, prior in zip((scored, conceded, cs), priors))
            prefix = f"pre_team_{venue}_{period}"
            values.update({f"{prefix}_match_count": n, f"{prefix}_goals_scored_sum": scored if n else None,
                f"{prefix}_goals_conceded_sum": conceded if n else None, f"{prefix}_goals_scored_mean": scored / n if n else None,
                f"{prefix}_goals_conceded_mean": conceded / n if n else None, f"{prefix}_clean_sheet_frequency": cs / n if n else None,
                f"{prefix}_goals_scored_smoothed": gf, f"{prefix}_goals_conceded_smoothed": ga,
                f"{prefix}_clean_sheet_frequency_smoothed": clean,
                f"{prefix}_attack_ratio": gf / priors[0] if priors[0] else 1.,
                f"{prefix}_concession_ratio": ga / priors[1] if priors[1] else 1.})
            if period != "season":
                values[f"{prefix}_short_history"] = n < int(period[4:])
    return values


def team_deadline_features(fixtures: list[Row], rosters: dict[str, dict[int, Row]],
                           deadlines: dict[tuple[str, int], datetime], config: dict) -> list[Row]:
    validate_config(config)
    result = []
    for season, teams in sorted(rosters.items()):
        for gw in range(1, 39):
            cutoff = deadlines.get((season, gw))
            league = eligible(fixtures, season, gw, cutoff) if cutoff else []
            for team_id, identity in sorted(teams.items()):
                history = [side(f, team_id) for f in league if team_id in (f["home_id"], f["away_id"])]
                result.append({**identity, "gameweek": gw, "feature_cutoff_utc": cutoff,
                    "feature_status": "verified_deadline_results_lag_24h" if cutoff else "missing_deadline",
                    **(team_values(history, league, cutoff, config) if cutoff else dict.fromkeys(TEAM_FEATURE_COLUMNS))})
    return result


def player_club_features(player_rows: list[Row], matches: list[Row], team_rows: list[Row],
                         deadlines: dict[tuple[str, int], datetime]) -> Iterator[Row]:
    by_player = defaultdict(list)
    for match in matches:
        by_player[match["season"], match["player_key"]].append(match)
    index = {(r["season"], r["team_id"], r["gameweek"]): r for r in team_rows}
    for row in player_rows:
        season, player, gw = row["season"], row["player_key"], row["gameweek"]
        cutoff = deadlines.get((season, gw))
        prior = [m for m in by_player[season, player] if cutoff and m["gameweek"] < gw and m["finished"]
                 and m["kickoff_utc"] is not None and m["kickoff_utc"] + RESULT_LAG < cutoff]
        latest_time = max((m["kickoff_utc"] for m in prior), default=None)
        latest = [m for m in prior if m["kickoff_utc"] == latest_time]
        clubs = {m["team_id"] for m in latest}
        team_id = next(iter(clubs)) if len(clubs) == 1 else None
        team = index.get((season, team_id, gw)) if cutoff else None
        values = dict.fromkeys(PLAYER_TEAM_FEATURE_COLUMNS)
        if team:
            values.update({dst: team[src] for src, dst in zip(TEAM_FEATURE_COLUMNS, PLAYER_TEAM_FEATURE_COLUMNS)})
            values["pre_gw_last_observed_club_record_age_days"] = (cutoff - latest_time).total_seconds() / 86400
        values["pre_gw_last_observed_club_missing"] = not bool(team) if cutoff else None
        yield {"season": season, "player_key": player, "gameweek": gw, "feature_cutoff_utc": cutoff,
               "last_observed_team_id": team_id if team else None, "club_status": "last_observed_proxy" if team else "unknown",
               **values}


def retrospective_context(fixtures: list[Row], team_rows: list[Row], deadlines: dict,
                           rosters: dict[str, dict[int, Row]]) -> tuple[list[Row], list[Row]]:
    """Final assignments never enter any approved schema or player-club join."""
    per_fixture, per_gw = [], []
    strength = {(r["season"], r["team_id"], r["gameweek"]): r for r in team_rows}
    selected = ("overall_season_attack_ratio", "overall_season_concession_ratio",
                "overall_last5_goals_scored_smoothed", "overall_last5_goals_conceded_smoothed",
                "overall_last5_clean_sheet_frequency_smoothed", "overall_last5_match_count")
    for row in team_rows:
        season, team_id, gw = row["season"], row["team_id"], row["gameweek"]
        cutoff = deadlines.get((season, gw))
        final = [f for f in fixtures if f["season"] == season and f["gameweek"] == gw and team_id in (f["home_id"], f["away_id"])]
        final.sort(key=lambda f: (f["kickoff_utc"] is None, f["kickoff_utc"] or datetime.max.replace(tzinfo=cutoff.tzinfo if cutoff else None), f["fixture_id"]))
        assignments_complete = all(f["gameweek"] is not None and f["identity_complete"] for f in fixtures if f["season"] == season)
        per_gw.append({"season": season, "team_id": team_id, "gameweek": gw, "feature_cutoff_utc": cutoff,
            "schedule_status": "research_only_final_assignment", "retro_fixture_ids": [f["fixture_id"] for f in final],
            "retro_fixture_count": len(final), "retro_blank": not final if assignments_complete else None,
            "retro_double": len(final) > 1, "retro_assignment_complete": assignments_complete,
            "retro_kickoff_coverage": sum(f["kickoff_utc"] is not None for f in final)})
        prior_kickoff = cutoff - timedelta(days=row["pre_team_last_match_age_days"]) if cutoff and row["pre_team_last_match_age_days"] is not None else None
        for f in final:
            opponent = f["away_id"] if team_id == f["home_id"] else f["home_id"]
            other = strength.get((season, opponent, gw))
            per_fixture.append({"season": season, "team_id": team_id, "gameweek": gw, "fixture_id": f["fixture_id"],
                "feature_cutoff_utc": cutoff, "schedule_status": "research_only_final_assignment",
                "retro_opponent_id": opponent, "retro_opponent_code": rosters.get(season, {}).get(opponent, {}).get("team_code"),
                "retro_was_home": team_id == f["home_id"], "retro_kickoff_utc": f["kickoff_utc"],
                "retro_pl_rest_days": (f["kickoff_utc"] - prior_kickoff).total_seconds() / 86400 if f["kickoff_utc"] and prior_kickoff else None,
                **{f"retro_opponent_lagged_{s}": other[f"pre_team_{s}"] if other else None for s in selected}})
            prior_kickoff = f["kickoff_utc"]
    return per_fixture, per_gw


def club_proxy_audit(club_rows: list[Row], matches: list[Row]) -> list[Row]:
    """Compare the lagged proxy with the observed target club only AFTER feature generation."""
    targets = defaultdict(set)
    for match in matches:
        targets[match["season"], match["player_key"], match["gameweek"]].add(match["team_id"])
    result = []
    for row in club_rows:
        clubs = targets[row["season"], row["player_key"], row["gameweek"]]
        proxy = row["last_observed_team_id"]
        result.append({"season": row["season"], "player_key": row["player_key"], "gameweek": row["gameweek"],
            "quality_proxy_team_id": proxy, "quality_observed_target_team_ids": sorted(clubs),
            "quality_proxy_matches_target_club": proxy in clubs if proxy is not None and len(clubs) == 1 else None})
    return result


def xg_coverage_audit(fixtures: list[Row], matches: list[Row]) -> list[Row]:
    groups = defaultdict(list)
    for match in matches:
        groups[match["season"], match["fixture_id"], match["team_id"]].append(match)
    result = []
    for f in fixtures:
        for team in (f["home_id"], f["away_id"]):
            records = groups[f["season"], f["fixture_id"], team]
            known = all(m["expected_goals"] is not None for m in records)
            starts_known = all(m["starts"] is not None for m in records)
            result.append({"season": f["season"], "fixture_id": f["fixture_id"], "team_id": team,
                "quality_player_records": len(records), "quality_starts_sum": sum(m["starts"] for m in records) if starts_known and records else None,
                "quality_xg_missing_count": sum(m["expected_goals"] is None for m in records),
                "research_player_xg_sum": sum(m["expected_goals"] for m in records) if records and known else None,
                "quality_xg_status": "player_sum_not_verified_team_xg",
                "quality_substitute_coverage_verified": False})
    return result
