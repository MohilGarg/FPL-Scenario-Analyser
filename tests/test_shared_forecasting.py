from __future__ import annotations

import random
import unittest
from dataclasses import replace
from datetime import UTC, datetime

from fpl_forfeit.forecasting import (
    DataSource,
    FixtureSimulator,
    ForecastInputs,
    TeamMatchResult,
    project_players,
)
from fpl_forfeit.forecasting.models import Condition
from fpl_forfeit.forecasting.statistics import estimate_team_goal_rates
from fpl_forfeit.models import Fixture, Player, Position
from fpl_forfeit.points import event_points


def football_inputs_only() -> ForecastInputs:
    """No league ID, manager, picks, live aggregate or API client is required."""
    roster = {
        1: Player(1, "Keeper", 1, "Home", Position.GOALKEEPER),
        2: Player(2, "Defender", 2, "Away", Position.DEFENDER),
    }
    fixture = Fixture(101, 7, 1, 2, "Home", "Away", False, False)
    return ForecastInputs(
        roster,
        (fixture,),
        datetime(2026, 10, 11, tzinfo=UTC),
        provenance=(DataSource("saved_test_input", "normalised football data"),),
    )


class SharedProjectionTests(unittest.TestCase):
    def test_team_statistics_use_normalised_results_without_api_or_league_data(self):
        results = (TeamMatchResult(1, 2, 3, 0),) * 10
        rates = estimate_team_goal_rates(results)
        self.assertGreater(rates[1][0], rates[2][0])
        self.assertEqual(rates[1][0], rates[2][1])
        self.assertEqual(rates[1][1], rates[2][0])
        self.assertLess(rates[1][0], 3)  # Sparse history is still shrunk to the league mean.
        self.assertEqual(estimate_team_goal_rates(()), {})

    def test_future_horizon_and_doubles_keep_fixture_and_gameweek_identity(self):
        inputs = football_inputs_only()
        first = inputs.fixtures[0]
        inputs = replace(
            inputs,
            fixtures=(
                replace(first, id=103, gameweek=8),
                first,
                replace(first, id=102),
                replace(first, id=99, gameweek=6, finished=True),
            ),
        )
        rows = project_players(inputs, samples=30)
        self.assertEqual({(r.fixture_id, r.gameweek) for r in rows}, {(101, 7), (102, 7), (103, 8)})
        filtered = project_players(inputs, gameweeks=(8,), samples=30)
        self.assertEqual({(r.fixture_id, r.gameweek) for r in filtered}, {(103, 8)})
        self.assertEqual(
            rows, project_players(replace(inputs, fixtures=inputs.fixtures[::-1]), samples=30)
        )
        self.assertEqual(project_players(inputs, gameweeks=(9,), samples=30), ())

    def test_live_projection_keeps_observed_goals_and_separates_remaining_points(self):
        inputs = football_inputs_only()
        fixture = replace(
            inputs.fixtures[0], started=True, elapsed_minutes=60, home_score=1, away_score=0
        )
        observed = {"minutes": 60, "goals_scored": 1, "clean_sheets": 1, "saves": 2}
        inputs = replace(
            inputs,
            fixtures=(fixture,),
            fixture_stats={(1, 101): observed},
            fixture_minutes={(1, 101): 60},
        )
        row = next(row for row in project_players(inputs, samples=80) if row.player_id == 1)
        self.assertGreaterEqual(row.expected_goals, 1)
        self.assertEqual(row.appearance_probability, 1)
        self.assertIsNone(row.starting_probability)
        self.assertGreaterEqual(row.expected_minutes, 60)
        self.assertAlmostEqual(
            row.expected_points - row.expected_remaining_points,
            event_points(Position.GOALKEEPER, observed),
        )

    def test_live_categories_supply_minutes_without_a_duplicate_minutes_mapping(self):
        inputs = football_inputs_only()
        fixture = replace(inputs.fixtures[0], started=True, home_score=1, away_score=0)
        inputs = replace(
            inputs,
            fixtures=(fixture,),
            fixture_stats={(1, 101): {"minutes": 70, "goals_scored": 1}},
        )
        model = FixtureSimulator(inputs)
        self.assertEqual(model.clock[101], 70)
        sample = model.sample(random.Random(8))[0]
        self.assertGreaterEqual(sample.stats[1]["minutes"], 70)
        self.assertTrue(all(event[3] >= 70 for event in sample.events))

    def test_invalid_players_and_duplicate_fixture_ids_fail_explicitly(self):
        inputs = football_inputs_only()
        with self.assertRaisesRegex(ValueError, "player IDs"):
            project_players(inputs, player_ids=(999,), samples=1)
        with self.assertRaisesRegex(ValueError, "unique"):
            project_players(replace(inputs, fixtures=inputs.fixtures * 2), samples=1)
        for count in (0, 5001):
            with self.assertRaises(ValueError):
                project_players(inputs, samples=count)


class StartingMinutesTests(unittest.TestCase):
    def test_season_durations_change_minutes_and_qualified_clean_sheets(self):
        inputs = football_inputs_only()
        defender = inputs.players[2]
        base = {"season_starts": 20, "season_team_matches": 20}
        short = replace(defender, season_minutes=900, **base)
        full = replace(defender, season_minutes=1800, **base)
        short_rows = project_players(
            replace(inputs, players={1: inputs.players[1], 2: short}), samples=1000, seed=8
        )
        full_rows = project_players(
            replace(inputs, players={1: inputs.players[1], 2: full}), samples=1000, seed=8
        )
        short_row, full_row = short_rows[1], full_rows[1]
        self.assertGreater(full_row.expected_minutes - short_row.expected_minutes, 20)
        self.assertEqual(short_row.clean_sheet_probability, 0)
        self.assertGreater(full_row.clean_sheet_probability, 0.05)

    def test_missing_season_minutes_keep_explicit_duration_prior(self):
        inputs = football_inputs_only()
        model, rng = FixtureSimulator(inputs), random.Random(4)
        start_durations = set()
        for _ in range(100):
            sample = model.sample(rng)[0]
            start, end = sample.windows[2]
            if start == 0:
                start_durations.add(end)
        self.assertEqual(start_durations, {55, 60, 70, 80, 90})

    def test_forced_late_goal_preserves_coherent_overlap_for_a_short_duration_player(self):
        inputs = football_inputs_only()
        player = replace(
            inputs.players[2],
            team_id=1,
            season_starts=20,
            season_team_matches=20,
            season_minutes=600,
        )
        inputs = replace(inputs, players={1: inputs.players[1], 2: player})
        model = FixtureSimulator(
            inputs,
            (
                Condition(101, "goal", player_id=2),
                Condition(101, "assist", player_id=1),
                Condition(101, "substitute", player_id=1, value=5),
                Condition(101, "score", home_goals=1, away_goals=0),
            ),
        )
        for seed in range(10):
            sample = model.sample(random.Random(seed))[0]
            self.assertEqual(sample.stats[2]["goals_scored"], 1)
            self.assertEqual(sample.stats[1]["minutes"], 5)
            self.assertEqual(sample.stats[1]["assists"], 1)
            self.assertGreaterEqual(sample.events[0][3], 85)


if __name__ == "__main__":
    unittest.main()
