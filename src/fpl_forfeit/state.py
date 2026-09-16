from __future__ import annotations

from dataclasses import dataclass

from .models import LeagueState, Manager
from .substitutions import score_manager


@dataclass(frozen=True, slots=True)
class CurrentStanding:
    manager: Manager
    effective_score: int


def current_standings(state: LeagueState) -> tuple[CurrentStanding, ...]:
    complete = state.team_complete()
    standings = [
        CurrentStanding(
            manager=manager,
            effective_score=(
                manager.official_points - manager.transfer_cost
                if state.raw.get("historical") and manager.official_points is not None
                else score_manager(manager, state.players, state.live_scores, complete)
            ),
        )
        for manager in state.managers
    ]
    return tuple(
        sorted(standings, key=lambda row: (row.effective_score, row.manager.manager_name.lower()))
    )


def remaining_players(state: LeagueState) -> set[int]:
    owned = {pick.player_id for manager in state.managers for pick in manager.picks}
    return {
        player_id
        for player_id in owned
        if state.unfinished_fixtures_for_team(state.players[player_id].team_id)
    }
