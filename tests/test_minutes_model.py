from __future__ import annotations

import math
import random
import unittest
from dataclasses import replace

from fpl_forfeit.assistant.fitted_forecast import build_fitted_forecast, dependent_starters
from fpl_forfeit.assistant.minutes_model import FEATURES, STATES, MinutesModel, history_features
from fpl_forfeit.assistant.service import AssistantService
from fpl_forfeit.forecasting.horizon import forecast_horizon
from tests.assistant_fixtures import NOW, FixtureClient, catalogue
from tests.test_fixture_components import context


def artifact():
    prior = [0.1, 0.15, 0.05, 0.15, 0.55]
    return {
        "format_version": 1,
        "version": "minutes-fitted-v2-test",
        "kind": "softmax",
        "features": list(FEATURES),
        "states": list(STATES),
        "priors": {p: list(prior) for p in ("GK", "DEF", "MID", "FWD")},
        "cold_priors": {p: [0.8, 0.05, 0, 0.02, 0.13] for p in ("GK", "DEF", "MID", "FWD")},
        "durations": {
            p: {str(s): [(m, 1)] for s, m in ((1, 10), (2, 65), (3, 45), (4, 80))}
            for p in ("GK", "DEF", "MID", "FWD")
        },
        "config": {"temperature": 1, "duration_strength": 4, "prior_strength": 2, "half_life": 6},
        "model": {
            "mean": [0] * len(FEATURES),
            "scale": [1] * len(FEATURES),
            "coef": [[0] * len(FEATURES) for _ in range(5)],
            "intercept": [math.log(p) for p in prior],
        },
    }


