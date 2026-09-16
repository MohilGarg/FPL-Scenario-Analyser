from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .exposures import differential_exposures, effective_exposures
from .models import LeagueState, Manager
from .safety import SafetyAssessment, assess_safety
from .settings import AnalysisSettings
from .solver import SearchResult, SolvedScenario, core_condition, solve_candidate
from .state import CurrentStanding, current_standings


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    state: LeagueState
    settings: AnalysisSettings
    standings: tuple[CurrentStanding, ...]
    safety: dict[int, SafetyAssessment]
    searches: dict[int, SearchResult]


def analyse_state(state: LeagueState, settings: AnalysisSettings | None = None) -> AnalysisResult:
    settings = settings or AnalysisSettings()
    standings = current_standings(state)
    scores = {row.manager.entry_id: row.effective_score for row in standings}
    safety = assess_safety(
        state,
        scores,
        min_remaining_player_contribution=(settings.min_remaining_player_contribution),
        max_remaining_player_contribution=(settings.max_remaining_player_contribution),
    )
    searches: dict[int, SearchResult] = {}
    for row in standings:
        if safety[row.manager.entry_id].safe:
            continue
        searches[row.manager.entry_id] = solve_candidate(
            state,
            row.manager,
            limit=settings.scenario_count,
            max_relevant=settings.max_relevant_players,
            max_nodes=settings.max_search_nodes,
            allow_tied_last=settings.allow_tied_last,
        )
    return AnalysisResult(state, settings, standings, safety, searches)


def analysis_to_dict(result: AnalysisResult) -> dict[str, Any]:
    state = result.state
    scores = {row.manager.entry_id: row.effective_score for row in result.standings}
    lowest = min(scores.values(), default=0)
    current_last_ids = [entry_id for entry_id, score in scores.items() if score == lowest]

    exposures = effective_exposures(
        state.managers, state.players, state.live_scores, state.team_complete()
    )
    manager_ids = [manager.entry_id for manager in state.managers]
    differentials = differential_exposures(exposures, manager_ids)
    unfinished_differentials = [
        player_id
        for player_id in differentials
        if state.unfinished_fixtures_for_team(state.players[player_id].team_id)
    ]

    managers = [
        _manager_payload(
            result,
            row.manager,
            row.effective_score,
            scores,
            set(differentials),
            row.manager.entry_id in current_last_ids,
        )
        for row in result.standings
    ]
    safe_ids = [item["entry_id"] for item in managers if item["status"] == "safe"]
    can_finish_last_ids = [
        item["entry_id"] for item in managers if item["status"] == "can_finish_last"
    ]
    unresolved_ids = [
        item["entry_id"]
        for item in managers
        if item["status"] in {"unresolved", "no_modelled_path"}
    ]

    return {
        "api_version": "1",
        "league": {
            "id": state.league_id,
            "name": state.league_name,
            "gameweek": state.gameweek,
            "fetched_at": state.fetched_at.isoformat(),
        },
        "summary": {
            "current_last_entry_ids": current_last_ids,
            "at_risk_entry_ids": [
                item["entry_id"] for item in managers if item["status"] != "safe"
            ],
            "can_finish_last_entry_ids": can_finish_last_ids,
            "safe_entry_ids": safe_ids,
            "unresolved_entry_ids": unresolved_ids,
        },
        "managers": managers,
        "differentials": [
            _differential_payload(state, player_id, differentials[player_id])
            for player_id in sorted(
                unfinished_differentials,
                key=lambda value: state.players[value].name.casefold(),
            )
        ],
        "model": {
            "minimum_remaining_player_contribution": (
                result.settings.min_remaining_player_contribution
            ),
            "maximum_remaining_player_contribution": (
                result.settings.max_remaining_player_contribution
            ),
            "bounds_scope": "per player across the rest of the current Gameweek",
            "bounds_are_theoretical_maxima": False,
            "ties_count_as_last": result.settings.allow_tied_last,
            "scenario_rank_is_probability": False,
        },
    }


