from __future__ import annotations

import json
import random
import unittest
from dataclasses import fields, replace
from datetime import UTC, datetime
from statistics import fmean

from fpl_forfeit.forecast import FixtureModel, football_inputs, outlook, projection_payload
from fpl_forfeit.forecasting import (
    DataSource,
    FixtureSimulator,
    ForecastInputs,
    TeamMatchResult,
    project_players,
)
from fpl_forfeit.forecasting.models import MODEL_VERSION, Condition
from fpl_forfeit.forecasting.statistics import estimate_team_goal_rates
from fpl_forfeit.models import ElementScore, Fixture, Player, Position
from fpl_forfeit.points import event_points
from tests.test_forecast import fixture_state


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

    def test_standalone_contract_and_provenance_without_a_league(self):
        inputs = football_inputs_only()
        rows = project_players(inputs, samples=100, seed=19)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.samples == 100 for row in rows))
        self.assertTrue(all(row.model_version == MODEL_VERSION for row in rows))
        self.assertTrue(all(row.data_timestamp == inputs.fetched_at for row in rows))
        self.assertTrue(all(row.provenance == inputs.provenance for row in rows))
        for row in rows:
            self.assertGreaterEqual(row.expected_minutes, 0)
            self.assertLessEqual(row.expected_minutes, 90)
            self.assertLessEqual(row.starting_probability, row.appearance_probability)
            self.assertGreaterEqual(row.points_variance, 0)
            self.assertEqual(row.expected_points, row.expected_remaining_points)
        self.assertNotIn("managers", {field.name for field in fields(ForecastInputs)})
        self.assertEqual(rows, project_players(inputs, samples=100, seed=19))
        self.assertEqual(
            json.loads(json.dumps(projection_payload(rows[0])))["provenance"],
            [{"name": "saved_test_input", "detail": "normalised football data"}],
        )

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

    def test_missing_live_data_is_null_and_existing_official_totals_are_frozen(self):
        state = fixture_state(1)
        state.fixtures = (replace(state.fixtures[0], started=True, elapsed_minutes=70),)
        state.live_scores[12] = ElementScore(9, 70)
        result = outlook(state, samples=20)
        row = next(row for row in result["player_projections"] if row["player_id"] == 12)
        self.assertEqual(row["coverage"], "missing_live_breakdown")
        for field in (
            "expected_points",
            "expected_remaining_points",
            "expected_minutes",
            "appearance_probability",
            "starting_probability",
            "expected_goals",
            "expected_assists",
            "clean_sheet_probability",
            "points_variance",
        ):
            self.assertIsNone(row[field])
        self.assertEqual(row["samples"], 0)
        values, _ = FixtureModel(state).draw(random.Random(1))
        self.assertEqual(values[12], state.live_scores[12])

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

    def test_player_projections_agree_with_public_draws_and_ignore_manager_policy(self):
        state = fixture_state(2)
        owned = tuple(sorted({p.player_id for m in state.managers for p in m.picks}))
        expected = project_players(football_inputs(state), player_ids=owned, samples=60, seed=3)
        first = outlook(state, samples=60, seed=3)
        self.assertEqual(first["player_projections"], [projection_payload(r) for r in expected])
        altered = replace(
            state,
            league_id=999,
            managers=tuple(
                replace(
                    m,
                    transfer_cost=20,
                    active_chip="bboost",
                    picks=tuple(
                        replace(p, is_captain=p.player_id == 1, is_vice_captain=p.player_id == 2)
                        for p in m.picks
                    ),
                )
                for m in state.managers
            ),
        )
        second = outlook(altered, samples=60, seed=3, allow_tied_last=False)
        self.assertEqual(first["player_projections"], second["player_projections"])
        self.assertNotEqual(first["managers"], second["managers"])

    def test_completed_dgw_fixture_is_excluded_and_event_points_stay_authoritative(self):
        state = fixture_state(1)
        state.fixtures = (
            replace(state.fixtures[0], finished=True, started=True),
            replace(state.fixtures[0], id=2),
        )
        state.live_scores = {pid: ElementScore(11, 90) for pid in state.players}
        model, rng = FixtureModel(state), random.Random(4)
        samples = [model.draw(rng)[0][12].points for _ in range(100)]
        result = outlook(state, samples=100, seed=4)
        self.assertEqual({r["fixture_id"] for r in result["player_projections"]}, {2})
        row = next(r for r in result["player_projections"] if r["player_id"] == 12)
        self.assertAlmostEqual(11 + row["expected_remaining_points"], fmean(samples))
        self.assertEqual(
            outlook(replace(state, event_final=True), samples=10)["player_projections"], []
        )

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
