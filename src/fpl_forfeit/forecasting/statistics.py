"""Transparent season-based player/team estimates with explicit sparse-data priors."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..models import Player, Position
from .models import TeamMatchResult


def estimate_team_goal_rates(
    results: Iterable[TeamMatchResult],
) -> dict[int, tuple[float, float]]:
    """Shrink completed-match scoring/conceding towards the league mean by eight games."""
    played = tuple(results)
    if not played:
        return {}
    rate = sum(f.home_goals + f.away_goals for f in played) / (2 * len(played))
    totals: dict[int, list[int]] = {}
    for f in played:
        for team, own, against in (
            (f.home_team_id, f.home_goals, f.away_goals),
            (f.away_team_id, f.away_goals, f.home_goals),
        ):
            row = totals.setdefault(team, [0, 0, 0])
            row[0] += 1
            row[1] += own
            row[2] += against
    return {
        team: ((own + 8 * rate) / (n + 8), (against + 8 * rate) / (n + 8))
        for team, (n, own, against) in totals.items()
    }


def starting_minutes(player: Player) -> tuple[int, ...]:
    """Shrink a season-based starter-duration proxy towards the existing prior.

    Season minutes include cameos, so subtract three expected cameo minutes per
    non-starting team match. This is an explicit proxy, not observed start durations.
    Four prior starts retain variation when evidence is sparse. Goalkeepers keep
    their full-match prior because season totals cannot identify injury durations.
    """
    prior = (90, 90, 90, 80, 70, 60, 55)
    if player.position == Position.GOALKEEPER:
        return (90,)
    starts, minutes = player.season_starts, player.season_minutes
    if not starts or minutes is None:
        return prior
    nonstarts = max(0, (player.season_team_matches or starts) - starts)
    duration = min(90, max(1, (minutes - 3 * nonstarts) / starts))
    return tuple(
        max(1, min(90, round((4 * value + starts * duration) / (4 + starts)))) for value in prior
    )


def team_goal_rate(
    rates: Mapping[int, tuple[float, float]],
    team: int,
    opponent: int,
    home: bool,
) -> float:
    own = rates.get(team, (1.4, 1.4))[0]
    against = rates.get(opponent, (1.4, 1.4))[1]
    return min(4.0, max(0.25, (own + against) / 2 * (1.1 if home else 0.9)))


@dataclass(frozen=True, slots=True)
class PlayerRates:
    start: float
    goal: float
    assist: float
    defensive: float
    saves: float
    yellow: float
    starting_minutes: tuple[int, ...]


def poisson(rng: random.Random, rate: float) -> int:
    threshold = math.exp(-max(0, rate))
    product, count = 1.0, 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return max(0, count - 1)


def player_rates(player: Player) -> PlayerRates:
    # Shrink observed rates towards explicit priors; use priors alone for missing totals.
    goal = {
        Position.GOALKEEPER: 0.003,
        Position.DEFENDER: 0.06,
        Position.MIDFIELDER: 0.20,
        Position.FORWARD: 0.35,
    }[player.position]
    assist = {
        Position.GOALKEEPER: 0.005,
        Position.DEFENDER: 0.07,
        Position.MIDFIELDER: 0.15,
        Position.FORWARD: 0.10,
    }[player.position]
    minutes = player.season_minutes or 0

    def rate(total: int | None, prior: float) -> float:
        return prior if total is None else (total + 5 * prior) / (minutes / 90 + 5)

    start = 0.8
    if player.season_team_matches is not None and player.season_starts is not None:
        start = (player.season_starts + 4 * 0.8) / (player.season_team_matches + 4)
    if player.availability_status in {"i", "s", "u", "n"}:
        start *= (player.chance_of_playing or 0) / 100
    elif player.chance_of_playing is not None:
        start *= player.chance_of_playing / 100
    return PlayerRates(
        min(0.98, max(0, start)),
        rate(player.season_goals, goal),
        rate(player.season_assists, assist),
        rate(
            player.season_defensive_contribution, 7 if player.position == Position.DEFENDER else 5
        ),
        rate(player.season_saves, 3),
        rate(player.season_yellow_cards, 0.14),
        starting_minutes(player),
    )
