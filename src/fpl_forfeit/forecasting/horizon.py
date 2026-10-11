"""Future Gameweek aggregation over the existing shared fixture simulator.

No league, personal squad, authentication, HTTP or second football model lives here.
The same stored outcomes support forecasts, captaincy, autosubs and plan comparisons.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import fmean, pvariance

from ..models import FPLCatalogue
from ..points import event_points
from .models import DEFAULT_SEED, DataSource, ForecastInputs, PlayerFixtureProjection
from .projections import ProjectionAccumulator
from .simulation import FixtureSimulator


@dataclass(frozen=True, slots=True)
class OutcomeSamples:
    points: tuple[int, ...]
    minutes: tuple[int, ...]
    appeared: bytes


@dataclass(slots=True)
class HorizonForecast:
    catalogue: FPLCatalogue
    gameweeks: tuple[int, ...]
    samples: int
    seed: int
    inputs: ForecastInputs
    fixtures: tuple[PlayerFixtureProjection, ...]
    outcomes: dict[int, dict[int, OutcomeSamples]]
    players: dict[int, dict[int, dict]]


def forecast_horizon(
    catalogue: FPLCatalogue,
    gameweeks: tuple[int, ...],
    *,
    samples: int = 192,
    seed: int = DEFAULT_SEED,
) -> HorizonForecast:
    if not 1 <= samples <= 1600 or len(gameweeks) > 6 or len(set(gameweeks)) != len(gameweeks):
        raise ValueError("Invalid bounded forecasting horizon.")
    fixtures = tuple(f for f in catalogue.fixtures if f.gameweek in gameweeks)
    if any(f.started or f.finished for f in fixtures):
        raise ValueError("Future forecasts require fixtures that have not started.")
    inputs = ForecastInputs(
        catalogue.players,
        fixtures,
        catalogue.fetched_at,
        team_goal_rates=catalogue.team_goal_rates,
        provenance=(
            DataSource(
                "official_fpl_bootstrap",
                "Season xG/xA, minutes, starts, prices and current availability",
            ),
            DataSource(
                "official_fpl_fixtures", "Published fixture assignments and completed team results"
            ),
        ),
    )
    accumulated = ProjectionAccumulator(inputs, inputs.players)
    outcomes: dict[int, dict[int, OutcomeSamples]] = {}
    for gw in gameweeks:
        model = FixtureSimulator(
            replace(inputs, fixtures=tuple(f for f in fixtures if f.gameweek == gw))
        )
        rng = random.Random(seed + gw)
        points = {pid: [0] * samples for pid in inputs.players}
        minutes = {pid: [0] * samples for pid in inputs.players}
        appeared = {pid: bytearray(samples) for pid in inputs.players}
        for index in range(samples):
            draws = model.sample(rng)
            accumulated.add(draws)
            for draw in draws:
                for pid, stats in draw.stats.items():
                    points[pid][index] += event_points(inputs.players[pid].position, stats)
                    minutes[pid][index] += stats.get("minutes", 0)
                    appeared[pid][index] |= bool(
                        stats.get("minutes") or stats.get("yellow_cards") or stats.get("red_cards")
                    )
        outcomes[gw] = {
            pid: OutcomeSamples(tuple(points[pid]), tuple(minutes[pid]), bytes(appeared[pid]))
            for pid in inputs.players
        }
    projections = accumulated.results()
    by_player: dict[tuple[int, int], list[PlayerFixtureProjection]] = defaultdict(list)
    for projection in projections:
        by_player[projection.player_id, projection.gameweek].append(projection)
    known_gameweeks = {gw.id for gw in catalogue.gameweeks}
    rows = {}
    for pid, player in catalogue.players.items():
        rows[pid] = {}
        for gw in gameweeks:
            projected = by_player[pid, gw]
            coverage = (
                "unknown"
                if gw not in known_gameweeks
                else "partial"
                if player.team_id in catalogue.unassigned_team_ids
                or player.team_id in catalogue.missing_schedule_team_ids
                else "published"
                if projected
                else "confirmed_blank"
                if catalogue.schedule_complete
                else "unknown"
            )
            available = coverage in {"published", "confirmed_blank"}
            draws = outcomes[gw][pid]
            ordered = sorted(draws.points)
            rows[pid][gw] = {
                "gameweek": gw,
                "fixture_ids": [p.fixture_id for p in projected],
                "coverage": coverage,
                "expected_points": fmean(draws.points) if available else None,
                "published_fixture_points": fmean(draws.points) if projected else None,
                "expected_minutes": fmean(draws.minutes) if available else None,
                "appearance_probability": sum(draws.appeared) / samples if available else None,
                "starting_probability": projected[0].starting_probability
                if len(projected) == 1
                else None,
                "expected_goals": sum(p.expected_goals or 0 for p in projected)
                if available
                else None,
                "expected_assists": sum(p.expected_assists or 0 for p in projected)
                if available
                else None,
                "expected_defensive_points": sum(
                    p.expected_defensive_points or 0 for p in projected
                )
                if available
                else None,
                "clean_sheet_expectation": sum(p.clean_sheet_probability or 0 for p in projected)
                if available
                else None,
                "points_variance": pvariance(draws.points) if available else None,
                "points_interval_80": [
                    ordered[int((samples - 1) * 0.1)],
                    ordered[int((samples - 1) * 0.9)],
                ]
                if available
                else None,
                "uses_player_fallbacks": any(p.uses_player_fallbacks for p in projected),
                "uses_team_fallbacks": any(p.uses_team_fallbacks for p in projected),
            }
    return HorizonForecast(catalogue, gameweeks, samples, seed, inputs, projections, outcomes, rows)
