"""Private coherent minutes adapter over the shared football simulator and scorer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from random import Random

from ..api import parse_ml_history
from ..forecasting.horizon import HorizonForecast, forecast_horizon
from ..forecasting.models import DataSource, ForecastInputs
from ..models import Fixture, Position
from .minutes_model import MinutesModel
from .playing_time import MinutesSimulator


def dependent_starters(
    probabilities: dict[int, float], capacity: int, rng: Random
) -> tuple[set[int], dict[int, float]]:
    """Pivotal rounding preserves feasible marginals and never exceeds capacity.

    Overfull input marginals are proportionally scaled first; the projection is
    explicit, since no legal joint distribution can preserve an infeasible sum.
    Anonymous teammates fill unused slots. No actual historical XI is consulted.
    """
    if capacity < 0 or any(not 0 <= p <= 1 for p in probabilities.values()):
        raise ValueError("Invalid starter marginals/capacity")
    total = sum(probabilities.values())
    projected = {
        pid: p * min(1, capacity / total) if total else 0.0 for pid, p in probabilities.items()
    }
    values = dict(projected)
    fractional = [pid for pid, p in values.items() if 1e-12 < p < 1 - 1e-12]
    rng.shuffle(fractional)
    while len(fractional) > 1:
        a, b = fractional.pop(), fractional.pop()
        x, y = values[a], values[b]
        if x + y <= 1:
            if rng.random() < x / (x + y):
                values[a], values[b] = x + y, 0.0
            else:
                values[a], values[b] = 0.0, x + y
        else:
            if rng.random() < (1 - y) / (2 - x - y):
                values[a], values[b] = 1.0, x + y - 1
            else:
                values[a], values[b] = x + y - 1, 1.0
        fractional.extend(pid for pid in (a, b) if 1e-12 < values[pid] < 1 - 1e-12)
    if fractional:
        pid = fractional[0]
        values[pid] = float(rng.random() < values[pid])
    selected = {pid for pid, p in values.items() if p >= 1 - 1e-12}
    if len(selected) > capacity:
        raise ValueError("Dependent rounding exceeded legal starter capacity")
    return selected, projected


class FittedMinutesSimulator(MinutesSimulator):
    def __init__(
        self,
        inputs: ForecastInputs,
        estimates: dict,
        *,
        version: str,
        balanced: bool = True,
        goals: dict | None = None,
        attacking_model=None,
    ):
        # Horizon construction calls the simulator separately for each Gameweek.
        # A later conditional-if-available estimate must not overwrite the current
        # Gameweek's official availability cap in the shared per-player draw.
        active = {fixture.id for fixture in inputs.fixtures}
        super().__init__(
            inputs, {key: value for key, value in estimates.items() if key[0] in active}
        )
        if attacking_model is not None:
            self.fixture_rates = {
                key: attacking_model.rates(inputs.players[key[1]], rate)
                for key, rate in self.fixture_rates.items()
            }
        self.balanced = balanced
        self.fixture_goals = goals or {}
        self.inputs = replace(
            self.inputs,
            model_version=version,
            provenance=inputs.provenance
            + (
                DataSource(
                    "coherent_fitted_minutes",
                    "Chronologically fitted five-state model, training-only cold priors and conditional durations. "
                    "Official next-GW chance caps historical appearance once; no inferred injury return dates. "
                    "Pivotal starter sampling projects overfull marginals to one GK/ten outfield slots."
                    if balanced
                    else "Coherent minutes with the original greedy shared-lineup constraints; experimental ablation.",
                ),
            ),
        )

    def _windows(self, fixture: Fixture, team: int, rng: Random):
        if not self.balanced or fixture.started:
            return super()._windows(fixture, team, rng)
        original = self.rates
        updated = dict(original)
        for goalkeeper, capacity in ((True, 1), (False, 10)):
            rates = {
                p.id: original[p.id].start
                for p in self.team_players[fixture.id, team]
                if (p.position == Position.GOALKEEPER) == goalkeeper
            }
            selected, projected = dependent_starters(rates, capacity, rng)
            for pid in rates:
                rate = original[pid]
                sub_mass = (1 - rate.start) * (rate.cameo_probability or 0)
                updated[pid] = replace(
                    rate,
                    start=float(pid in selected),
                    cameo_probability=min(1.0, sub_mass / (1 - projected[pid]))
                    if projected[pid] < 1
                    else 0.0,
                )
        self.rates = updated
        try:
            return super()._windows(fixture, team, rng)
        finally:
            self.rates = original


def build_fitted_forecast(
    baseline: HorizonForecast,
    snapshot: dict,
    histories: dict[int, dict],
    asof: datetime,
    *,
    model: MinutesModel | None = None,
    balanced: bool = True,
    goals: dict[tuple[int, int], float] | None = None,
    attacking_model=None,
) -> tuple[HorizonForecast, dict]:
    model = model or MinutesModel()
    first = next(g for g in baseline.catalogue.gameweeks if g.id == baseline.gameweeks[0])
    if asof.tzinfo is None or first.deadline is None or asof >= first.deadline:
        raise ValueError("Fitted minutes require a pre-deadline cutoff")
    estimates, coverage = {}, []
    for pid, player in baseline.catalogue.players.items():
        flags, rows = [], []
        valid_source = True
        try:
            source = histories[pid]
            if datetime.fromisoformat(source["retrieved_at"]) > asof:
                raise ValueError("Late historical source")
            rows = parse_ml_history(
                source["payload"], snapshot["fixtures"], pid, baseline.gameweeks[0], asof
            )
        except (KeyError, TypeError, ValueError):
            flags.append("statistical_fallback_missing_or_invalid_history_source")
            valid_source = False
        if valid_source and not rows:
            flags.append("training_only_cold_prior_no_observations")
        if rows and rows[-1]["team_id"] != player.team_id:
            flags.append("club_change_selection_uncertainty")
        if valid_source:
            try:
                player_estimates = {}
                for fixture in baseline.inputs.fixtures:
                    if not fixture.involves(player.team_id):
                        continue
                    deadline = next(
                        g.deadline for g in baseline.catalogue.gameweeks if g.id == fixture.gameweek
                    )
                    days = max(0.0, (deadline - first.deadline).total_seconds() / 86400)
                    player_estimates[fixture.id, pid] = model.predict(
                        player, rows, horizon_days=days
                    )
            except (KeyError, TypeError, ValueError):
                flags.append("statistical_fallback_invalid_playing_time_history")
                valid_source = False
            else:
                estimates.update(player_estimates)
        coverage.append(
            {
                "player_id": pid,
                "observations": len(rows),
                "source": model.data["kind"] if valid_source else "statistical_fallback",
                "flags": flags,
            }
        )
    version = (
        model.version
        + ("-balanced" if balanced else "-greedy")
        + ("-team" if goals else "")
        + ("-attacking" if attacking_model else "")
    )
    forecast = forecast_horizon(
        baseline.catalogue,
        baseline.gameweeks,
        samples=baseline.samples,
        seed=baseline.seed,
        simulator_factory=lambda inputs: FittedMinutesSimulator(
            inputs,
            estimates,
            version=version,
            balanced=balanced,
            goals=goals,
            attacking_model=attacking_model,
        ),
    )
    for pid, weekly in forecast.players.items():
        for gw, row in weekly.items():
            row["minutes_coverage"] = next(c for c in coverage if c["player_id"] == pid)
            uncertain = any(
                e.availability_unknown
                for (fid, p), e in estimates.items()
                if p == pid and fid in row["fixture_ids"]
            )
            player = baseline.catalogue.players[pid]
            uncertain = uncertain or (
                gw != baseline.gameweeks[0]
                and bool(row["fixture_ids"])
                and (
                    player.chance_of_playing not in (None, 100)
                    or player.availability_status in {"i", "s", "u", "n", "d"}
                )
            )
            row["availability_unknown"] = uncertain
            if uncertain:
                row["conditional_if_available"] = dict(row)
                row["coverage"] = "future_availability_unknown"
                # No point mean is invented by pretending current uncertainty is a
                # definitive six-week injury forecast or an established recovery.
                for key in (
                    "expected_points",
                    "expected_minutes",
                    "appearance_probability",
                    "starting_probability",
                    "probability_60_minutes",
                    "substitute_probability",
                    "no_show_probability",
                    "blank_probability",
                    "haul_probability",
                    "points_variance",
                    "points_interval_80",
                    "points_quantiles",
                    "mean_standard_error",
                    "expected_points_components",
                ):
                    row[key] = None
    return forecast, {
        "version": version,
        "feature_asof_utc": asof.isoformat(),
        "model_sha256": model.sha256,
        "model": model.data,
        "attacking_model": attacking_model.data if attacking_model else None,
        "coverage": coverage,
        "lineup_sampler": "pivotal" if balanced else "legacy_greedy",
        "future_availability_policy": "current official chance is next-GW only; later flagged-player means unknown with conditional-if-available scenario",
    }