class CoherentMinutesTests(unittest.TestCase):
    def test_substitute_sixty_mass_and_conditional_durations_are_coherent(self):
        model = MinutesModel(data=artifact())
        player = catalogue().players[3]
        estimate = model.predict(player, [{"minutes": 45, "starts": 1}])
        self.assertAlmostEqual(sum(estimate.states), 1)
        self.assertAlmostEqual(
            estimate.start_probability
            + estimate.substitute_probability
            + estimate.no_show_probability,
            1,
        )
        self.assertGreater(estimate.states[2], 0)
        self.assertAlmostEqual(estimate.sixty_probability, estimate.states[2] + estimate.states[4])
        self.assertAlmostEqual(
            estimate.expected_minutes,
            estimate.start_probability * estimate.conditional_start_minutes
            + estimate.substitute_probability * estimate.conditional_substitute_minutes,
        )
        self.assertTrue(any(m >= 60 for m in estimate.substitute_minutes))

    def test_cold_priors_and_availability_cap_do_not_multiply_historic_absence_again(self):
        model = MinutesModel(data=artifact())
        player = catalogue().players[3]
        cold = model.predict(player, [])
        self.assertAlmostEqual(cold.no_show_probability, 0.8)
        doubtful = model.predict(
            replace(player, chance_of_playing=50), [{"minutes": 90, "starts": 1}]
        )
        self.assertAlmostEqual(1 - doubtful.no_show_probability, 0.5)
        future = model.predict(
            replace(player, chance_of_playing=0), [{"minutes": 90, "starts": 1}], horizon_days=7
        )
        self.assertTrue(future.availability_unknown)
        self.assertGreater(future.expected_minutes, 0)

    def test_invalid_artifact_or_unknown_observation_fails(self):
        data = artifact()
        data["durations"]["GK"]["2"] = [(59, 1)]
        with self.assertRaises(ValueError):
            MinutesModel(data=data)
        data = artifact()
        data["model"]["scale"][0] = 0
        with self.assertRaises(ValueError):
            MinutesModel(data=data)
        with self.assertRaises(ValueError):
            history_features(catalogue().players[1], [{"minutes": None, "starts": 0}])

    def test_pivotal_rounding_preserves_feasible_marginals_without_identity_priority(self):
        probabilities = {1: 0.9, 2: 0.7, 3: 0.4}
        counts = {pid: 0 for pid in probabilities}
        rng = random.Random(241)
        for _ in range(6000):
            selected, projected = dependent_starters(probabilities, 2, rng)
            self.assertLessEqual(len(selected), 2)
            self.assertEqual(projected, probabilities)
            for pid in selected:
                counts[pid] += 1
        for pid, probability in probabilities.items():
            self.assertAlmostEqual(counts[pid] / 6000, probability, delta=0.02)
        selected, projected = dependent_starters({1: 0.9, 2: 0.9, 3: 0.9}, 2, rng)
        self.assertAlmostEqual(sum(projected.values()), 2)
        self.assertEqual(len(selected), 2)

    def test_versioned_historical_simulation_scoring_and_component_reconciliation(self):
        cat = catalogue()
        old = forecast_horizon(replace(cat, scoring_season="2024-25"), (7,), samples=12)
        current = forecast_horizon(cat, (7,), samples=12)
        for pid in old.players:
            a, b = old.players[pid][7], current.players[pid][7]
            self.assertEqual(a["expected_defensive_points"], 0)
            self.assertAlmostEqual(
                sum(a["expected_points_components"].values()), a["expected_points"]
            )
            self.assertAlmostEqual(
                b["expected_points"] - a["expected_points"], b["expected_defensive_points"]
            )

    def test_fitted_forecast_cold_coverage_and_unknown_future_injury_are_explicit(self):
        baseline, snapshot, histories = context()
        cat = baseline.catalogue
        roster = dict(cat.players)
        roster[1] = replace(roster[1], chance_of_playing=25)
        extra = replace(cat.fixtures[0], id=120, gameweek=8)
        baseline = forecast_horizon(
            replace(cat, players=roster, fixtures=cat.fixtures + (extra,)), (7, 8), samples=8
        )
        forecast, meta = build_fitted_forecast(
            baseline, snapshot, histories, NOW, model=MinutesModel(data=artifact())
        )
        self.assertIn(
            "statistical_fallback_missing_or_invalid_history_source", meta["coverage"][1]["flags"]
        )
        self.assertIsNotNone(forecast.players[1][7]["expected_points"])
        self.assertIsNone(forecast.players[1][8]["expected_points"])
        self.assertTrue(forecast.players[1][8]["availability_unknown"])
        self.assertIsNotNone(forecast.players[1][8]["conditional_if_available"]["expected_points"])

    def test_runtime_selection_is_request_local_and_refresh_discards_experiments(self):
        service = AssistantService(FixtureClient(), samples=4, now=lambda: NOW)
        original = service.forecast()
        challenger, _ = build_fitted_forecast(
            original, {"fixtures": []}, {}, NOW, model=MinutesModel(data=artifact())
        )
        service.ml.generation = (original.catalogue.fetched_at.isoformat(), original.gameweeks)
        service.ml.challenger_forecasts["fitted_minutes"] = challenger
        with service.using_model("fitted_minutes"):
            self.assertIs(service.forecast(), challenger)
        self.assertIs(service.forecast(), original)
        service.refresh()
        self.assertEqual(service.ml.challenger_forecasts, {})

    def test_later_conditional_availability_cannot_erase_current_absence(self):
        baseline, snapshot, histories = context()
        cat = baseline.catalogue
        roster = dict(cat.players)
        roster[1] = replace(roster[1], chance_of_playing=0)
        extra = replace(cat.fixtures[0], id=120, gameweek=8)
        baseline = forecast_horizon(
            replace(cat, players=roster, fixtures=cat.fixtures + (extra,)), (7, 8), samples=32
        )
        forecast, _ = build_fitted_forecast(
            baseline, snapshot, histories, NOW, model=MinutesModel(data=artifact())
        )
        self.assertEqual(forecast.players[1][7]["expected_minutes"], 0)
        self.assertEqual(forecast.players[1][7]["appearance_probability"], 0)
        self.assertTrue(forecast.players[1][8]["availability_unknown"])

    def test_invalid_player_history_falls_back_without_losing_other_players(self):
        baseline, snapshot, histories = context()
        histories[1]["payload"]["history"][0]["starts"] = None
        forecast, meta = build_fitted_forecast(
            baseline, snapshot, histories, NOW, model=MinutesModel(data=artifact())
        )
        self.assertEqual(len(forecast.players), len(baseline.players))
        coverage = next(row for row in meta["coverage"] if row["player_id"] == 1)
        self.assertEqual(coverage["source"], "statistical_fallback")
        self.assertIn("statistical_fallback_invalid_playing_time_history", coverage["flags"])

    def test_attacking_component_calibration_cannot_change_playing_time(self):
        from fpl_forfeit.assistant.attacking_rates import FEATURES as ATTACKING_FEATURES
        from fpl_forfeit.assistant.attacking_rates import AttackingRateModel
        from fpl_forfeit.forecasting.statistics import player_rates

        data = {
            "version": "attacking-rates-v2",
            "features": list(ATTACKING_FEATURES),
            "models": {
                name: {"intercept": math.log(value), "coefficients": [0] * len(ATTACKING_FEATURES)}
                for name, value in (("goal", 0.5), ("assist", 0.3))
            },
        }
        player = catalogue().players[3]
        original = player_rates(player)
        changed = AttackingRateModel(data=data).rates(player, original)
        self.assertEqual(changed.start, original.start)
        self.assertEqual(changed.starting_minutes, original.starting_minutes)
        self.assertEqual(changed.cameo_probability, original.cameo_probability)
        self.assertAlmostEqual(changed.goal, 0.5)
        self.assertAlmostEqual(changed.assist, 0.3)


if __name__ == "__main__":
    unittest.main()
