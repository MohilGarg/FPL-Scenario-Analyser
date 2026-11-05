from __future__ import annotations

import math
import random
import unittest
from dataclasses import replace
from datetime import timedelta

from fpl_forfeit.api import FPLGameweekUnavailable
from fpl_forfeit.assistant.playing_time import (
    MinutesEstimate,
    MinutesSimulator,
    build_minutes_forecast,
    estimate_minutes,
)
from fpl_forfeit.assistant.service import AssistantService
from fpl_forfeit.forecasting.horizon import forecast_horizon
from fpl_forfeit.models import Position
from fpl_forfeit.points import SCORING_VERSIONS, event_points, point_components
from tests.assistant_fixtures import NOW, FixtureClient, catalogue
from tests.test_fixture_components import context


class MinutesStateTests(unittest.TestCase):
    def test_zeroes_are_observations_and_threshold_probability_is_not_mean_minutes(self):
        player = catalogue().players[3]
        regular = estimate_minutes(player, [{"minutes": 90, "starts": 1}] * 8)
        absent = estimate_minutes(player, [{"minutes": 0, "starts": 0}] * 8)
        self.assertGreater(regular.start_probability, absent.start_probability)
        self.assertGreater(absent.no_show_probability, regular.no_show_probability)
        mixed = estimate_minutes(player, [{"minutes": m, "starts": 1} for m in (59, 60) * 4])
        self.assertGreater(mixed.sixty_probability, 0)
        self.assertLess(mixed.sixty_probability, mixed.start_probability)
        self.assertAlmostEqual(sum(mixed.states), 1)
        self.assertAlmostEqual(
            mixed.no_show_probability + mixed.start_probability + mixed.substitute_probability, 1
        )

    def test_official_availability_applied_once_and_horizon_shrinks_recent_evidence(self):
        player = catalogue().players[3]
        history = [{"minutes": 90, "starts": 1}] * 8
        healthy = estimate_minutes(player, history)
        doubtful = estimate_minutes(replace(player, chance_of_playing=25), history)
        self.assertAlmostEqual(doubtful.start_probability, healthy.start_probability / 4)
        self.assertAlmostEqual(doubtful.expected_minutes, healthy.expected_minutes / 4)
        self.assertLess(
            estimate_minutes(player, history, horizon_days=35).start_probability,
            healthy.start_probability,
        )
        self.assertEqual(
            estimate_minutes(replace(player, availability_status="i"), history).expected_minutes, 0
        )

    def test_invalid_or_unknown_observations_are_not_silently_zeroes(self):
        for row in (
            {"minutes": None, "starts": 0},
            {"minutes": 20, "starts": None},
            {"minutes": 0, "starts": 1},
            {"minutes": math.nan, "starts": 0},
        ):
            with self.assertRaises(ValueError):
                estimate_minutes(catalogue().players[3], [row])

    def test_double_gameweek_uses_one_availability_draw_not_independent_leg_injuries(self):
        baseline, _, _ = context()
        player = replace(baseline.catalogue.players[1], position=Position.DEFENDER)
        baseline = forecast_horizon(
            replace(baseline.catalogue, players={1: player}), (7,), samples=4
        )
        estimate = MinutesEstimate((0, 0, 0, 1), 0.5, (90,), (10,), 10, 0)
        model = MinutesSimulator(
            baseline.inputs, {(f.id, 1): estimate for f in baseline.inputs.fixtures}
        )
        rng = random.Random(12)
        played = []
        for _ in range(80):
            draws = model.sample(rng)
            self.assertEqual(draws[0].stats[1]["minutes"], draws[1].stats[1]["minutes"])
            played.append(draws[0].stats[1]["minutes"] > 0)
        self.assertTrue(any(played) and not all(played))

    def test_cutoff_and_history_fallback_and_private_selection(self):
        baseline, snapshot, histories = context()
        result, meta = build_minutes_forecast(baseline, snapshot, histories, NOW)
        self.assertEqual(result.inputs.model_version, "minutes-states-v1")
        self.assertEqual(meta["coverage"][0]["source"], "minutes_states")
        self.assertEqual(meta["coverage"][1]["source"], "statistical_fallback")
        histories[1]["retrieved_at"] = (NOW + timedelta(hours=1)).isoformat()
        _, meta = build_minutes_forecast(baseline, snapshot, histories, NOW)
        self.assertEqual(meta["coverage"][0]["source"], "statistical_fallback")
        with self.assertRaisesRegex(ValueError, "pre-deadline"):
            build_minutes_forecast(baseline, snapshot, histories, NOW + timedelta(days=2))
        service = AssistantService(FixtureClient(), samples=4, now=lambda: NOW)
        default = service.forecast()
        with self.assertRaises(FPLGameweekUnavailable), service.using_model("minutes"):
            service.forecast()
        service.ml.generation = (default.catalogue.fetched_at.isoformat(), default.gameweeks)
        service.ml.minutes_forecast = result
        with service.using_model("minutes"):
            self.assertIs(service.forecast(), result)
        self.assertIs(service.forecast(), default)

    def test_distribution_metrics_and_components_reconcile_double_gameweek_draws(self):
        forecast, _, _ = context()
        for pid, weekly in forecast.players.items():
            for gw, row in weekly.items():
                if row["expected_points"] is None:
                    continue
                self.assertAlmostEqual(
                    sum(row["expected_points_components"].values()), row["expected_points"]
                )
                for name in (
                    "probability_60_minutes",
                    "starting_probability",
                    "no_show_probability",
                    "blank_probability",
                    "haul_probability",
                ):
                    self.assertTrue(0 <= row[name] <= 1, name)
                self.assertEqual(
                    row["no_show_probability"],
                    sum(m == 0 for m in forecast.outcomes[gw][pid].minutes) / forecast.samples,
                )
        self.assertEqual(forecast.players[1][8]["no_show_probability"], 1)
        self.assertEqual(forecast.players[1][8]["haul_probability"], 0)


