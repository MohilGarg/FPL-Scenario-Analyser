from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from .models import ElementScore, Manager, Player
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
    exposures: Mapping[int, Mapping[int, int]], manager_ids: Sequence[int]
) -> dict[int, dict[int, int]]:
    """Discard players whose multiplier is identical for all relevant managers."""

    result: dict[int, dict[int, int]] = {}
    for player_id, by_manager in exposures.items():
        values = {manager_id: by_manager.get(manager_id, 0) for manager_id in manager_ids}
        if len(set(values.values())) > 1:
            result[player_id] = values
    return result
