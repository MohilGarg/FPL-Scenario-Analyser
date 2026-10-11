"""Normalised football inputs and projections, independent of leagues and squads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from ..models import ConfirmedAppearance, Fixture, Player

MODEL_VERSION = "shared-fixtures-v4"
BENCHMARK_MODEL_VERSION = "shared-fixtures-v3"
DEFAULT_SEED = 20261010
DEFAULT_SAMPLES = 1600
OVERVIEW_SAMPLES = 96


@dataclass(frozen=True, slots=True)
class DataSource:
    name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TeamMatchResult:
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int


@dataclass(frozen=True, slots=True)
class ForecastInputs:
    """One observed roster and a fixture horizon, potentially spanning many Gameweeks.

    Dictionaries contain normalised domain data, never raw API payloads. Callers must
    keep an input generation unchanged while sampling. Missing live breakdowns stay
    unknown. Future fixtures need no breakdown; their unplayed baseline is zero.
    """

    players: Mapping[int, Player]
    fixtures: tuple[Fixture, ...]
    fetched_at: datetime
    fixture_minutes: Mapping[tuple[int, int], int] = field(default_factory=dict)
    fixture_stats: Mapping[tuple[int, int], Mapping[str, int]] = field(default_factory=dict)
    team_goal_rates: Mapping[int, tuple[float, float]] = field(default_factory=dict)
    provenance: tuple[DataSource, ...] = (DataSource("caller_supplied"),)
    confirmed_appearances: Mapping[tuple[int, int], ConfirmedAppearance] = field(
        default_factory=dict
    )
    model_version: str = MODEL_VERSION


class ScenarioConflict(ValueError):
    """Hypothetical events conflict with each other or observed football data."""


@dataclass(frozen=True, slots=True)
class Condition:
    fixture_id: int
    kind: str
    player_id: int | None = None
    team_id: int | None = None
    value: int = 1
    home_goals: int | None = None
    away_goals: int | None = None


def canonical_conditions(conditions: tuple[Condition, ...]) -> tuple[Condition, ...]:
    """Equivalent conjunctions consume random values and use caches identically."""
    return tuple(
        sorted(
            conditions,
            key=lambda c: (
                c.fixture_id,
                c.kind,
                c.player_id or 0,
                c.team_id or 0,
                c.value,
                -1 if c.home_goals is None else c.home_goals,
                -1 if c.away_goals is None else c.away_goals,
            ),
        )
    )


@dataclass(slots=True)
class FixtureSample:
    fixture_id: int
    home_goals: int
    away_goals: int
    stats: dict[int, dict[str, int]]
    # New goals only: (team, scorer, assister, minute); None means anonymous.
    events: list[tuple[int, int | None, int | None, int]]
    windows: dict[int, tuple[int, int]]


@dataclass(frozen=True, slots=True)
class PlayerFixtureProjection:
    """Unmultiplied final fixture expectations, conditional on observed data.

    Remaining points are category deltas, preserving the public consumer's official
    event baseline. Appearance includes zero-minute disciplinary appearances.
    Starting probability is unknown for live matches without observed lineup data.
    Clean sheet means the player qualifies (60+ minutes, no counted concession).
    Unknown breakdowns give null metrics rather than a fictitious zero forecast.
    """

    player_id: int
    fixture_id: int
    gameweek: int
    expected_points: float | None
    expected_remaining_points: float | None
    expected_minutes: float | None
    appearance_probability: float | None
    starting_probability: float | None
    expected_goals: float | None
    expected_assists: float | None
    clean_sheet_probability: float | None
    points_variance: float | None
    points_interval_80: tuple[int, int] | None
    samples: int
    coverage: Literal["modelled", "missing_live_breakdown"]
    uses_player_fallbacks: bool
    uses_team_fallbacks: bool
    model_version: str
    data_timestamp: datetime
    provenance: tuple[DataSource, ...]
