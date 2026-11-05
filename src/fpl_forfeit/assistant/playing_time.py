"""Experimental, cutoff-safe playing states for private shared-outcome forecasts.

This is a shrunk empirical model, not a claim of calibrated injury recovery. Only
observed official histories and the current official availability flag are used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from random import Random

from ..api import parse_ml_history
from ..forecasting.horizon import HorizonForecast, forecast_horizon
from ..forecasting.models import DataSource, FixtureSample, ForecastInputs
from ..forecasting.statistics import PlayerRates, player_rates
from ..models import Fixture, Player, Position
from .component_forecast import ComponentSimulator

VERSION = "minutes-states-v1"


@dataclass(frozen=True, slots=True)
class MinutesEstimate:
    # Conditional on current availability: absent / substitute / short start / 60+ start.
    states: tuple[float, float, float, float]
    availability: float
    starting_minutes: tuple[int, ...]
    substitute_minutes: tuple[int, ...]
    observations: int
    horizon_days: float

    @property
    def start_probability(self) -> float:
        return self.availability * sum(self.states[2:])

    @property
    def substitute_probability(self) -> float:
        return self.availability * self.states[1]

    @property
    def no_show_probability(self) -> float:
        return 1 - self.start_probability - self.substitute_probability

    @property
    def sixty_probability(self) -> float:
        start = sum(m >= 60 for m in self.starting_minutes) / len(self.starting_minutes)
        sub = sum(m >= 60 for m in self.substitute_minutes) / len(self.substitute_minutes)
        return self.start_probability * start + self.substitute_probability * sub

    @property
    def expected_minutes(self) -> float:
        return self.start_probability * sum(self.starting_minutes) / len(
            self.starting_minutes
        ) + self.substitute_probability * sum(self.substitute_minutes) / len(
            self.substitute_minutes
        )

    def rates(self, original: PlayerRates) -> PlayerRates:
        """Availability is sampled once per GW by MinutesSimulator, never twice."""
        start = sum(self.states[2:])
        return replace(
            original,
            start=start,
            cameo_probability=self.states[1] / (1 - start) if start < 1 else 0,
            starting_minutes=self.starting_minutes,
            cameo_minutes=self.substitute_minutes,
        )


def _duration_sample(values: list[tuple[int, float]], prior: tuple[int, ...]) -> tuple[int, ...]:
    """Deterministic weighted quantiles retain thresholds instead of scoring the mean."""
    weighted = [(m, 4 / len(prior)) for m in prior] + values
    weighted.sort()
    total = sum(w for _, w in weighted)
    result = []
    for i in range(100):
        target, accumulated = (i + 0.5) * total / 100, 0.0
        for minutes, weight in weighted:
            accumulated += weight
            if accumulated >= target:
                result.append(minutes)
                break
    return tuple(result)


def estimate_minutes(
    player: Player,
    rows: list[dict],
    *,
    horizon_days: float = 0,
) -> MinutesEstimate:
    """Recency-weight starts/nonstarts including zeroes; shrink to season/prior.

    Eight prior matches stabilise sparse histories. A four-match half life fades
    towards the longer-run prior with a 28-day horizon half life. No future lineup,
    club report, return date or unobserved cross-competition fixture is invented.
    """
    if not math.isfinite(horizon_days) or horizon_days < 0:
        raise ValueError("A finite nonnegative forecast horizon is required")
    valid = []
    for row in rows:
        minutes, starts = row.get("minutes"), row.get("starts")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, (int, float))
            or not math.isfinite(minutes)
            or int(minutes) != minutes
            or not 0 <= minutes <= 90
            or starts not in (0, 1)
            or (starts == 1 and minutes == 0)
        ):
            raise ValueError("Playing-time history needs explicit valid minutes and starts")
        valid.append((int(minutes), int(starts)))
    original = player_rates(player)
    availability = (
        player.chance_of_playing / 100
        if player.chance_of_playing is not None
        else 0.0
        if player.availability_status in {"i", "s", "u", "n"}
        else 1.0
    )
    if not 0 <= availability <= 1:
        raise ValueError("Invalid official availability probability")
    matches = player.season_team_matches or 0
    starts = player.season_starts
    # Season inputs are as-of totals, never end-of-season retrospectively joined totals.
    season_start = (starts + 4 * 0.8) / (matches + 4) if starts is not None else 0.8
    season_start = min(0.98, max(0, season_start))
    sub = (1 - season_start) * (0.02 if player.position == Position.GOALKEEPER else 0.25)
    long = sum(m >= 60 for m in original.starting_minutes) / len(original.starting_minutes)
    prior = (1 - season_start - sub, sub, season_start * (1 - long), season_start * long)
    counts = [8 * p for p in prior]
    starter_durations, sub_durations = [], []
    horizon_weight = 2 ** (-horizon_days / 28)
    for index, (minutes, started) in enumerate(valid[-20:]):
        age = min(20, len(valid)) - 1 - index
        weight = 2 ** (-age / 4) * horizon_weight
        state = 0 if minutes == 0 else 1 if not started else 2 if minutes < 60 else 3
        counts[state] += weight
        if started:
            starter_durations.append((minutes, weight))
        elif minutes:
            sub_durations.append((minutes, weight))
    total = sum(counts)
    durations = _duration_sample(starter_durations, original.starting_minutes)
    # Reconcile the short/long start mass with the empirical threshold mixture.
    start_mass = counts[2] + counts[3]
    long_fraction = sum(m >= 60 for m in durations) / len(durations)
    counts[2], counts[3] = start_mass * (1 - long_fraction), start_mass * long_fraction
    return MinutesEstimate(
        tuple(c / total for c in counts),
        availability,
        durations,
        _duration_sample(sub_durations, (5, 10, 15, 20)),
        len(valid),
        horizon_days,
    )


class MinutesSimulator(ComponentSimulator):
    """Shared matches and legal lineups; current unavailability persists across a DGW."""

    def __init__(self, inputs: ForecastInputs, estimates: dict[tuple[int, int], MinutesEstimate]):
        rates = {
            key: estimate.rates(player_rates(inputs.players[key[1]]))
            for key, estimate in estimates.items()
        }
        super().__init__(inputs, rates=rates, goals={}, version=VERSION)
        self.estimates = estimates
        self.available: dict[int, bool] = {}
        self.inputs = replace(
            self.inputs,
            provenance=inputs.provenance
            + (
                DataSource(
                    "official_history_minutes_states",
                    "Recency-weighted starts, nonappearances and empirical durations; shrunk priors; "
                    "current availability shared across DGW fixtures. Experimental, no inferred return dates.",
                ),
            ),
        )

    def sample(self, rng: Random) -> tuple[FixtureSample, ...]:
        availability = {pid: e.availability for (_, pid), e in self.estimates.items()}
        self.available = {pid: rng.random() < p for pid, p in sorted(availability.items())}
        return super().sample(rng)

    def sample_fixture(self, fixture: Fixture, rng: Random) -> FixtureSample:
        saved = self.fixture_rates
        self.fixture_rates = {
            key: rate
            if self.available.get(key[1], True)
            else replace(rate, start=0, cameo_probability=0)
            for key, rate in saved.items()
        }
        try:
            return super().sample_fixture(fixture, rng)
        finally:
            self.fixture_rates = saved


def build_minutes_forecast(
    baseline: HorizonForecast,
    snapshot: dict,
    histories: dict[int, dict],
    asof: datetime,
) -> tuple[HorizonForecast, dict]:
    first = next(g for g in baseline.catalogue.gameweeks if g.id == baseline.gameweeks[0])
    if first.deadline is None or asof >= first.deadline:
        raise ValueError("Playing-time forecasts require a pre-deadline cutoff")
    estimates, coverage = {}, []
    for pid, player in baseline.catalogue.players.items():
        try:
            history = histories[pid]
            retrieved = datetime.fromisoformat(history["retrieved_at"])
            if retrieved > asof:
                raise ValueError("History retrieved after feature cutoff")
            rows = parse_ml_history(
                history["payload"],
                snapshot["fixtures"],
                pid,
                baseline.gameweeks[0],
                asof,
            )
            if not rows or rows[-1]["team_id"] != player.team_id:
                raise ValueError("Missing or transferred history")
            for fixture in baseline.inputs.fixtures:
                if not fixture.involves(player.team_id):
                    continue
                deadline = next(
                    g.deadline for g in baseline.catalogue.gameweeks if g.id == fixture.gameweek
                )
                days = max(0, (deadline - first.deadline).total_seconds() / 86400)
                estimates[fixture.id, pid] = estimate_minutes(player, rows, horizon_days=days)
            coverage.append(
                {"player_id": pid, "source": "minutes_states", "observations": len(rows)}
            )
        except (KeyError, ValueError, TypeError):
            coverage.append(
                {
                    "player_id": pid,
                    "source": "statistical_fallback",
                    "reason": "missing_invalid_or_transferred_history",
                }
            )
    forecast = forecast_horizon(
        baseline.catalogue,
        baseline.gameweeks,
        samples=baseline.samples,
        seed=baseline.seed,
        simulator_factory=lambda inputs: MinutesSimulator(inputs, estimates),
    )
    by_pid = {r["player_id"]: r for r in coverage}
    for pid, weekly in forecast.players.items():
        for row in weekly.values():
            row["minutes_coverage"] = by_pid[pid]
    return forecast, {
        "version": VERSION,
        "feature_asof_utc": asof.isoformat(),
        "coverage": coverage,
    }
