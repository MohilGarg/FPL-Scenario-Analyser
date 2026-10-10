from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import combinations

from .comparison import compare_managers
from .exposures import effective_exposures
from .models import ElementScore, Fixture, LeagueState, Manager
from .ranking import individual_cost, scenario_cost
from .scenarios import Outcome, ScenarioVariable, football_consistent, outcome_catalog, route_key
from .settings import MAX_COMPLETION_NODES, MAX_SCENARIO_LINEUPS
from .state import current_standings
from .substitutions import effective_multipliers, score_manager


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
    state: LeagueState,
    max_relevant: int = 8,
    *,
    include_baselines: bool = False,
    candidate: Manager | None = None,
) -> tuple[tuple[ScenarioVariable, ...], int]:
    complete = state.team_complete()
    exposures = effective_exposures(state.managers, state.players, state.live_scores, complete)
    owned = {pick.player_id for manager in state.managers for pick in manager.picks}
    manager_ids = [manager.entry_id for manager in state.managers]
    current = {r.manager.entry_id: r.effective_score for r in current_standings(state)}

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
        if candidate is not None:
            # Focus the free search dimensions on this candidate's differential exposure.
            own = exposures.get(player_id, {}).get(candidate.entry_id, 0)
            potential = max(
                (
                    abs(own - value) / (1 + abs(current[candidate.entry_id] - current[entry]) / 12)
                    for entry, value in zip(manager_ids, values)
                ),
                default=0,
            )
            if any(p.player_id == player_id for p in candidate.picks):
                potential += 0.5
        for fixture in state.unfinished_fixtures_for_team(player.team_id):
            ranked.append((potential * 100 + ownership, player_id, fixture))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2].id))
    selected = ranked if include_baselines else ranked[:max_relevant]
    variables = []
    for index, (_, player_id, fixture) in enumerate(selected):
        clock = (
            max(
                fixture.elapsed_minutes or 0,
                max(
                    (
                        minutes
                        for (pid, fid), minutes in state.fixture_minutes.items()
                        if fid == fixture.id
                    ),
                    default=0,
                ),
            )
            if fixture.started
            else 0
        )
        catalog = outcome_catalog(
            state.players[player_id],
            fixture,
            ElementScore(
                state.live_scores[player_id].points,
                state.fixture_minutes.get(
                    (player_id, fixture.id), state.live_scores[player_id].minutes
                ),
            ),
            stats=state.fixture_stats.get((player_id, fixture.id)) if fixture.started else None,
            remaining_minutes=max(0, 90 - clock) if fixture.started else None,
            off_field=(fixture.started
                       and 0 < state.fixture_minutes.get((player_id, fixture.id), 0) < clock-2
                       and not state.fixture_stats.get((player_id, fixture.id), {}).get("red_cards")),
        )
        if fixture.started:
            clock = max(
                fixture.elapsed_minutes or 0,
                max(
                    (
                        minutes
                        for (pid, fid), minutes in state.fixture_minutes.items()
                        if fid == fixture.id
                    ),
                    default=0,
                ),
            )
            available = max(0, 90 - clock)
            catalog = tuple(o for o in catalog if o.minutes_delta <= available)
            if not any(o.baseline for o in catalog):
                # A late entrant cannot reach 60; retain an explicit ordinary baseline.
                fallback = next((o for o in catalog if o.label == "leaves before 60 minutes"), None)
                if fallback:
                    catalog = (replace(fallback, baseline=True, plausibility_cost=0),) + tuple(
                        o for o in catalog if o is not fallback
                    )
        # An omitted search dimension still has an explicit baseline. It is NOT a DNP.
        outcomes = (
            (next(item for item in catalog if item.baseline),)
            if index >= max_relevant
            else tuple(sorted(catalog, key=individual_cost))
        )
        variables.append(ScenarioVariable(state.players[player_id], fixture, outcomes))
    return tuple(variables), max(0, len(ranked) - max_relevant)


def projected_player_scores(
    state: LeagueState, outcomes: Sequence[Outcome]
) -> dict[int, ElementScore]:
    points = {player_id: score.points for player_id, score in state.live_scores.items()}
    minutes = {player_id: score.minutes for player_id, score in state.live_scores.items()}
    for outcome in outcomes:
        points[outcome.player_id] += outcome.points_delta
        minutes[outcome.player_id] += outcome.minutes_delta
    return {
        player_id: ElementScore(
            points=value, minutes=minutes[player_id], carded=state.live_scores[player_id].carded
        )
        for player_id, value in points.items()
    }


