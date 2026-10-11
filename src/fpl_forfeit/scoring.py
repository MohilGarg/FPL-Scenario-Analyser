"""Official hypothetical event scoring; awarded FPL totals remain the source of truth.

Rules: https://www.premierleague.com/en/news/2174909 (2026/27).
Manager multipliers, chips, hits and legal autosubs are shared with live scoring.
"""

from __future__ import annotations

from collections.abc import Mapping

from .models import ElementScore, LeagueState
from .points import CLEAN_SHEET_POINTS, GOAL_POINTS, event_points
from .substitutions import score_manager

__all__ = ["CLEAN_SHEET_POINTS", "GOAL_POINTS", "event_points", "final_manager_scores", "last_ids"]


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
