from __future__ import annotations

from collections.abc import Mapping, Sequence

from .exposures import differential_exposures, effective_exposures
from .models import LeagueState
from .safety import SafetyAssessment
from .settings import (
    DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION,
    DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION,
)
from .state import CurrentStanding


def render_report(
    state: LeagueState,
    standings: Sequence[CurrentStanding],
    safety: Mapping[int, SafetyAssessment],
    *,
    min_remaining_player_contribution: int = (DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION),
    max_remaining_player_contribution: int = (DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION),
    allow_tied_last: bool = True,
) -> str:
    lines = [
        f"{state.league_name} — Gameweek {state.gameweek}",
        f"Data fetched: {state.fetched_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "CURRENT EFFECTIVE SCORES (transfer costs included)",
    ]
    lowest = standings[0].effective_score if standings else 0
    bottom_count = sum(row.effective_score == lowest for row in standings)
    complete = bool(state.fixtures) and all(fixture.finished for fixture in state.fixtures)
    for row in standings:
        suffix = ""
        if row.effective_score == lowest:
            suffix = (
                "  ← BOTTOM TIE (no sole loser)"
                if bottom_count > 1 and not allow_tied_last
                else "  ← FINISHED LAST"
                if complete
                else "  ← CURRENTLY LAST"
            )
        hit = f", -{row.manager.transfer_cost} hit" if row.manager.transfer_cost else ""
        chip = f", {row.manager.active_chip}" if row.manager.active_chip else ""
        lines.append(f"  {row.effective_score:>3}  {row.manager.display_name}{hit}{chip}{suffix}")

    lines.extend(["", "LAST-PLACE STATUS"])
    for row in standings:
        assessment = safety[row.manager.entry_id]
        is_last = row.effective_score == lowest and (allow_tied_last or bottom_count == 1)
        if complete:
            status = "FINISHED LAST" if is_last else "CLEAR"
        elif assessment.safe:
            status = "SAFE"
        else:
            status = "AT RISK"
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

    lines.extend(
        [
            "",
            "MODEL NOTE",
            (
                f"  SAFE uses the practical {min_remaining_player_contribution:+d} to "
                f"{max_remaining_player_contribution:+d} bound for each player's total remaining "
                "Gameweek contribution (including a Double Gameweek). These are not literal "
                "theoretical maxima. AT RISK only means last place cannot be ruled out "
                "under that envelope; it is not a probability."
            ),
        ]
    )
    return "\n".join(lines)
