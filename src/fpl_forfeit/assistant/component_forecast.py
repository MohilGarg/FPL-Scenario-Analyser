"""Private component adapter: one shared score/events process, never added point deltas."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from random import Random

from ..api import parse_ml_history
from ..forecasting.horizon import HorizonForecast, forecast_horizon
from ..forecasting.models import MODEL_VERSION, DataSource, FixtureSample, ForecastInputs
from ..forecasting.simulation import FixtureSimulator
from ..forecasting.statistics import PlayerRates
from ..models import Fixture
from .component_features import FEATURE_COLUMNS, fixture_features, player_features, team_features
from .component_model import ComponentModel


class ComponentSimulator(FixtureSimulator):
    def __init__(
        self,
        inputs: ForecastInputs,
        *,
        rates: dict[tuple[int, int], PlayerRates],
        goals: dict[tuple[int, int], float],
        version: str,
    ) -> None:
        super().__init__(replace(inputs, model_version=MODEL_VERSION))
        self.base_rates = self.rates
        self.fixture_rates, self.fixture_goals = rates, goals
        provenance = inputs.provenance
        if not any(p.name == "fixture_component_ml" for p in provenance):
            provenance += (
                DataSource(
                    "fixture_component_ml",
                    "Regularised components trained with explicit historical schedule proxies; observed form frozen through six GWs. Statistical unsupported-component/availability fallbacks.",
                ),
            )
        self.inputs = replace(inputs, model_version=version, provenance=provenance)
        self.active_fixture = None

    def _goal_rate(self, team: int, opponent: int, home: bool) -> float:
        return self.fixture_goals.get(
            (self.active_fixture, team), super()._goal_rate(team, opponent, home)
        )

    def sample_fixture(self, fixture: Fixture, rng: Random) -> FixtureSample:
        self.active_fixture = fixture.id
        self.rates = {
            pid: self.fixture_rates.get((fixture.id, pid), rate)
            for pid, rate in self.base_rates.items()
        }
        for team in (fixture.home_team_id, fixture.away_team_id):
            for kind in ("goal", "assist"):
                self.unowned_weights[fixture.id, team, kind] = sum(
                    getattr(self.rates[p.id], kind) * self.rates[p.id].start
                    for p in self.inputs.players.values()
                    if p.team_id == team and p.id not in self.player_ids
                )
            self.unowned_starts[fixture.id, team] = sum(
                self.rates[p.id].start
                for p in self.inputs.players.values()
                if p.team_id == team and p.id not in self.player_ids
            )
        return super().sample_fixture(fixture, rng)


def build_component_forecast(
    baseline: HorizonForecast,
    snapshot: dict,
    histories: dict[int, dict],
    asof: datetime,
    *,
    model: ComponentModel | None = None,
) -> tuple[HorizonForecast, dict]:
    from ..forecasting.statistics import player_rates

    model = model or ComponentModel()
    strengths = team_features(snapshot["fixtures"], baseline.gameweeks[0], asof)
    index = {f["id"]: f for f in snapshot["fixtures"]}
    rates, goals, metadata = {}, {}, []
    for fixture in baseline.inputs.fixtures:
        raw = index.get(fixture.id)
        if not raw or not raw.get("kickoff_time"):
            for pid, player in baseline.catalogue.players.items():
                if fixture.involves(player.team_id):
                    metadata.append(
                        {
                            "fixture_id": fixture.id,
                            "player_id": pid,
                            "gameweek": fixture.gameweek,
                            "learned_components": [],
                            "fallbacks": ["statistical_missing_fixture_context"],
                            "team_score_source": "statistical",
                            "known_features": 0,
                            "approved_features": len(FEATURE_COLUMNS),
                            "feature_asof_utc": asof.isoformat(),
                        }
                    )
            continue
        learned_teams = []
        for team in (fixture.home_team_id, fixture.away_team_id):
            values = fixture_features(raw, team, snapshot["fixtures"], strengths)
            if (
                model.data["enabled"]["team_goals"]
                and min(values["own_count"], values["opp_count"]) >= 3
            ):
                goals[fixture.id, team] = min(4, max(0.05, model.predict("team_goals", values)))
                learned_teams.append(team)
        for pid, player in baseline.catalogue.players.items():
            if not fixture.involves(player.team_id):
                continue
            original = player_rates(player)
            flags = [
                "statistical_defensive_contribution",
                "statistical_bonus_competition",
                "rare_events_not_comprehensively_trained",
            ]
            enabled, values = [], None
            try:
                history = histories[pid]
                rows = parse_ml_history(
                    history["payload"], snapshot["fixtures"], pid, baseline.gameweeks[0], asof
                )
                if len(rows) < 3 or rows[-1]["team_id"] != player.team_id:
                    raise ValueError("short_or_transferred_history")
                if sum(r.get("minutes") or 0 for r in rows[-10:]) < model.data["config"].get(
                    "history_min_minutes", 0
                ):
                    flags.append("statistical_insufficient_minutes_for_per90_features")
                    raise ValueError("insufficient_observed_exposure")
                values = {
                    **player_features(rows, player.position.value),
                    **fixture_features(raw, player.team_id, snapshot["fixtures"], strengths),
                }
                proposal = model.proposals(values)
                updates = {}
                if (
                    model.data["enabled"]["playing_state"]
                    and player.availability_status in (None, "a")
                    and player.chance_of_playing in (None, 100)
                ):
                    updates.update(
                        {
                            k: proposal[k]
                            for k in (
                                "start",
                                "starting_minutes",
                                "cameo_probability",
                                "cameo_minutes",
                            )
                        }
                    )
                    enabled.append("playing_time")
                else:
                    flags.append("statistical_availability_or_playing_time_gate")
                for name, target in (
                    ("goals", "goal"),
                    ("assists", "assist"),
                    ("saves", "saves"),
                    ("yellow", "yellow"),
                ):
                    if model.data["enabled"][name]:
                        updates[target] = proposal[target]
                        enabled.append(name)
                    else:
                        flags.append("statistical_" + name + "_validation_gate")
                rates[fixture.id, pid] = replace(original, **updates)
            except (KeyError, ValueError, TypeError):
                flags.append("statistical_missing_or_transferred_history")
            metadata.append(
                {
                    "fixture_id": fixture.id,
                    "player_id": pid,
                    "gameweek": fixture.gameweek,
                    "learned_components": enabled,
                    "fallbacks": flags,
                    "team_score_source": "ml"
                    if len(learned_teams) == 2
                    else "mixed_or_statistical",
                    "known_features": sum(values[c] is not None for c in FEATURE_COLUMNS)
                    if values
                    else 0,
                    "approved_features": len(FEATURE_COLUMNS),
                    "feature_asof_utc": asof.isoformat(),
                }
            )
    factory = lambda inputs: ComponentSimulator(
        inputs, rates=rates, goals=goals, version=model.version
    )
    forecast = forecast_horizon(
        baseline.catalogue,
        baseline.gameweeks,
        samples=baseline.samples,
        seed=baseline.seed,
        simulator_factory=factory,
    )
    for pid, weekly in forecast.players.items():
        for gw, row in weekly.items():
            coverage = [m for m in metadata if m["player_id"] == pid and m["gameweek"] == gw]
            row["component_coverage"] = coverage
    return forecast, {
        "version": model.version,
        "model_sha256": model.sha256,
        "model": model.data,
        "fixture_metadata": metadata,
        "team_goal_rates": [
            {"fixture_id": fid, "team_id": team, "rate": value}
            for (fid, team), value in goals.items()
        ],
        "schedule_training_policy": model.data["config"]["schedule_policy"],
        "horizon_assumption": model.data["config"]["horizon"],
        "feature_asof_utc": asof.isoformat(),
    }