def projected_scores(state: LeagueState, outcomes: Sequence[Outcome]) -> dict[int, int]:
    scores = projected_player_scores(state, outcomes)
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


@dataclass(frozen=True, slots=True)
class _LineupPlan:
    domains: tuple[tuple[Outcome, ...], ...]
    pivotal: tuple[int, ...]
    appearing: frozenset[int]
    exposures: dict[int, dict[int, int]]


def _lineup_plans(
    state: LeagueState,
    candidate: Manager,
    variables: tuple[ScenarioVariable, ...],
    allow_tied_last: bool,
) -> tuple[list[_LineupPlan], bool]:
    """Resolve legal autosubs once per presence pattern, then prune linear score gaps.

    This is only a search optimisation of projected_scores, never a separate scoring rule.
    Every returned example is still checked by that authoritative scorer.
    """
    grouped: dict[int, list[int]] = {}
    for index, variable in enumerate(variables):
        grouped.setdefault(variable.player.id, []).append(index)
    forced = {
        player_id
        for player_id, indexes in grouped.items()
        if state.live_scores[player_id].appeared
        or any(
            all(item.minutes_delta > 0 for item in variables[index].outcomes) for index in indexes
        )
    }
    optional = sorted(
        player_id
        for player_id, indexes in grouped.items()
        if player_id not in forced
        and any(item.minutes_delta > 0 for index in indexes for item in variables[index].outcomes)
    )
    complete = {player.team_id: True for player in state.players.values()}
    opponents = [manager for manager in state.managers if manager.entry_id != candidate.entry_id]
    target = 0 if allow_tied_last else -1
    plans: list[_LineupPlan] = []
    count = 0
    for size in range(len(optional) + 1):
        for absent in combinations(optional, size):
            if count >= MAX_SCENARIO_LINEUPS:
                return plans, False
            count += 1
            appearing = frozenset(forced | (set(optional) - set(absent)))
            scores = {
                player_id: ElementScore(
                    score.points, max(score.minutes, int(player_id in appearing))
                )
                for player_id, score in state.live_scores.items()
            }
            multipliers = {
                manager.entry_id: effective_multipliers(manager, state.players, scores, complete)
                for manager in state.managers
            }
            base = {
                manager.entry_id: sum(
                    state.live_scores[player_id].points * weight
                    for player_id, weight in multipliers[manager.entry_id].items()
                )
                - manager.transfer_cost
                for manager in state.managers
            }
            own = multipliers[candidate.entry_id]
            domains = [
                tuple(
                    item
                    for item in variable.outcomes
                    if (
                        item.minutes_delta == 0
                        if variable.player.id not in appearing
                        else (
                            state.live_scores[variable.player.id].appeared
                            or len(grouped[variable.player.id]) > 1
                            or item.minutes_delta > 0
                        )
                    )
                )
                for variable in variables
            ]
            gaps = [base[candidate.entry_id] - base[other.entry_id] for other in opponents]
            weights = [
                tuple(
                    own.get(v.player.id, 0) - multipliers[other.entry_id].get(v.player.id, 0)
                    for v in variables
                )
                for other in opponents
            ]
            # Bounds consistency removes outcomes that cannot satisfy even one opponent.
            # Using independent minima is conservative even when fixtures are correlated.
            changed = True
            while changed and all(domains):
                changed = False
                for gap, row in zip(gaps, weights, strict=True):
                    minima = [
                        min(weight * item.points_delta for item in domain)
                        for weight, domain in zip(row, domains, strict=True)
                    ]
                    total = gap + sum(minima)
                    if total > target:
                        domains[0] = ()
                        break
                    for index, (weight, domain) in enumerate(zip(row, domains, strict=True)):
                        allowed = tuple(
                            item
                            for item in domain
                            if total - minima[index] + weight * item.points_delta <= target
                        )
                        if len(allowed) != len(domain):
                            changed = True
                            domains[index] = allowed

            if not all(domains):
                continue
            # Only vary dimensions that can change a last-place inequality or the closest
            # baseline rival. Other players get a football-consistent completion below.
            baseline_gaps = [
                gap
                + sum(
                    weight * domain[0].points_delta
                    for weight, domain in zip(row, domains, strict=True)
                )
                for gap, row in zip(gaps, weights, strict=True)
            ]
            closest = max(baseline_gaps, default=0)
            relevant = [
                row
                for gap, row, baseline in zip(gaps, weights, baseline_gaps, strict=True)
                if baseline == closest
                or gap
                + sum(
                    max(weight * item.points_delta for item in domain)
                    for weight, domain in zip(row, domains, strict=True)
                )
                > target
            ]
            pivotal = tuple(
                index
                for index, domain in enumerate(domains)
                if len(domain) > 1 and any(row[index] for row in relevant)
            )
            exposure = {
                player_id: {
                    entry: values.get(player_id, 0) for entry, values in multipliers.items()
                }
                for player_id in grouped
            }
            plans.append(_LineupPlan(tuple(domains), pivotal, appearing, exposure))
    return plans, True


