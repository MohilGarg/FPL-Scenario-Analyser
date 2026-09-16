"""Pairwise explanations, shared by the API and the solver's core-condition text."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .exposures import effective_exposures
from .models import LeagueState, Manager


def compare_managers(
    state: LeagueState,
    a: Manager,
    b: Manager,
    scores: Mapping[int, int],
    *,
    allow_tied_last: bool = True,
) -> dict[str, Any]:
    if a.entry_id == b.entry_id:
        raise ValueError("Choose two different managers")
    exposures = effective_exposures((a, b), state.players, state.live_scores, state.team_complete())
    gap = scores[a.entry_id] - scores[b.entry_id]
    # Let D be B's remaining effective contribution minus A's. A <= B iff D >= gap.
    required = gap + (0 if allow_tied_last else 1)
    shared, differentials = [], []
    for player_id, values in exposures.items():
        player = state.players[player_id]
        if not state.unfinished_fixtures_for_team(player.team_id):
            continue
        am, bm = values.get(a.entry_id, 0), values.get(b.entry_id, 0)
        item = {
            "player_id": player_id,
            "player_name": player.name,
            "a_multiplier": am,
            "b_multiplier": bm,
            "relative_multiplier": bm - am,
        }
        if am != bm:
            differentials.append(item)
        elif am:
            shared.append(item)
    conditional = any(
        pick.is_starter
        and state.live_scores[pick.player_id].minutes == 0
        and state.unfinished_fixtures_for_team(state.players[pick.player_id].team_id)
        for manager in (a, b)
        for pick in manager.picks
    )
    relation = "at or below" if allow_tied_last else "below"
    if gap < 0:
        position = f"{a.manager_name} trails {b.manager_name} by {-gap} effective points."
    elif gap > 0:
        position = f"{a.manager_name} leads {b.manager_name} by {gap} effective points."
    else:
        position = f"{a.manager_name} and {b.manager_name} are level on effective points."
    favourable = [item for item in differentials if item["relative_multiplier"] > 0]
    adverse = [item for item in differentials if item["relative_multiplier"] < 0]
    if (
        required > 0
        and len(favourable) == len(adverse) == 1
        and favourable[0]["relative_multiplier"] == -adverse[0]["relative_multiplier"] == 1
    ):
        condition = (
            f"{favourable[0]['player_name']} must outscore {adverse[0]['player_name']} "
            f"by at least {required} remaining points for {a.manager_name} to finish "
            f"{relation} {b.manager_name}."
        )
    elif differentials:
        if required >= 0:
            condition = (
                f"{b.manager_name}'s remaining effective points must exceed "
                f"{a.manager_name}'s by at least {required} for {a.manager_name} "
                f"to finish {relation} {b.manager_name}."
            )
        else:
            condition = (
                f"{a.manager_name} can gain at most {-required} effective points on "
                f"{b.manager_name} from here and still finish {relation} them."
            )
    else:
        condition = "There is no remaining fixed-exposure differential between these managers."
    if conditional:
        condition += " This assumes the pending starters play; autosubs or vice-captain takeover can change the condition."
    return {
        "a": {"entry_id": a.entry_id, "name": a.manager_name, "score": scores[a.entry_id]},
        "b": {"entry_id": b.entry_id, "name": b.manager_name, "score": scores[b.entry_id]},
        "gap": gap,
        "required_relative_points": required,
        "ties_count_as_last": allow_tied_last,
        "position": position,
        "condition": condition,
        "conditional_exposure": conditional,
        "shared_players": shared,
        "differentials": differentials,
        "scope_note": "This compares two managers only; finishing last requires the condition against every opponent.",
    }
