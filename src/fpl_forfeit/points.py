"""Shared fixture scoring; official awarded totals remain the source of truth."""

from __future__ import annotations

from collections.abc import Mapping

from .models import Position

GOAL_POINTS = {
    Position.GOALKEEPER: 10,
    Position.DEFENDER: 6,
    Position.MIDFIELDER: 5,
    Position.FORWARD: 4,
}
CLEAN_SHEET_POINTS = {
    Position.GOALKEEPER: 4,
    Position.DEFENDER: 4,
    Position.MIDFIELDER: 1,
    Position.FORWARD: 0,
}

SCORING_VERSIONS = {
    "2023-24": (6, False),
    "2024-25": (10, False),
    "2025-26": (10, True),
    "2026-27": (10, True),
}
CURRENT_SCORING_SEASON = "2026-27"


def event_points(
    position: Position,
    stats: Mapping[str, int],
    *,
    season: str = CURRENT_SCORING_SEASON,
) -> int:
    """Score one fixture, using goals conceded while on field (also after a red).

    clean_sheets is explicit, so a subbed defender can retain a clean sheet even
    when the final team score is not nil. Minutes exclude stoppage time.
    """
    goalkeeper_goal, defensive_enabled = SCORING_VERSIONS[season]
    minutes = stats.get("minutes", 0)
    return (
        (2 if minutes >= 60 else 1 if minutes > 0 else 0)
        + (goalkeeper_goal if position == Position.GOALKEEPER else GOAL_POINTS[position])
        * stats.get("goals_scored", 0)
        + 3 * stats.get("assists", 0)
        + (CLEAN_SHEET_POINTS[position] * stats.get("clean_sheets", 0) if minutes >= 60 else 0)
        - (
            stats.get("goals_conceded", 0) // 2
            if position in (Position.GOALKEEPER, Position.DEFENDER)
            else 0
        )
        + (stats.get("saves", 0) // 3 if position == Position.GOALKEEPER else 0)
        + 5 * stats.get("penalties_saved", 0)
        - 2 * stats.get("penalties_missed", 0)
        - stats.get("yellow_cards", 0)
        - 3 * stats.get("red_cards", 0)
        - 2 * stats.get("own_goals", 0)
        + stats.get("bonus", 0)
        + (
            2
            if defensive_enabled
            and position != Position.GOALKEEPER
            and stats.get("defensive_contribution", 0)
            >= (10 if position == Position.DEFENDER else 12)
            else 0
        )
    )


def point_components(
    position: Position,
    stats: Mapping[str, int],
    *,
    season: str = CURRENT_SCORING_SEASON,
) -> dict[str, int]:
    """Additive scorer terms; regression tests reconcile every supported season."""
    goalkeeper_goal, defensive_enabled = SCORING_VERSIONS[season]
    minutes = stats.get("minutes", 0)
    return {
        "appearance": 2 if minutes >= 60 else 1 if minutes > 0 else 0,
        "goals": (goalkeeper_goal if position == Position.GOALKEEPER else GOAL_POINTS[position])
        * stats.get("goals_scored", 0),
        "assists": 3 * stats.get("assists", 0),
        "clean_sheets": CLEAN_SHEET_POINTS[position] * stats.get("clean_sheets", 0)
        if minutes >= 60
        else 0,
        "conceded": -(stats.get("goals_conceded", 0) // 2)
        if position in (Position.GOALKEEPER, Position.DEFENDER)
        else 0,
        "saves": stats.get("saves", 0) // 3 if position == Position.GOALKEEPER else 0,
        "penalties": 5 * stats.get("penalties_saved", 0) - 2 * stats.get("penalties_missed", 0),
        "cards": -stats.get("yellow_cards", 0) - 3 * stats.get("red_cards", 0),
        "own_goals": -2 * stats.get("own_goals", 0),
        "bonus": stats.get("bonus", 0),
        "defensive": 2
        if defensive_enabled
        and position != Position.GOALKEEPER
        and stats.get("defensive_contribution", 0) >= (10 if position == Position.DEFENDER else 12)
        else 0,
    }