def _complete_outcomes(
    state: LeagueState,
    variables: tuple[ScenarioVariable, ...],
    plan: _LineupPlan,
    indexes: tuple[int, ...],
    budget: int,
) -> tuple[tuple[Outcome, ...] | None, int, bool]:
    chosen = {
        position: plan.domains[position][value]
        for position, value in zip(plan.pivotal, indexes, strict=True)
    }
    pending = sorted(
        (index for index in range(len(variables)) if index not in chosen),
        key=lambda index: len(plan.domains[index]),
    )
    checked = 0
    cut_short = False

    def visit(offset: int) -> tuple[Outcome, ...] | None:
        nonlocal checked, cut_short
        if checked >= budget:
            cut_short = True
            return None
        checked += 1
        if not football_consistent(tuple(chosen.values()), variables):
            return None
        if offset == len(pending):
            outcomes = tuple(chosen[index] for index in range(len(variables)))
            actual = {item.player_id for item in outcomes if item.minutes_delta > 0}
            actual.update(
                player_id for player_id in plan.appearing if state.live_scores[player_id].appeared
            )
            return outcomes if actual == set(plan.appearing) else None
        position = pending[offset]
        for item in plan.domains[position]:
            chosen[position] = item
            result = visit(offset + 1)
            if result is not None:
                return result
            if cut_short:
                break
        chosen.pop(position, None)
        return None

    return visit(0), checked, cut_short


def solve_candidate(
    state: LeagueState,
    candidate: Manager,
    *,
    limit: int = 3,
    max_relevant: int = 8,
    max_nodes: int = 50_000,
    allow_tied_last: bool = True,
) -> SearchResult:
    variables, truncated = build_variables(
        state,
        max_relevant=max_relevant,
        include_baselines=True,
        candidate=candidate,
    )
    owned = {p.player_id for m in state.managers for p in m.picks}
    if any(state.live_scores[pid].carded and state.live_scores[pid].minutes == 0 for pid in owned):
        # Presence patterns for zero-minute disciplinary appearances need distinct
        # autosub and captain-minute states. Do not claim completeness of this search.
        return SearchResult(candidate.entry_id, (), 0, truncated, False)
    if not variables:
        scores = projected_scores(state, ())
        scenarios = ()
        if _finishes_last(candidate.entry_id, scores, allow_tied_last):
            scenarios = (SolvedScenario(candidate.entry_id, (), scores, 0.0),)
        return SearchResult(candidate.entry_id, scenarios, 1, truncated, True)

    plans, all_plans = _lineup_plans(state, candidate, variables, allow_tied_last)
    pending_exposures = effective_exposures(
        state.managers, state.players, state.live_scores, state.team_complete()
    )
    heap: list[tuple[float, int, tuple[int, ...]]] = []
    seen: set[tuple[int, tuple[int, ...]]] = set()

    def enqueue(plan_index: int, indexes: tuple[int, ...]) -> None:
        key = (plan_index, indexes)
        if key in seen:
            return
        seen.add(key)
        plan = plans[plan_index]
        selected = dict(zip(plan.pivotal, indexes, strict=True))
        # Lower-cost completions are visited first; actual costs rank the final examples.
        cost = sum(
            individual_cost(domain[selected.get(index, 0)])
            for index, domain in enumerate(plan.domains)
        )
        heapq.heappush(heap, (cost, plan_index, indexes))

    for plan_index, plan in enumerate(plans):
        enqueue(plan_index, (0,) * len(plan.pivotal))
    results: list[SolvedScenario] = []
    result_signatures: dict[tuple[tuple[int, int, str], ...], int] = {}
    checked = 0

    while heap and checked < max_nodes:
        if (
            len(results) >= limit
            and heap[0][0] >= sorted(r.plausibility_cost for r in results)[limit - 1]
        ):
            break
        _, plan_index, indexes = heapq.heappop(heap)
        plan = plans[plan_index]
        outcomes, used, cut_short = _complete_outcomes(
            state,
            variables,
            plan,
            indexes,
            min(MAX_COMPLETION_NODES, max_nodes - checked),
        )
        checked += used
        all_plans = all_plans and not cut_short
        if outcomes is not None:
            scores = projected_scores(state, outcomes)
            if _finishes_last(candidate.entry_id, scores, allow_tied_last):
                signature = _diversity_signature(
                    outcomes,
                    scores,
                    candidate.entry_id,
                    plan.exposures,
                    pending_exposures,
                )
                result = SolvedScenario(
                    candidate.entry_id, outcomes, scores, scenario_cost(outcomes)
                )
                previous = result_signatures.get(signature)
                if previous is None:
                    result_signatures[signature] = len(results)
                    results.append(result)
                elif result.plausibility_cost < results[previous].plausibility_cost:
                    results[previous] = result

        for position, index in enumerate(indexes):
            if index + 1 >= len(plan.domains[plan.pivotal[position]]):
                continue
            neighbour = list(indexes)
            neighbour[position] += 1
            enqueue(plan_index, tuple(neighbour))

    return SearchResult(
        candidate_entry_id=candidate.entry_id,
        scenarios=tuple(sorted(results, key=lambda result: result.plausibility_cost)[:limit]),
        nodes_checked=checked,
        truncated_players=truncated,
        exhausted=not heap and all_plans,
    )


