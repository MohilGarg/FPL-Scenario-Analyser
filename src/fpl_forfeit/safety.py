from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import LeagueState
from .settings import (
    DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION,
    DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION,
)


@dataclass(frozen=True, slots=True)
class ScoreBounds:
    lower: int
    upper: int


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    entry_id: int
    safe: bool
    bounds: ScoreBounds
    witness_entry_id: int | None = None
    reason: str = ""


def conservative_score_bounds(
    state: LeagueState,
    current_scores: Mapping[int, int],
    *,
    min_remaining_player_contribution: int = (DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION),
    max_remaining_player_contribution: int = (DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION),
) -> dict[int, ScoreBounds]:
    """Conservative bounds under the documented remaining-Gameweek envelope.

    Each owned player receives one total remaining contribution range, even in a Double Gameweek.
    Every bench player is allowed to count, so the result is intentionally wider than necessary.
    The configured values are practical pruning bounds, not literal football maxima.
    """

    result: dict[int, ScoreBounds] = {}
    for manager in state.managers:
        low_swing = 0
        high_swing = 0
        for pick in manager.picks:
            player = state.players[pick.player_id]
            if not state.unfinished_fixtures_for_team(player.team_id):
                continue
            maximum_multiplier = 1
            if pick.is_captain or pick.is_vice_captain:
                maximum_multiplier = 3 if manager.active_chip == "3xc" else 2
            low_swing += min_remaining_player_contribution * maximum_multiplier
            high_swing += max_remaining_player_contribution * maximum_multiplier
        current = current_scores[manager.entry_id]
        result[manager.entry_id] = ScoreBounds(current + low_swing, current + high_swing)
    return result


def assess_safety(
    state: LeagueState,
    current_scores: Mapping[int, int],
    *,
    min_remaining_player_contribution: int = (DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION),
    max_remaining_player_contribution: int = (DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION),
    allow_tied_last: bool = True,
) -> dict[int, SafetyAssessment]:
    bounds = conservative_score_bounds(
        state,
        current_scores,
        min_remaining_player_contribution=min_remaining_player_contribution,
        max_remaining_player_contribution=max_remaining_player_contribution,
    )
    result: dict[int, SafetyAssessment] = {}
    for manager in state.managers:
        own = bounds[manager.entry_id]

        witness = next(
            (
                opponent
                for opponent in state.managers
                if opponent.entry_id != manager.entry_id
                and (
                    bounds[opponent.entry_id].upper < own.lower
                    if allow_tied_last
                    else bounds[opponent.entry_id].upper <= own.lower
                )
            ),
            None,
        )
        if witness:
            reason = (
                f"Even at {manager.manager_name}'s modelled minimum ({own.lower}), "
                f"{witness.manager_name} cannot exceed {bounds[witness.entry_id].upper}."
            )
        else:
            reason = "No opponent is guaranteed to remain below this manager within the bounds."
        result[manager.entry_id] = SafetyAssessment(
            entry_id=manager.entry_id,
            safe=witness is not None,
            bounds=own,
            witness_entry_id=witness.entry_id if witness else None,
            reason=reason,
        )
    return result