def _status(assessment: SafetyAssessment, search: SearchResult | None) -> str:
    if assessment.safe:
        return "safe"
    if search and search.scenarios:
        return "can_finish_last"
    if search and search.exhausted and not search.truncated_players:
        return "no_modelled_path"
    return "unresolved"


def _manager_payload(
    result: AnalysisResult,
    manager: Manager,
    effective_score: int,
    scores: dict[int, int],
    differential_ids: set[int],
    is_current_last: bool,
) -> dict[str, Any]:
    assessment = result.safety[manager.entry_id]
    search = result.searches.get(manager.entry_id)
    status = _status(assessment, search)
    return {
        "entry_id": manager.entry_id,
        "manager_name": manager.manager_name,
        "team_name": manager.team_name,
        "effective_score": effective_score,
        "transfer_cost": manager.transfer_cost,
        "active_chip": manager.active_chip,
        "is_current_last": is_current_last,
        "status": status,
        "bounds": {
            "lower": assessment.bounds.lower,
            "upper": assessment.bounds.upper,
        },
        "safety_reason": assessment.reason if assessment.safe else None,
        "core_condition": (
            core_condition(result.state, manager, scores) if not assessment.safe else None
        ),
        "scenarios": [
            _scenario_payload(result.state, manager, scenario, differential_ids, rank)
            for rank, scenario in enumerate(search.scenarios if search else (), start=1)
        ],
        "search": {
            "nodes_checked": search.nodes_checked if search else 0,
            "truncated_player_fixtures": search.truncated_players if search else 0,
            "exhausted": search.exhausted if search else True,
        },
    }


def _scenario_payload(
    state: LeagueState,
    manager: Manager,
    scenario: SolvedScenario,
    differential_ids: set[int],
    rank: int,
) -> dict[str, Any]:
    meaningful = [outcome for outcome in scenario.outcomes if not outcome.baseline]
    baseline_differentials = [
        outcome
        for outcome in scenario.outcomes
        if outcome.baseline and outcome.player_id in differential_ids
    ]
    shown = (meaningful + baseline_differentials)[:6]
    if shown:
        description = "; ".join(
            f"{state.players[outcome.player_id].name} {outcome.label}" for outcome in shown
        )
    else:
        description = "No special swing is needed; baseline outcomes leave them last"
    opponent_scores = [
        score for entry_id, score in scenario.final_scores.items() if entry_id != manager.entry_id
    ]
    return {
        "rank": rank,
        "plausibility_cost": scenario.plausibility_cost,
        "description": description,
        "candidate_final_score": scenario.final_scores[manager.entry_id],
        "next_lowest_score": min(opponent_scores),
        "events": [
            {
                "player_id": outcome.player_id,
                "player_name": state.players[outcome.player_id].name,
                "fixture_id": outcome.fixture_id,
                "description": outcome.label,
                "points_delta": outcome.points_delta,
                "minutes_delta": outcome.minutes_delta,
                "baseline": outcome.baseline,
            }
            for outcome in scenario.outcomes
        ],
    }


def _differential_payload(
    state: LeagueState, player_id: int, values: dict[int, int]
) -> dict[str, Any]:
    player = state.players[player_id]
    managers = {manager.entry_id: manager for manager in state.managers}
    fixtures = state.unfinished_fixtures_for_team(player.team_id)
    return {
        "player_id": player_id,
        "player_name": player.name,
        "position": player.position.value,
        "team_name": player.team_name,
        "fixtures": [
            {
                "fixture_id": fixture.id,
                "opponent": fixture.team_name(fixture.opponent_of(player.team_id)),
                "started": fixture.started,
                "finished": fixture.finished,
            }
            for fixture in fixtures
        ],
        "exposures": [
            {
                "entry_id": entry_id,
                "manager_name": managers[entry_id].manager_name,
                "multiplier": multiplier,
            }
            for entry_id, multiplier in values.items()
            if multiplier
        ],
    }
