"""Player projections from the same joint fixture draws used by public analysis."""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from statistics import fmean, pvariance

from ..models import Player, Position
from ..points import event_points
from .models import (
    BENCHMARK_MODEL_VERSION,
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    FixtureSample,
    ForecastInputs,
    PlayerFixtureProjection,
)
from .simulation import FixtureSimulator


@dataclass(slots=True)
class _Totals:
    points: list[int] = field(default_factory=list)
    minutes: int = 0
    appearances: int = 0
    starts: int = 0
    goals: int = 0
    assists: int = 0
    clean_sheets: int = 0
    defensive_contribution: int = 0
    defensive_points: int = 0


class ProjectionAccumulator:
    """Summarise existing joint draws without rerunning or decoupling outcomes."""

    def __init__(self, inputs: ForecastInputs, player_ids: Iterable[int]) -> None:
        self.inputs = inputs
        selected = set(player_ids)
        if not selected <= inputs.players.keys():
            raise ValueError("Selected player IDs are absent from forecast inputs.")
        self.fixtures = {f.id: f for f in inputs.fixtures if not f.finished}
        self.totals = {
            (pid, f.id): _Totals()
            for f in sorted(self.fixtures.values(), key=lambda f: (f.gameweek, f.id))
            for pid in sorted(selected)
            if f.involves(inputs.players[pid].team_id)
        }

    def add(self, samples: tuple[FixtureSample, ...]) -> None:
        for sample in samples:
            for pid, stats in sample.stats.items():
                row = self.totals.get((pid, sample.fixture_id))
                if row is None:
                    continue
                row.points.append(event_points(self.inputs.players[pid].position, stats))
                minutes = stats.get("minutes", 0)
                row.minutes += minutes
                row.appearances += bool(
                    minutes or stats.get("yellow_cards") or stats.get("red_cards")
                )
                start, end = sample.windows[pid]
                row.starts += start == 0 and end > 0
                row.goals += stats.get("goals_scored", 0)
                row.assists += stats.get("assists", 0)
                row.clean_sheets += stats.get("clean_sheets", 0)
                row.defensive_contribution += stats.get("defensive_contribution", 0)
                row.defensive_points += event_points(
                    self.inputs.players[pid].position,
                    {"defensive_contribution": stats.get("defensive_contribution", 0)},
                )

    def results(self) -> tuple[PlayerFixtureProjection, ...]:
        projections: list[PlayerFixtureProjection] = []
        for (pid, fid), row in self.totals.items():
            fixture, player = self.fixtures[fid], self.inputs.players[pid]
            n = len(row.points)
            old = self.inputs.fixture_stats.get((pid, fid), {})
            ordered = sorted(row.points)
            mean = fmean(row.points) if n else None
            projections.append(
                PlayerFixtureProjection(
                    player_id=pid,
                    fixture_id=fid,
                    gameweek=fixture.gameweek,
                    expected_points=mean,
                    expected_remaining_points=mean - event_points(player.position, old)
                    if mean is not None
                    else None,
                    expected_minutes=row.minutes / n if n else None,
                    appearance_probability=row.appearances / n if n else None,
                    starting_probability=(
                        float(self.inputs.confirmed_appearances[(pid, fid)].status == "starter")
                        if self.inputs.model_version != BENCHMARK_MODEL_VERSION
                        and (pid, fid) in self.inputs.confirmed_appearances
                        else row.starts / n
                        if n and not fixture.started
                        else None
                    ),
                    expected_goals=row.goals / n if n else None,
                    expected_assists=row.assists / n if n else None,
                    clean_sheet_probability=row.clean_sheets / n if n else None,
                    points_variance=pvariance(row.points) if n else None,
                    points_interval_80=(ordered[int((n - 1) * 0.1)], ordered[int((n - 1) * 0.9)])
                    if n
                    else None,
                    samples=n,
                    coverage="modelled" if n else "missing_live_breakdown",
                    uses_player_fallbacks=_uses_fallbacks(player),
                    uses_team_fallbacks=any(
                        team not in self.inputs.team_goal_rates
                        for team in (
                            fixture.home_team_id,
                            fixture.away_team_id,
                        )
                    ),
                    model_version=self.inputs.model_version,
                    data_timestamp=self.inputs.fetched_at,
                    provenance=self.inputs.provenance,
                    expected_defensive_contribution=row.defensive_contribution / n if n else None,
                    expected_defensive_points=row.defensive_points / n if n else None,
                )
            )
        return tuple(projections)


def _uses_fallbacks(player: Player) -> bool:
    return (
        any(
            value is None
            for value in (
                player.season_minutes,
                player.season_starts,
                player.season_team_matches,
                player.season_goals,
                player.season_assists,
                player.season_yellow_cards,
                player.season_saves
                if player.position == Position.GOALKEEPER
                else player.season_defensive_contribution,
            )
        )
        or not player.season_minutes
    )


def project_players(
    inputs: ForecastInputs,
    *,
    player_ids: tuple[int, ...] | None = None,
    gameweeks: tuple[int, ...] | None = None,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> tuple[PlayerFixtureProjection, ...]:
    """Project unfinished fixtures over a supplied horizon without any squad.

    No retrieval or future-data fabrication occurs here. Gameweeks select supplied
    fixtures; returned IDs preserve doubles and postponed fixtures. There is no
    cross-Gameweek form/injury evolution model yet. The selected player set is part
    of model identity: omitted players participate via anonymous allocation weights.
    """
    if not 1 <= samples <= 5000:
        raise ValueError("Simulation count must be between 1 and 5000.")
    if gameweeks is not None:
        inputs = replace(
            inputs, fixtures=tuple(f for f in inputs.fixtures if f.gameweek in gameweeks)
        )
    model = FixtureSimulator(inputs, player_ids=player_ids)
    accumulator = ProjectionAccumulator(inputs, model.players)
    rng = random.Random(seed)
    for _ in range(samples if model.fixtures else 0):
        accumulator.add(model.sample(rng))
    return accumulator.results()
