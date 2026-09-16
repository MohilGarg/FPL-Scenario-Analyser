from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .exposures import effective_exposures
from .models import ElementScore, Fixture, LeagueState, Manager
from .ranking import scenario_cost
from .scenarios import Outcome, ScenarioVariable, football_consistent, outcome_catalog
from .substitutions import score_manager


@dataclass(frozen=True, slots=True)
class SolvedScenario:
    candidate_entry_id: int
    outcomes: tuple[Outcome, ...]
    final_scores: dict[int, int]
    plausibility_cost: float


@dataclass(frozen=True, slots=True)
class SearchResult:
    candidate_entry_id: int
    scenarios: tuple[SolvedScenario, ...]
    nodes_checked: int
    truncated_players: int
    exhausted: bool


def build_variables(
    state: LeagueState, max_relevant: int = 8
) -> tuple[tuple[ScenarioVariable, ...], int]:
    complete = state.team_complete()
    exposures = effective_exposures(state.managers, state.players, state.live_scores, complete)
    owned = {pick.player_id for manager in state.managers for pick in manager.picks}
    manager_ids = [manager.entry_id for manager in state.managers]

    ranked: list[tuple[float, int, Fixture]] = []
    for player_id in owned:
        player = state.players[player_id]
        values = [exposures.get(player_id, {}).get(entry_id, 0) for entry_id in manager_ids]
        spread = max(values, default=0) - min(values, default=0)
        ownership = sum(
            1 for manager in state.managers if any(p.player_id == player_id for p in manager.picks)
        )
        # A bench player may become an autosub despite a current multiplier of zero.
        potential = max(spread, 0.25 if ownership else 0.0)
        for fixture in state.unfinished_fixtures_for_team(player.team_id):
            ranked.append((potential * 100 + ownership, player_id, fixture))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2].id))
    selected = ranked[:max_relevant]
    variables = tuple(
        ScenarioVariable(
            player=state.players[player_id],
            fixture=fixture,
            outcomes=outcome_catalog(
                state.players[player_id],
                fixture,
                ElementScore(
                    points=state.live_scores[player_id].points,
                    minutes=state.fixture_minutes.get(
                        (player_id, fixture.id),
                        state.live_scores[player_id].minutes,
                    ),
                ),
            ),
        )
        for _, player_id, fixture in selected
    )
    return variables, max(0, len(ranked) - len(selected))


def projected_scores(state: LeagueState, outcomes: Sequence[Outcome]) -> dict[int, int]:
    points = {player_id: score.points for player_id, score in state.live_scores.items()}
    minutes = {player_id: score.minutes for player_id, score in state.live_scores.items()}
    for outcome in outcomes:
        points[outcome.player_id] += outcome.points_delta
        minutes[outcome.player_id] += outcome.minutes_delta
    scores = {
        player_id: ElementScore(points=value, minutes=minutes[player_id])
        for player_id, value in points.items()
    }
    all_complete = {player.team_id: True for player in state.players.values()}
    return {
        manager.entry_id: score_manager(manager, state.players, scores, all_complete)
        for manager in state.managers
    }


def _finishes_last(entry_id: int, final_scores: Mapping[int, int], allow_tied_last: bool) -> bool:
    own = final_scores[entry_id]
    opponents = [score for other, score in final_scores.items() if other != entry_id]
    if not opponents:
        return True
    return own <= min(opponents) if allow_tied_last else own < min(opponents)


