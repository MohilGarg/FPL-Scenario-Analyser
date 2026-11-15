from __future__ import annotations

import random
import unittest
from dataclasses import replace

from fpl_forfeit.forecasting.models import Condition, ScenarioConflict
from fpl_forfeit.models import Position
from fpl_forfeit.scoring import event_points
from tests.simulator_helpers import FixtureModel, fixture_state


class ConditionalRegressionTests(unittest.TestCase):
    def test_goal_and_late_assist_never_fail_randomly(self):
        state = fixture_state(1)
        conditions = (
            Condition(1, "goal", player_id=7),
            Condition(1, "assist", player_id=13),
            Condition(1, "substitute", player_id=13, value=20),
            Condition(1, "score", home_goals=1, away_goals=0),
        )
        model = FixtureModel(state, conditions)
        for seed in range(250):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertEqual(sample.home_goals, 1)
            self.assertEqual(sample.stats[13]["minutes"], 20)
            self.assertEqual(sample.stats[13]["assists"], 1)
            self.assertGreater(sample.windows[7][1], sample.windows[13][0])

    def test_disjoint_forced_windows_are_rejected_before_sampling(self):
        with self.assertRaisesRegex(ScenarioConflict, "overlapping"):
            FixtureModel(
                fixture_state(1),
                (
                    Condition(1, "goal", player_id=7),
                    Condition(1, "minutes", player_id=7, value=55),
                    Condition(1, "assist", player_id=13),
                    Condition(1, "substitute", player_id=13, value=20),
                    Condition(1, "score", home_goals=1, away_goals=0),
                ),
            )

    def test_zero_minute_disciplinary_appearance_cannot_be_erased_by_dnp(self):
        state = fixture_state(1)
        state.fixture_stats[(12, 1)] = {"minutes": 0, "red_cards": 1}
        with self.assertRaisesRegex(ScenarioConflict, "participate"):
            FixtureModel(state, (Condition(1, "dnp", player_id=12),))

    def test_substitution_limit_applies_separately_to_each_team(self):
        conditions = tuple(
            Condition(1, "substitute", player_id=pid, value=20) for pid in (3, 5, 7, 2, 4, 6)
        )
        model = FixtureModel(fixture_state(1), conditions)
        sample = model.draw(random.Random(9))[1][0]
        self.assertTrue(all(sample.stats[pid]["minutes"] == 20 for pid in (3, 5, 7, 2, 4, 6)))

    def test_keeper_substitutions_have_nonoverlapping_windows(self):
        model = FixtureModel(
            fixture_state(1),
            (
                Condition(1, "minutes", player_id=1, value=70),
                Condition(1, "substitute", player_id=15, value=20),
            ),
        )
        for seed in range(40):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertEqual(sample.windows[1], (0, 70))
            self.assertEqual(sample.windows[15], (70, 90))

    def test_fully_owned_on_field_team_has_no_anonymous_goalscorer(self):
        state = fixture_state(1)
        for pid in range(1, 12):
            state.players[pid] = replace(state.players[pid], team_id=1)
        conditions = tuple(Condition(1, "minutes", player_id=pid, value=90) for pid in range(1, 12))
        model = FixtureModel(
            state, conditions + (Condition(1, "score", home_goals=2, away_goals=0),)
        )
        for seed in range(50):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertTrue(all(e[1] is not None for e in sample.events))
            self.assertEqual(sum(s["goals_scored"] for s in sample.stats.values()), 2)

    def test_saves_awarded_only_to_goalkeepers(self):
        self.assertEqual(event_points(Position.GOALKEEPER, {"minutes": 90, "saves": 6}), 4)
        for position in (Position.DEFENDER, Position.MIDFIELDER, Position.FORWARD):
            self.assertEqual(event_points(position, {"minutes": 90, "saves": 6}), 2)