class VersionedScoringTests(unittest.TestCase):
    def test_historical_rules_are_explicit_and_current_thresholds_are_per_fixture(self):
        self.assertEqual(
            event_points(Position.GOALKEEPER, {"goals_scored": 1}, season="2023-24"), 6
        )
        self.assertEqual(
            event_points(Position.GOALKEEPER, {"goals_scored": 1}, season="2024-25"), 10
        )
        self.assertEqual(event_points(Position.GOALKEEPER, {"goals_scored": 1}), 10)
        self.assertEqual(
            event_points(Position.DEFENDER, {"defensive_contribution": 20}, season="2024-25"), 0
        )
        for position, threshold in (
            (Position.DEFENDER, 10),
            (Position.MIDFIELDER, 12),
            (Position.FORWARD, 12),
        ):
            self.assertEqual(event_points(position, {"defensive_contribution": threshold - 1}), 0)
            self.assertEqual(event_points(position, {"defensive_contribution": threshold * 2}), 2)
        self.assertEqual(event_points(Position.DEFENDER, {"minutes": 59, "clean_sheets": 1}), 1)
        self.assertEqual(event_points(Position.DEFENDER, {"minutes": 60, "clean_sheets": 1}), 6)
        with self.assertRaises(KeyError):
            event_points(Position.DEFENDER, {}, season="2027-28")

    def test_component_terms_equal_shared_scorer_in_every_supported_season(self):
        rng = random.Random(72)
        for season in SCORING_VERSIONS:
            for position in Position:
                for _ in range(40):
                    stats = {
                        k: rng.randrange(4)
                        for k in (
                            "goals_scored",
                            "assists",
                            "goals_conceded",
                            "saves",
                            "penalties_saved",
                            "penalties_missed",
                            "yellow_cards",
                            "red_cards",
                            "own_goals",
                            "bonus",
                        )
                    }
                    stats.update(
                        minutes=rng.choice((0, 1, 59, 60, 90)),
                        clean_sheets=rng.randrange(2),
                        defensive_contribution=rng.randrange(25),
                    )
                    self.assertEqual(
                        sum(point_components(position, stats, season=season).values()),
                        event_points(position, stats, season=season),
                    )


if __name__ == "__main__":
    unittest.main()
