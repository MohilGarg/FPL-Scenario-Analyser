"""Live last-place analysis: standings, envelope safety and separating players.

Statuses are deliberately simple. During a Gameweek a manager is SAFE when, under the named
remaining-points envelope, some opponent cannot catch them; otherwise they are AT RISK
(last place cannot be ruled out). Once the Gameweek is complete only the final result is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .availability import availability_payload
from .exposures import (
    conditional_exposure_notes,
    differential_exposures,
    effective_exposures,
    remaining_effective_multipliers,
)
from .models import Fixture, LeagueState, Manager, Pick
from .safety import SafetyAssessment, assess_safety
from .settings import AnalysisSettings
from .state import CurrentStanding, current_standings
from .substitutions import effective_multipliers


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    state: LeagueState
    settings: AnalysisSettings
    standings: tuple[CurrentStanding, ...]
    safety: dict[int, SafetyAssessment]


def analyse_state(state: LeagueState, settings: AnalysisSettings | None = None) -> AnalysisResult:
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
    return AnalysisResult(state, settings, standings, safety)


def analysis_to_dict(result: AnalysisResult) -> dict[str, Any]:
    state = result.state
    settings = result.settings
    gameweek = _gameweek_payload(state)
    complete = gameweek["status"] == "complete"
    scores = {row.manager.entry_id: row.effective_score for row in result.standings}
    lowest = min(scores.values(), default=0)
    bottom_ids = [entry_id for entry_id, score in scores.items() if score == lowest]
    current_last_ids = bottom_ids if settings.allow_tied_last or len(bottom_ids) == 1 else []
    conditional = conditional_exposure_notes(state)

    managers = [
        _manager_payload(
            result,
            row.manager,
            row.effective_score,
            lowest,
            row.manager.entry_id in bottom_ids,
            row.manager.entry_id in current_last_ids,
            complete,
            conditional,
        )
        for row in result.standings
    ]
    at_risk_ids = [m["entry_id"] for m in managers if m["status"] == "at_risk"]
    safe_ids = [m["entry_id"] for m in managers if m["status"] == "safe"]

    # Only players that still move the gaps between managers who can finish last matter.
    differentials: list[dict[str, Any]] = []
    if not complete and len(at_risk_ids) >= 2:
        exposures = effective_exposures(
            state.managers, state.players, state.live_scores, state.team_complete()
        )
        focused = differential_exposures(exposures, at_risk_ids, conditional)
        remaining = [
            player_id
            for player_id in focused
            if state.unfinished_fixtures_for_team(state.players[player_id].team_id)
            or any(entry_id in conditional.get(player_id, {}) for entry_id in at_risk_ids)
        ]
        differentials = [
            _differential_payload(
                state, player_id, focused[player_id], at_risk_ids, conditional.get(player_id, {})
            )
            for player_id in sorted(
                remaining,
                key=lambda pid: (
                    -_exposure_spread(focused[pid]),
                    state.players[pid].name.casefold(),
                ),
            )
        ]

    historical = bool(state.raw.get("historical"))
    return {
        "api_version": "3",
        "league": {
            "id": state.league_id,
            "name": state.league_name,
            "gameweek": state.gameweek,
            "fetched_at": state.fetched_at.isoformat(),
            "historical": historical,
            "current_gameweek": state.raw.get("current_gameweek", state.gameweek),
            "history_note": (
                "Final results for current league members. Player team/position labels use the "
                "current season catalogue."
                if historical
                else None
            ),
            "available_gameweeks": [
                {"id": int(event["id"]), "finished": bool(event.get("finished"))}
                for event in state.raw.get("bootstrap", {}).get("events", [])
                if event.get("finished")
                or int(event["id"]) == state.raw.get("current_gameweek", state.gameweek)
            ]
            or [{"id": state.gameweek, "finished": complete}],
            **gameweek,
        },
        "summary": {
            "bottom_entry_ids": bottom_ids,
            "current_last_entry_ids": current_last_ids,
            "at_risk_entry_ids": at_risk_ids,
            "safe_entry_ids": safe_ids,
            "last_place_decided": complete or len(at_risk_ids) == 1,
            "last_rule": "tied_for_lowest_counts"
            if settings.allow_tied_last
            else "strictly_lowest",
        },
        "managers": managers,
        "differential_manager_ids": at_risk_ids if differentials else [],
        "differentials": differentials,
        "model": {
            "minimum_remaining_player_contribution": settings.min_remaining_player_contribution,
            "maximum_remaining_player_contribution": settings.max_remaining_player_contribution,
            "bounds_scope": "per player across the rest of the current Gameweek",
            "bounds_are_theoretical_maxima": False,
            "ties_count_as_last": settings.allow_tied_last,
            "safety_note": (
                f"SAFE means that even if every remaining player contributes between "
                f"{settings.min_remaining_player_contribution} and "
                f"+{settings.max_remaining_player_contribution} points over the rest of the "
                "Gameweek (including a Double Gameweek, any bench player counting, and captain "
                "multipliers), at least one opponent stays below this manager. It is a practical "
                "envelope, not a theoretical football limit. AT RISK only means last place "
                "cannot be ruled out under that envelope; it is not a probability."
            ),
        },
    }


def _exposure_spread(values: dict[int, int]) -> int:
    return max(values.values(), default=0) - min(values.values(), default=0)


def _gameweek_payload(state: LeagueState) -> dict[str, Any]:
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
    return {
        "status": status,
        "results_final": state.event_final or bool(state.raw.get("historical")),
        "fixtures": {
            "total": len(state.fixtures),
            "finished": finished_count,
            "live": live_count,
            "remaining": remaining_count,
        },
    }


def _manager_payload(
    result: AnalysisResult,
    manager: Manager,
    effective_score: int,
    lowest_score: int,
    is_bottom: bool,
    is_current_last: bool,
    complete: bool,
    conditional_notes: dict[int, dict[int, str]],
) -> dict[str, Any]:
    state = result.state
    assessment = result.safety[manager.entry_id]
    if complete:
        status = "last" if is_current_last else "clear"
    else:
        status = "safe" if assessment.safe else "at_risk"
    multipliers = remaining_effective_multipliers(
        manager, state.players, state.live_scores, state.team_complete()
    )
    scoring_multipliers = effective_multipliers(
        manager, state.players, state.live_scores, state.team_complete()
    )
    if state.raw.get("historical") or (
        state.event_final and any(pick.api_multiplier > 0 for pick in manager.picks)
    ):
        multipliers = scoring_multipliers = {
            pick.player_id: pick.api_multiplier for pick in manager.picks
        }
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
    witness = next(
        (m.manager_name for m in state.managers if m.entry_id == assessment.witness_entry_id),
        None,
    )
    return {
        "entry_id": manager.entry_id,
        "manager_name": manager.manager_name,
        "team_name": manager.team_name,
        "effective_score": effective_score,
        "official_points": official_points,
        "raw_official_points": effective_score + manager.transfer_cost,
        "score_source": "official_final"
        if state.event_final or state.raw.get("historical")
        else "live_recalculation",
        "score_notes": [
            "Effective score is counted player points minus transfer deductions.",
            "Autosubs must preserve a legal formation: 1 goalkeeper, at least 3 defenders, 2 midfielders and 1 forward.",
            "Zero-minute starters stay pending until all their Gameweek fixtures finish; vice-captain takeover also waits.",
            "A zero-minute disciplinary appearance blocks an autosub; captain handover still follows the separate no-minutes rule.",
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
        "safety_detail": None if complete else assessment.reason,
        "safety_witness": witness if assessment.safe and not complete else None,
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
    elif pick.is_starter and not score.appeared and remaining:
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
        "availability": availability_payload(player),
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
        "availability": availability_payload(player),
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