def solve_candidate(
    state: LeagueState,
    candidate: Manager,
    *,
    limit: int = 3,
    max_relevant: int = 8,
    max_nodes: int = 50_000,
    allow_tied_last: bool = True,
) -> SearchResult:
    variables, truncated = build_variables(state, max_relevant=max_relevant)
    if not variables:
        scores = projected_scores(state, ())
        scenarios = ()
        if _finishes_last(candidate.entry_id, scores, allow_tied_last):
            scenarios = (SolvedScenario(candidate.entry_id, (), scores, 0.0),)
        return SearchResult(candidate.entry_id, scenarios, 1, truncated, True)

    start = tuple(0 for _ in variables)
    heap: list[tuple[float, tuple[int, ...]]] = [(0.0, start)]
    seen = {start}
    results: list[SolvedScenario] = []
    result_signatures: set[tuple[tuple[int, str], ...]] = set()
    checked = 0

    while heap and checked < max_nodes and len(results) < limit:
        _, indexes = heapq.heappop(heap)
        outcomes = tuple(
            variable.outcomes[index] for variable, index in zip(variables, indexes, strict=True)
        )
        checked += 1
        if football_consistent(outcomes, variables):
            scores = projected_scores(state, outcomes)
            if _finishes_last(candidate.entry_id, scores, allow_tied_last):
                signature = tuple(
                    sorted(
                        (outcome.player_id, outcome.label)
                        for outcome in outcomes
                        if not outcome.baseline
                    )
                )
                if signature not in result_signatures:
                    result_signatures.add(signature)
                    results.append(
                        SolvedScenario(
                            candidate_entry_id=candidate.entry_id,
                            outcomes=outcomes,
                            final_scores=scores,
                            plausibility_cost=scenario_cost(outcomes),
                        )
                    )

        for position, index in enumerate(indexes):
            if index + 1 >= len(variables[position].outcomes):
                continue
            neighbour = list(indexes)
            neighbour[position] += 1
            key = tuple(neighbour)
            if key in seen:
                continue
            seen.add(key)
            cost = sum(
                variable.outcomes[value].plausibility_cost
                for variable, value in zip(variables, key, strict=True)
            )
            heapq.heappush(heap, (cost, key))

    return SearchResult(
        candidate_entry_id=candidate.entry_id,
        scenarios=tuple(sorted(results, key=lambda result: result.plausibility_cost)),
        nodes_checked=checked,
        truncated_players=truncated,
        exhausted=not heap,
    )


def core_condition(
    state: LeagueState,
    candidate: Manager,
    current_scores: Mapping[int, int],
) -> str:
    opponents = [manager for manager in state.managers if manager.entry_id != candidate.entry_id]
    if not opponents:
        return f"{candidate.manager_name} is the only manager in this league."
    benchmark = min(opponents, key=lambda manager: current_scores[manager.entry_id])
    gap = current_scores[candidate.entry_id] - current_scores[benchmark.entry_id]
    exposures = effective_exposures(
        state.managers, state.players, state.live_scores, state.team_complete()
    )
    positive: list[tuple[int, str]] = []
    negative: list[tuple[int, str]] = []
    for player_id, by_manager in exposures.items():
        if not state.unfinished_fixtures_for_team(state.players[player_id].team_id):
            continue
        differential = by_manager.get(benchmark.entry_id, 0) - by_manager.get(candidate.entry_id, 0)
        if differential > 0:
            positive.append((differential, state.players[player_id].name))
        elif differential < 0:
            negative.append((-differential, state.players[player_id].name))
    positive.sort(reverse=True)
    negative.sort(reverse=True)
    required = max(0, gap)
    if gap <= 0:
        if negative:
            adverse = ", ".join(f"{name} x{weight}" for weight, name in negative[:3])
            return (
                f"{candidate.manager_name} is currently {-gap} point"
                f"{'s' if gap != -1 else ''} below {benchmark.manager_name}; "
                f"the main threat to that margin is extra exposure to {adverse}."
            )
        return (
            f"{candidate.manager_name} is already {-gap} point"
            f"{'s' if gap != -1 else ''} below {benchmark.manager_name}."
        )
    if len(positive) == 1 and len(negative) == 1 and positive[0][0] == negative[0][0] == 1:
        return (
            f"{positive[0][1]} must outscore {negative[0][1]} by at least "
            f"{required} point{'s' if required != 1 else ''} for {candidate.manager_name} "
            f"to catch {benchmark.manager_name}."
        )
    if not positive and not negative:
        return (
            f"No current multiplier differential separates {candidate.manager_name} from "
            f"{benchmark.manager_name}; conditional autosubs or another opponent decide it."
        )
    parts: list[str] = []
    if positive:
        parts.append(
            "favourable exposure to "
            + ", ".join(f"{name} x{weight}" for weight, name in positive[:3])
        )
    if negative:
        parts.append("offset by " + ", ".join(f"{name} x{weight}" for weight, name in negative[:3]))
    return (
        f"{candidate.manager_name} needs a net swing of at least {required} point"
        f"{'s' if required != 1 else ''} versus {benchmark.manager_name}: " + "; ".join(parts) + "."
    )
