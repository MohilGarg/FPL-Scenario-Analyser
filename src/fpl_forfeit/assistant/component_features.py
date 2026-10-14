"""Shared historical/live component inputs. Future results never enter lagged form."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import fmean

POSITIONS = ("GK", "DEF", "MID", "FWD")
PLAYER_COLUMNS = (
    "history_count",
    "minutes5",
    "minutes10",
    "starts5",
    "appearance5",
    "sustained_start5",
    "xg90",
    "xa90",
    "goals90",
    "assists90",
    "saves90",
    "yellow90",
    "history_missing",
) + tuple(f"position_{p}" for p in POSITIONS)
TEAM_COLUMNS = (
    "own_gf",
    "own_ga",
    "opp_gf",
    "opp_ga",
    "own_recent_gf",
    "opp_recent_ga",
    "own_count",
    "opp_count",
    "home",
    "fixture_count",
    "rest_days",
    "congestion7",
)
FEATURE_COLUMNS = PLAYER_COLUMNS + TEAM_COLUMNS
LAG = timedelta(hours=24)


def player_features(rows: list[dict], position: str) -> dict:
    if position not in POSITIONS:
        raise ValueError("Unknown historical/live position")
    n = len(rows)
    out = {
        "history_count": n,
        "history_missing": not n,
        **{f"position_{p}": int(p == position) for p in POSITIONS},
    }
    for window in (5, 10):
        tail = rows[-window:]
        values = [r.get("minutes") for r in tail]
        out[f"minutes{window}"] = (
            fmean(values) if tail and all(v is not None for v in values) else None
        )
    recent = rows[-5:]
    out["starts5"] = (
        fmean(r["starts"] for r in recent)
        if recent and all(r.get("starts") is not None for r in recent)
        else None
    )
    out["appearance5"] = (
        fmean(r["minutes"] > 0 for r in recent)
        if recent and all(r.get("minutes") is not None for r in recent)
        else None
    )
    out["sustained_start5"] = (
        fmean(r["starts"] == 1 and r["minutes"] >= 60 for r in recent)
        if recent
        and all(r.get("starts") is not None and r.get("minutes") is not None for r in recent)
        else None
    )
    tail = rows[-10:]
    minutes = sum(r.get("minutes") or 0 for r in tail)
    for name, field in (
        ("xg90", "expected_goals"),
        ("xa90", "expected_assists"),
        ("goals90", "goals_scored"),
        ("assists90", "assists"),
        ("saves90", "saves"),
        ("yellow90", "yellow_cards"),
    ):
        values = [r.get(field) for r in tail]
        out[name] = (
            sum(values) * 90 / minutes
            if minutes
            and all(v is not None for v in values)
            and all(r.get("minutes") is not None for r in tail)
            else None
        )
    return out


def team_features(fixtures: list[dict], gw: int, cutoff: datetime) -> dict[int, dict]:
    eligible = sorted(
        (
            f
            for f in fixtures
            if f.get("finished")
            and f.get("event") is not None
            and int(f["event"]) < gw
            and f.get("kickoff_time")
            and datetime.fromisoformat(f["kickoff_time"]) + LAG < cutoff
            and f.get("team_h_score") is not None
            and f.get("team_a_score") is not None
        ),
        key=lambda f: (f["kickoff_time"], f["id"]),
    )
    league = (
        fmean([f[k] for f in eligible for k in ("team_h_score", "team_a_score")])
        if eligible
        else 1.35
    )
    teams = {int(f[k]) for f in fixtures for k in ("team_h", "team_a")}
    result = {}
    for team in teams:
        matches = [
            (f["team_h_score"], f["team_a_score"])
            if f["team_h"] == team
            else (f["team_a_score"], f["team_h_score"])
            for f in eligible
            if team in (f["team_h"], f["team_a"])
        ]
        count = len(matches)
        result[team] = {
            "gf": (sum(s for s, _ in matches) + 5 * league) / (count + 5),
            "ga": (sum(s for _, s in matches) + 5 * league) / (count + 5),
            "recent_gf": (sum(s for s, _ in matches[-5:]) + 5 * league) / (min(count, 5) + 5),
            "recent_ga": (sum(s for _, s in matches[-5:]) + 5 * league) / (min(count, 5) + 5),
            "count": count,
        }
    return result


def fixture_features(
    fixture: dict, team: int, schedule: list[dict], strengths: dict[int, dict]
) -> dict:
    home = fixture["team_h"] == team
    opponent = fixture["team_a"] if home else fixture["team_h"]
    own, other = strengths[team], strengths[opponent]
    kickoff = datetime.fromisoformat(fixture["kickoff_time"])
    prior = [
        datetime.fromisoformat(f["kickoff_time"])
        for f in schedule
        if f.get("kickoff_time")
        and team in (f["team_h"], f["team_a"])
        and datetime.fromisoformat(f["kickoff_time"]) < kickoff
    ]
    return {
        "own_gf": own["gf"],
        "own_ga": own["ga"],
        "opp_gf": other["gf"],
        "opp_ga": other["ga"],
        "own_recent_gf": own["recent_gf"],
        "opp_recent_ga": other["recent_ga"],
        "own_count": own["count"],
        "opp_count": other["count"],
        "home": int(home),
        "fixture_count": sum(
            f.get("event") == fixture["event"] and team in (f["team_h"], f["team_a"])
            for f in schedule
        ),
        "rest_days": min(30, (kickoff - max(prior)).total_seconds() / 86400) if prior else None,
        "congestion7": sum(kickoff - timedelta(days=7) <= t for t in prior),
    }
