from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from .models import ElementScore, LeagueState, Manager, Player, Position
from .substitutions import effective_multipliers


def effective_exposures(
    managers: Sequence[Manager],
    players: Mapping[int, Player],
    scores: Mapping[int, ElementScore],
    team_complete: Mapping[int, bool],
) -> dict[int, dict[int, int]]:
    """Return player -> manager entry -> effective multiplier."""

    result: dict[int, dict[int, int]] = defaultdict(dict)
    for manager in managers:
        multipliers = remaining_effective_multipliers(manager, players, scores, team_complete)
        for player_id, multiplier in multipliers.items():
            result[player_id][manager.entry_id] = multiplier
    return dict(result)


def remaining_effective_multipliers(
    manager: Manager,
    players: Mapping[int, Player],
    scores: Mapping[int, ElementScore],
    team_complete: Mapping[int, bool],
) -> dict[int, int]:
    """Resolve multipliers, including captaincy that will apply if a pending player appears."""

    multipliers = effective_multipliers(manager, players, scores, team_complete)
    captain = next((pick for pick in manager.picks if pick.is_captain), None)
    if captain:
        player = players[captain.player_id]
        if (
            multipliers[captain.player_id] > 0
            and not scores[captain.player_id].appeared
            and not team_complete.get(player.team_id, False)
        ):
            # FPL does not apply captaincy to the live score until the player appears,
            # but their remaining effective exposure is already two- or three-fold.
            multipliers[captain.player_id] = 3 if manager.active_chip == "3xc" else 2
    return multipliers


def differential_exposures(
    exposures: Mapping[int, Mapping[int, int]],
    manager_ids: Sequence[int],
    conditional: Mapping[int, Mapping[int, str]] | None = None,
) -> dict[int, dict[int, int]]:
    """Discard players whose multiplier is identical for all relevant managers."""

    result: dict[int, dict[int, int]] = {}
    for player_id, by_manager in exposures.items():
        values = {manager_id: by_manager.get(manager_id, 0) for manager_id in manager_ids}
        potential = (conditional or {}).get(player_id, {})
        if len({(values[entry_id], bool(potential.get(entry_id))) for entry_id in manager_ids}) > 1:
            result[player_id] = values
    return result


def conditional_exposure_notes(state: LeagueState) -> dict[int, dict[int, str]]:
    """Explain possible exposure changes without pretending a pending autosub is confirmed."""
    notes: dict[int, dict[int, str]] = defaultdict(dict)
    for manager in state.managers:
        if state.raw.get("historical") or not any(
            state.unfinished_fixtures_for_team(state.players[pick.player_id].team_id)
            for pick in manager.picks
        ):
            continue
        multipliers = remaining_effective_multipliers(
            manager, state.players, state.live_scores, state.team_complete()
        )
        missing = [
            pick
            for pick in manager.picks
            if pick.is_starter and not state.live_scores[pick.player_id].appeared
        ]
        for pick in manager.picks:
            player = state.players[pick.player_id]
            if (
                not pick.is_starter
                and manager.active_chip != "bboost"
                and not multipliers[pick.player_id]
                and (
                    state.live_scores[pick.player_id].appeared
                    or state.unfinished_fixtures_for_team(player.team_id)
                )
            ):
                compatible = any(
                    (state.players[absent.player_id].position == Position.GOALKEEPER)
                    == (player.position == Position.GOALKEEPER)
                    for absent in missing
                )
                if compatible:
                    notes[pick.player_id][manager.entry_id] = (
                        "Possible autosub; bench order and legal formation still apply"
                    )
            captain = next((p for p in manager.picks if p.is_captain), None)
            if (
                pick.is_vice_captain
                and captain
                and not state.live_scores[captain.player_id].appeared
                and state.unfinished_fixtures_for_team(state.players[captain.player_id].team_id)
            ):
                notes[pick.player_id][manager.entry_id] = (
                    "Possible vice-captain takeover if the captain never appears"
                )
    return dict(notes)
