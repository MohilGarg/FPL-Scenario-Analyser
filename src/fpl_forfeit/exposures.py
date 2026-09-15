from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

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
        multipliers = effective_multipliers(manager, players, scores, team_complete)
        for player_id, multiplier in multipliers.items():
            result[player_id][manager.entry_id] = multiplier
    return dict(result)


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

