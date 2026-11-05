"""Shared chronological minutes experiment population; no network or injury reconstruction."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from fpl_forfeit.assistant.minutes_model import history_features, playing_state
from fpl_forfeit.models import MatchAppearance, Player, Position
from ml.dataset import load_deadlines

SEASONS = ("2023-24", "2024-25")


def eligible_history(history: list[dict], gw: int, cutoff) -> list[dict]:
    return sorted(
        (
            r
            for r in history
            if r["gameweek"] < gw
            and r["finished"]
            and r["kickoff_utc"] + timedelta(hours=24) < cutoff
        ),
        key=lambda r: (r["kickoff_utc"], r["fixture_id"]),
    )


def historical_player(current: dict, prior: list[dict], *, football: bool = False) -> Player:
    def total(name):
        return (
            sum(r[name] for r in prior)
            if prior and all(r.get(name) is not None for r in prior)
            else None
        )

    return Player(
        current["element"],
        current["name"],
        current["team_id"],
        str(current["team_id"]),
        Position(current["position"]),
        season_starts=total("starts"),
        season_minutes=total("minutes"),
        season_team_matches=len(prior),
        appearance_history=tuple(
            MatchAppearance(r["fixture_id"], r["gameweek"], r["minutes"], bool(r["starts"]))
            for r in prior
        ),
        **(
            {
                "season_goals": total("goals_scored"),
                "season_assists": total("assists"),
                "season_saves": total("saves"),
                "season_yellow_cards": total("yellow_cards"),
                "season_defensive_contribution": total("defensive_contribution"),
                "expected_goals": total("expected_goals"),
                "expected_assists": total("expected_assists"),
            }
            if football
            else {}
        ),
    )


def groups(prior: list[dict], current: dict) -> dict[str, bool]:
    recent = prior[-5:]
    n = len(recent)
    starts = sum(r["starts"] for r in recent) / n if n else 0
    minutes = sum(r["minutes"] for r in recent) / n if n else 0
    last3 = prior[-3:]
    return {
        "all": True,
        "selection_candidates": n == 5 and starts >= 0.8 and minutes >= 60,
        "fixed_selection_candidates": n > 0 and starts >= 0.8 and minutes >= 60,
        "rotation_prone": n == 5 and 0.2 <= starts < 0.8,
        "recent_substitutes": len(last3) == 3
        and sum(not r["starts"] and r["minutes"] > 0 for r in last3) >= 2,
        "sparse_1_to3": 0 < len(prior) <= 3,
        "cold_no_history": not prior,
        # Club registration was not archived. This is a retrospective diagnostic,
        # never used to choose a historical population or train a feature.
        "club_change_proxy_diagnostic": bool(prior) and prior[-1]["team_id"] != current["team_id"],
    }


def load_matches(root: Path, seasons=SEASONS, *, football: bool = False):
    import pyarrow.parquet as pq

    columns = (
        None
        if football
        else [
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
            "total_points",
        ]
    )
    rows = pq.read_table(
        root / "match_records.parquet", filters=[("season", "in", list(seasons))], columns=columns
    ).to_pylist()
    quality = pq.read_table(
        root / "quality.parquet",
        filters=[("season", "in", list(seasons))],
        columns=["season", "element", "gameweek", "evaluation_eligible"],
    ).to_pylist()
    quarantine = {
        (r["season"], r["element"], r["gameweek"]) for r in quality if not r["evaluation_eligible"]
    }
    return rows, quarantine, load_deadlines(root / "deadlines.csv")


def training_rows(root: Path) -> list[dict]:
    matches, quarantine, deadlines = load_matches(root)
    histories = defaultdict(list)
    for row in matches:
        histories[row["player_key"]].append(row)
    result = []
    cache = {}
    ready = {}
    for row in matches:
        key = row["season"], row["gameweek"]
        ready[key] = max(ready.get(key, row["kickoff_utc"]), row["kickoff_utc"])
    for target in matches:
        if (target["season"], target["element"], target["gameweek"]) in quarantine:
            continue
        key = target["player_key"], target["gameweek"]
        if key not in cache:
            cutoff = deadlines[target["season"], target["gameweek"]]
            prior = eligible_history(histories[target["player_key"]], target["gameweek"], cutoff)
            # No historically unproved registration signal is allowed into X.
            feature_current = {
                **target,
                "team_id": prior[-1]["team_id"] if prior else target["team_id"],
            }
            player = historical_player(feature_current, prior)
            cache[key] = (
                player,
                prior,
                history_features(player, prior),
                groups(prior, target),
                cutoff,
            )
        player, prior, features, subset, cutoff = cache[key]
        if target["kickoff_utc"] <= cutoff:
            raise ValueError("Target starts before forecast deadline")
        result.append(
            {
                "season": target["season"],
                "gameweek": target["gameweek"],
                "deadline": cutoff,
                "ready": ready[target["season"], target["gameweek"]] + timedelta(hours=24),
                "player": player,
                "history": prior,
                "features": features,
                "groups": subset,
                "state": playing_state(target["minutes"], target["starts"]),
                "minutes": target["minutes"],
                "starts": target["starts"],
                "player_id": target["element"],
                "fixture_id": target["fixture_id"],
            }
        )
    return result
