from __future__ import annotations

from typing import Mapping, Sequence

from .exposures import differential_exposures, effective_exposures
from .models import LeagueState
from .safety import SafetyAssessment
from .solver import SearchResult, core_condition
from .state import CurrentStanding


def render_report(
    state: LeagueState,
    standings: Sequence[CurrentStanding],
    safety: Mapping[int, SafetyAssessment],
    searches: Mapping[int, SearchResult],
) -> str:
    lines = [
        f"{state.league_name} — Gameweek {state.gameweek}",
        f"Data fetched: {state.fetched_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "CURRENT EFFECTIVE SCORES (transfer costs included)",
    ]
    lowest = standings[0].effective_score if standings else 0
    for row in standings:
        suffix = "  ← CURRENTLY LAST" if row.effective_score == lowest else ""
        hit = f", -{row.manager.transfer_cost} hit" if row.manager.transfer_cost else ""
        chip = f", {row.manager.active_chip}" if row.manager.active_chip else ""
        lines.append(
            f"  {row.effective_score:>3}  {row.manager.display_name}{hit}{chip}{suffix}"
        )

    score_by_entry = {
        row.manager.entry_id: row.effective_score for row in standings
    }
    lines.extend(["", "LAST-PLACE STATUS"])
    for row in standings:
        assessment = safety[row.manager.entry_id]
        search = searches.get(row.manager.entry_id)
        if assessment.safe:
            status = "SAFE"
        elif search and search.scenarios:
            status = "CAN FINISH LAST"
        elif search and search.exhausted and not search.truncated_players:
            status = "NO MODELLED PATH"
        else:
            status = "UNRESOLVED"
        lines.append(
            f"  {status:<15} {row.manager.display_name} "
            f"[{assessment.bounds.lower}, {assessment.bounds.upper}]"
        )
        if assessment.safe:
            lines.append(f"    {assessment.reason}")

    manager_ids = [manager.entry_id for manager in state.managers]
    exposures = effective_exposures(
        state.managers, state.players, state.live_scores, state.team_complete()
    )
    differentials = differential_exposures(exposures, manager_ids)
    unfinished = {
        player_id
        for player_id in differentials
        if state.unfinished_fixtures_for_team(state.players[player_id].team_id)
    }
    if unfinished:
        lines.extend(["", "REMAINING EFFECTIVE DIFFERENTIALS"])
        for player_id in sorted(unfinished, key=lambda value: state.players[value].name):
            player = state.players[player_id]
            values = differentials[player_id]
            owners = [
                f"{next(m.manager_name for m in state.managers if m.entry_id == entry_id)} "
                f"x{multiplier}"
                for entry_id, multiplier in values.items()
                if multiplier
            ]
            lines.append(f"  {player.name}: {', '.join(owners) if owners else 'bench only'}")

    for row in standings:
        search = searches.get(row.manager.entry_id)
        if not search or not search.scenarios:
            continue
        lines.extend(
            [
                "",
                f"FOR {row.manager.manager_name.upper()} TO FINISH LAST",
                f"  Core condition: {core_condition(state, row.manager, score_by_entry)}",
            ]
        )
        for number, scenario in enumerate(search.scenarios, start=1):
            meaningful = [outcome for outcome in scenario.outcomes if not outcome.baseline]
            baseline_differentials = [
                outcome
                for outcome in scenario.outcomes
                if outcome.baseline and outcome.player_id in differentials
            ]
            shown = (meaningful + baseline_differentials)[:6]
            if shown:
                description = "; ".join(
                    f"{state.players[outcome.player_id].name} {outcome.label}"
                    for outcome in shown
                )
            else:
                description = "No special swing is needed; baseline outcomes leave them last"
            final = scenario.final_scores[row.manager.entry_id]
            other_low = min(
                value
                for entry_id, value in scenario.final_scores.items()
                if entry_id != row.manager.entry_id
            )
            lines.append(
                f"  Scenario {number} (rank {scenario.plausibility_cost:g}): "
                f"{description}. Final: {final} vs next-lowest {other_low}."
            )
        if search.truncated_players:
            lines.append(
                f"  Search note: {search.truncated_players} lower-impact player-fixture "
                "variables were outside the configured search width."
            )

    lines.extend(
        [
            "",
            "MODEL NOTE",
            "  SAFE is a proof only inside the displayed documented bounds (-8 to +20 "
            "remaining points per owned player-fixture by default). Scenario ranks are "
            "plausibility heuristics, not probabilities. UNRESOLVED never means safe.",
        ]
    )
    return "\n".join(lines)