def _diversity_signature(
    outcomes: tuple[Outcome, ...],
    final_scores: Mapping[int, int],
    candidate_entry_id: int,
    exposures: Mapping[int, Mapping[int, int]],
    pending_exposures: Mapping[int, Mapping[int, int]] | None = None,
) -> tuple[tuple[int, int, str], ...]:
    """Describe only changes material to the candidate and the close bottom group.

    This removes near-duplicate examples that add a card or appearance variation for a manager
    well clear of the deciding scores. It does not affect whether the underlying scenario is valid.
    """

    opponent_scores = {
        entry_id: score
        for entry_id, score in final_scores.items()
        if entry_id != candidate_entry_id
    }
    next_lowest = min(opponent_scores.values(), default=final_scores[candidate_entry_id])
    relevant_entries = {candidate_entry_id} | {
        entry_id for entry_id, score in opponent_scores.items() if score == next_lowest
    }
    material = [
        outcome
        for outcome in outcomes
        if not outcome.baseline
        and (
            len(
                {
                    exposures.get(outcome.player_id, {}).get(entry_id, 0)
                    for entry_id in relevant_entries
                }
            )
            > 1
            or (
                "does not" in outcome.label
                and len(
                    {
                        (pending_exposures or {}).get(outcome.player_id, {}).get(entry_id, 0)
                        for entry_id in relevant_entries
                    }
                )
                > 1
            )
        )
    ]
    # Cards/minor minutes should not produce three copies of the same attacking route.
    primary = [outcome for outcome in material if route_key(outcome) != "appearance_or_booking"]
    return tuple(
        sorted((outcome.player_id, outcome.fixture_id, route_key(outcome)) for outcome in primary)
    )


def core_condition(
    state: LeagueState,
    candidate: Manager,
    current_scores: Mapping[int, int],
    *,
    allow_tied_last: bool = True,
) -> str:
    opponents = [manager for manager in state.managers if manager.entry_id != candidate.entry_id]
    if not opponents:
        return f"{candidate.manager_name} is the only manager in this league."
    benchmark = min(opponents, key=lambda manager: current_scores[manager.entry_id])
    comparison = compare_managers(
        state, candidate, benchmark, current_scores, allow_tied_last=allow_tied_last
    )
    return comparison["condition"] + (
        " This is a necessary condition against the current bottom opponent, not a guarantee against the whole league."
        if len(opponents) > 1
        else ""
    )
