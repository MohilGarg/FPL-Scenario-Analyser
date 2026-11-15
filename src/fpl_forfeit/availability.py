"""Display-only availability context. Never used for scoring or safety."""

from __future__ import annotations

from typing import Any

from .models import Player

UNAVAILABLE_STATUSES = frozenset({"i", "s", "u", "n"})


def regularity(player: Player) -> float | None:
    starts, minutes = player.season_starts, player.season_minutes
    if starts is None or minutes is None or starts < 0 or minutes < 0:
        return None
    if player.season_team_matches:
        # Team fixtures, not Gameweeks: handles doubles/blanks. Current-club totals can
        # mismatch after a transfer, so never pretend these are calibrated probabilities.
        rate = min(1.0, starts / player.season_team_matches)
        evidence = min(1.0, player.season_team_matches / 4)
    else:
        # Older snapshots have starts/minutes but no season fixture denominator.
        rate, evidence = min(1.0, minutes / max(1, starts * 80)), min(1.0, starts / 4)
    return round(rate * evidence, 3)


def availability_payload(player: Player) -> dict[str, Any]:
    reliability = regularity(player)
    unavailable = (
        player.availability_status in UNAVAILABLE_STATUSES or player.chance_of_playing == 0
    )
    flagged = player.chance_of_playing is not None and player.chance_of_playing < 100
    label = (
        "Flagged unavailable"
        if unavailable
        else "Availability doubt"
        if flagged or player.availability_status == "d"
        else "Regular starter"
        if reliability is not None and reliability >= 0.75
        else "Limited / mixed starting history"
        if reliability is not None
        else "Minutes history unavailable"
    )
    return {
        "season_starts": player.season_starts,
        "season_minutes": player.season_minutes,
        "team_matches_started": player.season_team_matches,
        "price_millions": player.price_tenths / 10 if player.price_tenths is not None else None,
        "status": player.availability_status,
        "news": player.availability_news or None,
        "fpl_chance_of_playing": player.chance_of_playing,
        "label": label,
        "note": "Current FPL availability and season totals; not a confirmed lineup or a probability "
        "of starting. Starts may include a previous club.",
    }
