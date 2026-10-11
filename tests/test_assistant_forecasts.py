from __future__ import annotations

import unittest
from dataclasses import replace
from statistics import pvariance

from fpl_forfeit.api import parse_forecast_catalogue, parse_public_squad
from fpl_forfeit.forecasting.horizon import forecast_horizon
from fpl_forfeit.forecasting.models import DEFAULT_SEED
from fpl_forfeit.forecasting.projections import project_players
from tests.assistant_fixtures import catalogue


class FutureForecastTests(unittest.TestCase):
    def test_double_gameweek_aggregates_joint_points_minutes_and_uncertainty(self):
        base = catalogue()
        first = replace(base.fixtures[0], id=100, gameweek=7, home_team_id=1, away_team_id=2)
        second = replace(base.fixtures[1], id=101, gameweek=7, home_team_id=1, away_team_id=3)
        forecast = forecast_horizon(replace(base, fixtures=(first, second)), (7, 8), samples=12)
        row = forecast.players[1][7]
        self.assertEqual(row["fixture_ids"], [100, 101])
        projected = [p for p in forecast.fixtures if p.player_id == 1]
        self.assertAlmostEqual(row["expected_points"], sum(p.expected_points for p in projected))
        self.assertAlmostEqual(row["expected_minutes"], sum(p.expected_minutes for p in projected))
        self.assertEqual(row["points_variance"], pvariance(forecast.outcomes[7][1].points))
        self.assertEqual(forecast.players[1][8]["coverage"], "confirmed_blank")
        self.assertEqual(forecast.players[1][8]["expected_points"], 0)

    def test_missing_and_unassigned_fixtures_are_not_confirmed_blanks(self):
        base = catalogue()
        absent = forecast_horizon(
            replace(base, fixtures=(), schedule_complete=False), (7,), samples=4
        )
        self.assertIsNone(absent.players[1][7]["expected_points"])
        partial = forecast_horizon(
            replace(base, unassigned_team_ids=frozenset({1})), (7,), samples=4
        )
        self.assertEqual(partial.players[1][7]["coverage"], "partial")
        self.assertIsNone(partial.players[1][7]["expected_points"])
        self.assertIsNotNone(partial.players[1][7]["published_fixture_points"])
        missing = forecast_horizon(
            replace(base, missing_schedule_team_ids=frozenset({1})), (7,), samples=4
        )
        self.assertEqual(missing.players[1][7]["coverage"], "partial")
        self.assertIsNone(missing.players[1][7]["expected_points"])

    def test_horizon_reuses_shared_fixture_projections_exactly(self):
        forecast = forecast_horizon(catalogue(), (7, 8), samples=8)
        for gw in forecast.gameweeks:
            expected = project_players(
                forecast.inputs, gameweeks=(gw,), samples=8, seed=DEFAULT_SEED + gw
            )
            actual = tuple(p for p in forecast.fixtures if p.gameweek == gw)
            self.assertEqual(actual, expected)
            self.assertTrue(all(p.expected_defensive_points is not None for p in actual))

    def test_started_fixtures_cannot_be_silently_reforecast_as_future(self):
        base = catalogue()
        with self.assertRaisesRegex(ValueError, "have not started"):
            forecast_horizon(
                replace(base, fixtures=(replace(base.fixtures[0], started=True),)),
                (base.fixtures[0].gameweek,),
                samples=4,
            )

    def test_official_catalogue_normalisation_and_schedule_completeness(self):
        bootstrap = {
            "teams": [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}],
            "elements": [
                {
                    "id": 1,
                    "web_name": "A",
                    "team": 1,
                    "element_type": 2,
                    "minutes": 90,
                    "starts": 1,
                    "chance_of_playing_this_round": 100,
                    "chance_of_playing_next_round": 25,
                }
            ],
            "events": [{"id": 7, "deadline_time": "2026-10-12T12:00:00Z"}],
            "element_types": [
                {"id": 2, "squad_select": 5, "squad_min_play": 3, "squad_max_play": 5}
            ],
            "game_settings": {"squad_team_limit": 3, "max_extra_free_transfers": 4},
        }
        fixtures = [
            {"id": 1, "event": 7, "team_h": 1, "team_a": 2},
            {"id": 2, "event": None, "team_h": 2, "team_a": 1},
        ]
        parsed = parse_forecast_catalogue(bootstrap, fixtures)
        self.assertTrue(parsed.schedule_complete)
        self.assertEqual(parsed.unassigned_team_ids, frozenset({1, 2}))
        self.assertEqual(parsed.players[1].chance_of_playing, 25)
        self.assertEqual(parsed.maximum_free_transfers, 5)
        self.assertEqual(len(parsed.fixtures), 1)
        self.assertFalse(parse_forecast_catalogue(bootstrap, fixtures[:1]).schedule_complete)

    def test_public_squad_unavailable_fields_remain_unknown_and_repeated_chips_retained(self):
        picks = [
            {"element": i, "position": i, "is_captain": i == 1, "is_vice_captain": i == 2}
            for i in range(1, 16)
        ]
        result = parse_public_squad(
            42, 6, {"name": "Private fixture"}, {"picks": picks, "entry_history": {"bank": 0}}, None
        )
        self.assertEqual(result.bank_tenths, 0)
        self.assertIsNone(result.value_tenths)
        self.assertIsNone(result.chips_used)
        self.assertTrue(all(value is None for value in result.selling_prices.values()))
        history = {"chips": [{"name": "wildcard", "event": 3}, {"name": "wildcard", "event": 22}]}
        repeated = parse_public_squad(42, 23, {}, {"picks": picks}, history)
        self.assertEqual(repeated.chips_used, (("wildcard", 3), ("wildcard", 22)))
