"""Official hypothetical event scoring; awarded FPL totals remain the source of truth.

Rules: https://www.premierleague.com/en/news/2174909 (2026/27).
Manager multipliers, chips, hits and legal autosubs are shared with live scoring.
"""

from __future__ import annotations

from collections.abc import Mapping

from .models import ElementScore, LeagueState, Position
from .substitutions import score_manager

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


def event_points(position: Position, stats: Mapping[str, int]) -> int:
    """Score one fixture, using goals conceded while on field (also after a red).

    clean_sheets is explicit, so a subbed defender can retain a clean sheet even
    when the final team score is not nil. Minutes exclude stoppage time.
    """
    minutes = stats.get("minutes", 0)
    return (
        (2 if minutes >= 60 else 1 if minutes > 0 else 0)
        + GOAL_POINTS[position] * stats.get("goals_scored", 0)
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
            if position != Position.GOALKEEPER
            and stats.get("defensive_contribution", 0)
            >= (10 if position == Position.DEFENDER else 12)
            else 0
        )
    )


def final_manager_scores(
    state: LeagueState,
    scores: Mapping[int, ElementScore],
    *,
    multipliers: Mapping[int, Mapping[int, int]] | None = None,
) -> dict[int, int]:
    if state.event_final:
        return {
            m.entry_id: m.official_points - m.transfer_cost
            if m.official_points is not None
            else score_manager(m, state.players, scores, state.team_complete())
            for m in state.managers
        }
    complete = {p.team_id: True for p in state.players.values()}
    return {
        m.entry_id: score_manager(
            m,
            state.players,
            scores,
            complete,
            multipliers=multipliers.get(m.entry_id) if multipliers else None,
        )
        for m in state.managers
    }


def last_ids(scores: Mapping[int, float], allow_tied_last: bool = True) -> tuple[int, ...]:
    if not scores:
        return ()
    bottom = min(scores.values())
    tied = tuple(k for k, value in scores.items() if value == bottom)
    return tied if allow_tied_last or len(tied) == 1 else ()
