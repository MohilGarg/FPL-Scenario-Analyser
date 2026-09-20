"""Transparent minutes-context heuristics for ranking examples, never scoring or pruning."""

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


def minutes_cost(player: Player, minutes: int) -> float:
    """Cost adjustment, NOT a chance of starting. Price is a small tie-breaker only."""
    unavailable = (
        player.availability_status in UNAVAILABLE_STATUSES or player.chance_of_playing == 0
    )
    if unavailable:
        return 6.0 if minutes else -1.2
    if player.chance_of_playing is not None and player.chance_of_playing < 100:
        concern = 1 - max(0, player.chance_of_playing) / 100
        return 3.0 * concern if minutes else -concern
    evidence = regularity(player)
    if evidence is None:
        return 0.0  # Missing data is not evidence of reliability or absence.
    # The weak price signal cannot turn a cheap regular into a rotation risk, or an
    # expensive bench player into a regular; it only supplements strong minutes evidence.
    price_signal = min(0.3, max(0, (player.price_tenths or 40) - 40) / 250) * evidence
    if minutes == 0:
        return 5.5 * evidence + price_signal
    if minutes < 60:
        return 1.8 * evidence + price_signal
    return (
        1.5 * (1 - evidence)
        if player.season_team_matches and player.season_team_matches >= 4
        else 0.0
    )


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
        "note": "Current FPL availability and season totals; not a confirmed lineup or a probability of starting. "
        "Starts may include a previous club. Price is only a weak ranking tie-breaker. "
        "No appearance means zero minutes, not merely starting on the bench.",
    }
