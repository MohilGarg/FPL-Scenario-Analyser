from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .exposures import (
    conditional_exposure_notes,
    differential_exposures,
    effective_exposures,
    remaining_effective_multipliers,
)
from .models import Fixture, LeagueState, Manager, Pick
from .safety import SafetyAssessment, assess_safety
from .settings import AnalysisSettings
from .solver import SearchResult, SolvedScenario, core_condition, solve_candidate
from .state import CurrentStanding, current_standings
from .substitutions import effective_multipliers


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    state: LeagueState
    settings: AnalysisSettings
    standings: tuple[CurrentStanding, ...]
    safety: dict[int, SafetyAssessment]
    searches: dict[int, SearchResult]


def analyse_state(
    state: LeagueState,
    settings: AnalysisSettings | None = None,
    *,
    include_scenarios: bool = True,
    candidate_id: int | None = None,
) -> AnalysisResult:
    settings = settings or AnalysisSettings()
    standings = current_standings(state)
    scores = {row.manager.entry_id: row.effective_score for row in standings}
    safety = assess_safety(
        state,
        scores,
        min_remaining_player_contribution=settings.min_remaining_player_contribution,
        max_remaining_player_contribution=settings.max_remaining_player_contribution,
        allow_tied_last=settings.allow_tied_last,
    )
    remaining_variable_count = _remaining_variable_count(state)
    broad_early_state = remaining_variable_count > settings.max_relevant_players
    complete = _gameweek_payload(state, 0)["status"] == "complete"
    searches: dict[int, SearchResult] = {}
    for row in standings:
        if (
            safety[row.manager.entry_id].safe
            or broad_early_state
            or complete
            or not include_scenarios
            or candidate_id not in (None, row.manager.entry_id)
        ):
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
    bottom_ids = [entry_id for entry_id, score in scores.items() if score == lowest]
    current_last_ids = bottom_ids if result.settings.allow_tied_last or len(bottom_ids) == 1 else []

    exposures = effective_exposures(
        state.managers, state.players, state.live_scores, state.team_complete()
    )
    all_manager_ids = [manager.entry_id for manager in state.managers]
    conditional = conditional_exposure_notes(state)
    all_differentials = differential_exposures(exposures, all_manager_ids, conditional)
    differential_ids = set(all_differentials)

    managers = [
        _manager_payload(
            result,
            row.manager,
            row.effective_score,
            lowest,
            scores,
            differential_ids,
            row.manager.entry_id in bottom_ids,
            row.manager.entry_id in current_last_ids,
            conditional,
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
    relevant_manager_ids = [item["entry_id"] for item in managers if item["status"] != "safe"]
    if len(relevant_manager_ids) < 2:
        relevant_manager_ids = all_manager_ids
    focused_differentials = differential_exposures(exposures, relevant_manager_ids, conditional)
    unfinished_differentials = [
        player_id
        for player_id in focused_differentials
        if state.unfinished_fixtures_for_team(state.players[player_id].team_id)
        or any(entry_id in conditional.get(player_id, {}) for entry_id in relevant_manager_ids)
    ]
    gameweek = _gameweek_payload(
        state,
        sum(
            bool(state.unfinished_fixtures_for_team(state.players[player_id].team_id))
            for player_id in unfinished_differentials
        ),
    )

    return {
        "api_version": "2",
        "league": {
            "id": state.league_id,
            "name": state.league_name,
            "gameweek": state.gameweek,
            "fetched_at": state.fetched_at.isoformat(),
            "historical": bool(state.raw.get("historical")),
            "current_gameweek": state.raw.get("current_gameweek", state.gameweek),
            "history_note": (
                "Final results for current league members. Historical live scenarios are not reconstructed; "
                "player team/position labels use the current season catalogue."
                if state.raw.get("historical")
                else None
            ),
            "available_gameweeks": [
                {"id": int(event["id"]), "finished": bool(event.get("finished"))}
                for event in state.raw.get("bootstrap", {}).get("events", [])
                if event.get("finished")
                or int(event["id"]) == state.raw.get("current_gameweek", state.gameweek)
            ]
            or [{"id": state.gameweek, "finished": gameweek["status"] == "complete"}],
            **gameweek,
        },
        "summary": {
            "bottom_entry_ids": bottom_ids,
            "current_last_entry_ids": current_last_ids,
            "at_risk_entry_ids": [
                item["entry_id"] for item in managers if item["status"] != "safe"
            ],
            "can_finish_last_entry_ids": can_finish_last_ids,
            "safe_entry_ids": safe_ids,
            "unresolved_entry_ids": unresolved_ids,
            "last_rule": (
                "tied_for_lowest_counts" if result.settings.allow_tied_last else "strictly_lowest"
            ),
            "scenario_search_state": (
                "broad"
                if _remaining_variable_count(result.state) > result.settings.max_relevant_players
                else "active"
            ),
        },
        "managers": managers,
        "differential_manager_ids": relevant_manager_ids,
        "differentials": [
            _differential_payload(
                state,
                player_id,
                focused_differentials[player_id],
                relevant_manager_ids,
                conditional.get(player_id, {}),
            )
            for player_id in sorted(
                unfinished_differentials,
                key=lambda value: state.players[value].name.casefold(),
            )
        ],
        "all_differentials": [
            _differential_payload(
                state, player_id, values, all_manager_ids, conditional.get(player_id, {})
            )
            for player_id, values in sorted(all_differentials.items())
            if state.unfinished_fixtures_for_team(state.players[player_id].team_id)
            or player_id in conditional
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
            "scenario_vocabulary_note": (
                "Examples are bounded, not exhaustive. Future bonus/BPS, defensive-contribution "
                "awards, every goal-concession deduction and every multi-return combination are not "
                "predicted. Official points already awarded are retained."
            ),
        },
    }


def _remaining_variable_count(state: LeagueState) -> int:
    return len(
        {
            (pick.player_id, fixture.id)
            for manager in state.managers
            for pick in manager.picks
            for fixture in state.unfinished_fixtures_for_team(state.players[pick.player_id].team_id)
        }
    )


def _gameweek_payload(state: LeagueState, differential_count: int) -> dict[str, Any]:
    live_count = sum(fixture.started and not fixture.finished for fixture in state.fixtures)
    finished_count = sum(fixture.finished for fixture in state.fixtures)
    remaining_count = sum(not fixture.finished for fixture in state.fixtures)
    event = next(
        (
            item
            for item in state.raw.get("bootstrap", {}).get("events", [])
            if int(item.get("id", 0)) == state.gameweek
        ),
        {},
    )
    if event.get("finished") or (state.fixtures and remaining_count == 0):
        status = "complete"
    elif live_count or finished_count:
        status = "in_progress"
    else:
        status = "upcoming"

    if status == "complete":
        phase = "complete"
    elif live_count:
        phase = "live"
    elif remaining_count >= 4 or differential_count >= 8:
        phase = "early"
    else:
        phase = "late"
    return {
        "status": status,
        "phase": phase,
        "fixtures": {
            "total": len(state.fixtures),
            "finished": finished_count,
            "live": live_count,
            "remaining": remaining_count,
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
    lowest_score: int,
    scores: dict[int, int],
    differential_ids: set[int],
    is_bottom: bool,
    is_current_last: bool,
    conditional_notes: dict[int, dict[int, str]],
) -> dict[str, Any]:
    state = result.state
    assessment = result.safety[manager.entry_id]
    search = result.searches.get(manager.entry_id)
    status = _status(assessment, search)
    multipliers = remaining_effective_multipliers(
        manager, state.players, state.live_scores, state.team_complete()
    )
    scoring_multipliers = effective_multipliers(
        manager, state.players, state.live_scores, state.team_complete()
    )
    if state.raw.get("historical"):
        multipliers = scoring_multipliers = {
            pick.player_id: pick.api_multiplier for pick in manager.picks
        }
    if _gameweek_payload(state, 0)["status"] == "complete":
        status = "can_finish_last" if is_current_last else "safe"
    squad = [
        _pick_payload(
            state, manager, pick, multipliers[pick.player_id], scoring_multipliers[pick.player_id]
        )
        for pick in manager.picks
    ]
    for item in squad:
        item["conditional_exposure"] = conditional_notes.get(item["player_id"], {}).get(
            manager.entry_id
        )
    remaining = [
        item
        for item in squad
        if item["remaining_fixtures"]
        and (item["effective_multiplier"] > 0 or item["autosub_status"] == "possible")
    ]
    captain = next(
        (state.players[pick.player_id].name for pick in manager.picks if pick.is_captain), None
    )
    vice = next(
        (state.players[pick.player_id].name for pick in manager.picks if pick.is_vice_captain),
        None,
    )
    official_points = (
        manager.official_points
        if manager.official_points is not None
        else effective_score + manager.transfer_cost
    )
    return {
        "entry_id": manager.entry_id,
        "manager_name": manager.manager_name,
        "team_name": manager.team_name,
        "effective_score": effective_score,
        "official_points": official_points,
        "raw_official_points": effective_score + manager.transfer_cost,
        "score_source": "official_final" if state.raw.get("historical") else "live_recalculation",
        "score_notes": [
            "Effective score is counted player points minus transfer deductions.",
            "Autosubs must preserve a legal formation: 1 goalkeeper, at least 3 defenders, 2 midfielders and 1 forward.",
            "Zero-minute starters stay pending until all their Gameweek fixtures finish; vice-captain takeover also waits.",
        ],
        "bottom_gap": effective_score - lowest_score,
        "transfer_cost": manager.transfer_cost,
        "active_chip": manager.active_chip,
        "captain": captain,
        "vice_captain": vice,
        "is_bottom": is_bottom,
        "is_current_last": is_current_last,
        "status": status,
        "remaining_player_count": len(remaining),
        "remaining_players": [item["player_name"] for item in remaining],
        "bounds": {"lower": assessment.bounds.lower, "upper": assessment.bounds.upper},
        "safety_reason": (
            "Even under the conservative remaining-points envelope, this manager cannot "
            "fall below the current bottom group."
            if assessment.safe
            else None
        ),
        "safety_detail": assessment.reason if assessment.safe else None,
        "safety_explanation": {
            "current_score": effective_score,
            "worst_remaining_change": assessment.bounds.lower - effective_score,
            "minimum_final_score": assessment.bounds.lower,
            "witness_name": next(
                (
                    m.manager_name
                    for m in state.managers
                    if m.entry_id == assessment.witness_entry_id
                ),
                None,
            ),
            "witness_maximum_score": result.safety[assessment.witness_entry_id].bounds.upper
            if assessment.witness_entry_id is not None
            else None,
        }
        if assessment.safe
        else None,
        "core_condition": core_condition(
            state, manager, scores, allow_tied_last=result.settings.allow_tied_last
        )
        if not assessment.safe
        else None,
        "scenarios": [
            _scenario_payload(state, manager, scenario, differential_ids, rank)
            for rank, scenario in enumerate(search.scenarios if search else (), start=1)
        ],
        "search": {
            "nodes_checked": search.nodes_checked if search else 0,
            "truncated_player_fixtures": search.truncated_players if search else 0,
            "exhausted": search.exhausted if search else False,
            "loaded": search is not None,
        },
        "squad": squad,
    }


def _pick_payload(
    state: LeagueState,
    manager: Manager,
    pick: Pick,
    effective_multiplier: int,
    scoring_multiplier: int,
) -> dict[str, Any]:
    player = state.players[pick.player_id]
    score = state.live_scores[pick.player_id]
    historical_points_available = not state.raw.get("historical") or any(
        int(item["id"]) == pick.player_id for item in state.raw.get("live", {}).get("elements", [])
    )
    fixtures = state.fixtures_for_team(player.team_id)
    remaining = tuple(fixture for fixture in fixtures if not fixture.finished)
    has_live = any(fixture.started and not fixture.finished for fixture in remaining)
    if has_live:
        fixture_status = "live"
    elif remaining:
        fixture_status = "not_started"
    elif fixtures:
        fixture_status = "finished"
    else:
        fixture_status = "blank"

    autosub_status: str | None = None
    if manager.active_chip == "bboost" and not pick.is_starter:
        autosub_status = "bench_boost"
    elif pick.is_starter and effective_multiplier == 0:
        autosub_status = "subbed_out" if fixture_status in {"finished", "blank"} else "pending"
    elif not pick.is_starter and effective_multiplier > 0:
        autosub_status = "subbed_in"
    elif not pick.is_starter and remaining and manager.active_chip != "bboost":
        autosub_status = "possible"
    elif pick.is_starter and score.minutes == 0 and remaining:
        autosub_status = "pending"

    return {
        "player_id": player.id,
        "player_name": player.name,
        "team_name": player.team_name,
        "position": player.position.value,
        "squad_position": pick.squad_position,
        "selection": "starter" if pick.is_starter else "bench",
        "bench_order": max(0, pick.squad_position - 11) if not pick.is_starter else None,
        "is_captain": pick.is_captain,
        "is_vice_captain": pick.is_vice_captain,
        "effective_multiplier": effective_multiplier,
        "scoring_multiplier": scoring_multiplier,
        "captaincy_status": (
            "Vice-captain takeover"
            if pick.is_vice_captain and effective_multiplier > 1
            else "Captain pending"
            if pick.is_captain and score.minutes == 0 and remaining
            else None
        ),
        "official_points": score.points if historical_points_available else None,
        "effective_points": score.points * scoring_multiplier
        if historical_points_available
        else None,
        "minutes": score.minutes if historical_points_available else None,
        "fixture_status": fixture_status,
        "remaining_fixtures": [_fixture_payload(fixture, player.team_id) for fixture in remaining],
        "autosub_status": autosub_status,
    }


def _fixture_payload(fixture: Fixture, team_id: int) -> dict[str, Any]:
    return {
        "fixture_id": fixture.id,
        "opponent": fixture.team_name(fixture.opponent_of(team_id)),
        "status": "finished" if fixture.finished else "live" if fixture.started else "not_started",
        "started": fixture.started,
        "finished": fixture.finished,
        "kickoff_time": fixture.kickoff_time.isoformat() if fixture.kickoff_time else None,
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
    shown = meaningful + baseline_differentials
    if shown:
        description = "; ".join(
            f"{state.players[outcome.player_id].name} {outcome.label}" for outcome in shown
        )
    else:
        description = "No special swing is needed; baseline outcomes leave them last"
    opponent_scores = [
        score for entry_id, score in scenario.final_scores.items() if entry_id != manager.entry_id
    ]
    bottom_scores = sorted(
        (
            {
                "entry_id": entry.entry_id,
                "manager_name": entry.manager_name,
                "score": scenario.final_scores[entry.entry_id],
            }
            for entry in state.managers
        ),
        key=lambda item: (item["score"], item["manager_name"].casefold()),
    )[:4]
    candidate_score = scenario.final_scores[manager.entry_id]
    next_lowest = min(opponent_scores, default=candidate_score)
    return {
        "rank": rank,
        "plausibility_cost": scenario.plausibility_cost,
        "plausibility": _plausibility_label(scenario.plausibility_cost),
        "description": description,
        "candidate_final_score": candidate_score,
        "next_lowest_score": next_lowest,
        "bottom_scores": bottom_scores,
        "share_text": (
            f"For {manager.manager_name} to finish last: {description}. "
            f"They would finish on {candidate_score}, "
            + (
                f"tied for lowest on {next_lowest}."
                if candidate_score == next_lowest
                else f"below the next manager on {next_lowest}."
            )
        ),
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


def _plausibility_label(cost: float) -> str:
    if cost <= 1.5:
        return "More plausible"
    if cost <= 3.5:
        return "Plausible"
    if cost <= 6.0:
        return "Unusual"
    return "Extreme"


def _differential_payload(
    state: LeagueState,
    player_id: int,
    values: dict[int, int],
    manager_ids: list[int],
    conditional: dict[int, str] | None = None,
) -> dict[str, Any]:
    player = state.players[player_id]
    managers = {manager.entry_id: manager for manager in state.managers}
    fixtures = state.unfinished_fixtures_for_team(player.team_id)
    score = state.live_scores[player_id]
    return {
        "player_id": player_id,
        "player_name": player.name,
        "position": player.position.value,
        "team_name": player.team_name,
        "current_points": score.points,
        "minutes": score.minutes,
        "fixture_status": "live"
        if any(fixture.started for fixture in fixtures)
        else "not_started"
        if fixtures
        else "finished",
        "has_double_gameweek_remaining": len(state.fixtures_for_team(player.team_id)) > 1
        and bool(fixtures),
        "fixtures": [
            _fixture_payload(fixture, player.team_id)
            for fixture in state.fixtures_for_team(player.team_id)
        ],
        "exposures": [
            {
                "entry_id": entry_id,
                "manager_name": managers[entry_id].manager_name,
                "multiplier": values.get(entry_id, 0),
                "note": (conditional or {}).get(entry_id)
                or next(
                    (
                        "Triple Captain"
                        if pick.is_captain and managers[entry_id].active_chip == "3xc"
                        else "Captain"
                        if pick.is_captain
                        else "Bench Boost"
                        if not pick.is_starter and managers[entry_id].active_chip == "bboost"
                        else "Bench / legal autosub only"
                        if not pick.is_starter
                        else "Vice-captain"
                        if pick.is_vice_captain
                        else None
                        for pick in managers[entry_id].picks
                        if pick.player_id == player_id
                    ),
                    None,
                ),
            }
            for entry_id in manager_ids
        ],
    }
