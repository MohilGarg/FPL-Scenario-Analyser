from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import LeagueState
from .settings import (
    DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION,
    DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION,
)
from .substitutions import effective_multipliers


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
        current_multipliers = effective_multipliers(
            manager, state.players, state.live_scores, state.team_complete()
        )
        pending_lineup = any(
            pick.is_starter
            and not state.live_scores[pick.player_id].appeared
            and state.unfinished_fixtures_for_team(state.players[pick.player_id].team_id)
            for pick in manager.picks
        )
        for pick in manager.picks:
            player = state.players[pick.player_id]
            maximum_multiplier = 1
            if pick.is_captain or pick.is_vice_captain:
                maximum_multiplier = 3 if manager.active_chip == "3xc" else 2
            if state.unfinished_fixtures_for_team(player.team_id):
                low_swing += min_remaining_player_contribution * maximum_multiplier
                high_swing += max_remaining_player_contribution * maximum_multiplier
            # A future absence may bring in points ALREADY scored on the bench, or transfer
            # captaincy to a vice whose fixtures are complete. Those are not future player
            # returns, so account for the multiplier change separately from the -10/+35 range.
            if pending_lineup and (not pick.is_starter or pick.is_captain or pick.is_vice_captain):
                points = state.live_scores[pick.player_id].points
                current = current_multipliers[pick.player_id]
                adjustments = [
                    (multiplier - current) * points for multiplier in (0, maximum_multiplier)
                ]
                low_swing += min(0, *adjustments)
                high_swing += max(0, *adjustments)
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
