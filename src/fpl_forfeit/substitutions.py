from __future__ import annotations

from itertools import combinations, permutations
from typing import Mapping

from .models import ElementScore, Manager, Pick, Player, Position


def _valid_formation(positions: list[Position]) -> bool:
    return (
        positions.count(Position.GOALKEEPER) == 1
        and 3 <= positions.count(Position.DEFENDER) <= 5
        and 2 <= positions.count(Position.MIDFIELDER) <= 5
        and 1 <= positions.count(Position.FORWARD) <= 3
    )


def _confirmed_absent(
    pick: Pick,
    players: Mapping[int, Player],
    scores: Mapping[int, ElementScore],
    team_complete: Mapping[int, bool],
) -> bool:
    player = players[pick.player_id]
    return scores[pick.player_id].minutes == 0 and team_complete.get(player.team_id, False)


def _outfield_substitution_plan(
    starters: list[Pick],
    bench: list[Pick],
    players: Mapping[int, Player],
    scores: Mapping[int, ElementScore],
    team_complete: Mapping[int, bool],
) -> dict[int, int]:
    missing = [
        pick
        for pick in starters
        if players[pick.player_id].position != Position.GOALKEEPER
        and _confirmed_absent(pick, players, scores, team_complete)
    ]
    available = [
        pick
        for pick in bench
        if players[pick.player_id].position != Position.GOALKEEPER
        and scores[pick.player_id].appeared
    ]
    if not missing or not available:
        return {}

    starting_positions = [players[pick.player_id].position for pick in starters]
    best: tuple[tuple[int, tuple[int, ...]], dict[int, int]] | None = None
    max_size = min(len(missing), len(available))
    for size in range(max_size + 1):
        for chosen in combinations(range(len(available)), size):
            for replaced in combinations(missing, size):
                for ordered_replaced in permutations(replaced):
                    positions = list(starting_positions)
                    plan: dict[int, int] = {}
                    for bench_index, absent in zip(chosen, ordered_replaced, strict=True):
                        positions.remove(players[absent.player_id].position)
                        substitute = available[bench_index]
                        positions.append(players[substitute.player_id].position)
                        plan[absent.player_id] = substitute.player_id
                    if not _valid_formation(positions):
                        continue
                    # More substitutions first, then earlier bench slots lexicographically.
                    priority = (size, tuple(-index for index in chosen))
                    if best is None or priority > best[0]:
                        best = (priority, plan)
    return best[1] if best else {}


def effective_multipliers(
    manager: Manager,
    players: Mapping[int, Player],
    scores: Mapping[int, ElementScore],
    team_complete: Mapping[int, bool],
) -> dict[int, int]:
    """Resolve provisional/final FPL multipliers from the original 15 picks.

    A zero-minute player is absent only after all of their team's Gameweek fixtures are
    complete. This prevents an early autosub or vice-captain handover while they can still play.
    """

    picks = sorted(manager.picks, key=lambda pick: pick.squad_position)
    starters = [pick for pick in picks if pick.is_starter]
    bench = [pick for pick in picks if not pick.is_starter]
    multipliers = {pick.player_id: 0 for pick in picks}

    if manager.active_chip == "bboost":
        multipliers.update({pick.player_id: 1 for pick in picks})
    else:
        multipliers.update({pick.player_id: 1 for pick in starters})

        starting_goalkeeper = next(
            pick for pick in starters if players[pick.player_id].position == Position.GOALKEEPER
        )
        bench_goalkeeper = next(
            pick for pick in bench if players[pick.player_id].position == Position.GOALKEEPER
        )
        if _confirmed_absent(starting_goalkeeper, players, scores, team_complete) and scores[
            bench_goalkeeper.player_id
        ].appeared:
            multipliers[starting_goalkeeper.player_id] = 0
            multipliers[bench_goalkeeper.player_id] = 1

        plan = _outfield_substitution_plan(
            starters, bench, players, scores, team_complete
        )
        for absent_id, substitute_id in plan.items():
            multipliers[absent_id] = 0
            multipliers[substitute_id] = 1

    captain = next((pick for pick in picks if pick.is_captain), None)
    vice = next((pick for pick in picks if pick.is_vice_captain), None)
    captain_multiplier = 3 if manager.active_chip == "3xc" else 2
    if captain and scores[captain.player_id].appeared:
        if multipliers[captain.player_id] > 0:
            multipliers[captain.player_id] = captain_multiplier
    elif captain and _confirmed_absent(captain, players, scores, team_complete):
        if vice and scores[vice.player_id].appeared and multipliers[vice.player_id] > 0:
            multipliers[vice.player_id] = captain_multiplier

    return multipliers


def score_manager(
    manager: Manager,
    players: Mapping[int, Player],
    scores: Mapping[int, ElementScore],
    team_complete: Mapping[int, bool],
) -> int:
    multipliers = effective_multipliers(manager, players, scores, team_complete)
    gross = sum(scores[player_id].points * multiplier for player_id, multiplier in multipliers.items())
    return gross - manager.transfer_cost
