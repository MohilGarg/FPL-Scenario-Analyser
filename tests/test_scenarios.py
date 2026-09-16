from __future__ import annotations

import unittest

from fpl_forfeit.models import ElementScore, Fixture, Player, Position
from fpl_forfeit.scenarios import ScenarioVariable, football_consistent, outcome_catalog


class ScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(99, 1, 1, 2, "Home", "Away", False, False)
        self.defender = Player(1, "Home Defender", 1, "Home", Position.DEFENDER)
        self.forward = Player(2, "Away Forward", 2, "Away", Position.FORWARD)

    def test_blank_has_appearance_points(self) -> None:
        outcomes = outcome_catalog(self.forward, self.fixture, ElementScore(0, 0))
        baseline = next(outcome for outcome in outcomes if outcome.baseline)
        self.assertEqual(baseline.points_delta, 2)
        self.assertEqual(baseline.minutes_delta, 60)

    def test_opposing_goal_and_clean_sheet_are_rejected(self) -> None:
        defender_outcomes = outcome_catalog(self.defender, self.fixture, ElementScore(0, 0))
        forward_outcomes = outcome_catalog(self.forward, self.fixture, ElementScore(0, 0))
        clean_sheet = next(
            outcome for outcome in defender_outcomes if outcome.label == "keeps a clean sheet"
        )
        goal = next(outcome for outcome in forward_outcomes if outcome.label == "scores")
        variables = (
            ScenarioVariable(self.defender, self.fixture, defender_outcomes),
            ScenarioVariable(self.forward, self.fixture, forward_outcomes),
        )
        self.assertFalse(football_consistent((clean_sheet, goal), variables))

    def test_live_defender_loses_provisional_clean_sheet_points(self) -> None:
        live_fixture = Fixture(
            100,
            1,
            1,
            2,
            "Home",
            "Away",
            True,
            False,
            home_score=0,
            away_score=0,
        )
        outcomes = outcome_catalog(self.defender, live_fixture, ElementScore(6, 70))
        conceded = next(outcome for outcome in outcomes if "loses the clean sheet" in outcome.label)
        self.assertEqual(conceded.points_delta, -4)

    def test_live_player_below_sixty_gains_second_appearance_point(self) -> None:
        live_fixture = Fixture(
            101,
            1,
            1,
            2,
            "Home",
            "Away",
            True,
            False,
            home_score=1,
            away_score=1,
        )
        outcomes = outcome_catalog(self.forward, live_fixture, ElementScore(1, 50))
        baseline = next(outcome for outcome in outcomes if outcome.baseline)
        self.assertEqual(baseline.points_delta, 1)
        self.assertEqual(baseline.minutes_delta, 10)

    def test_common_multi_return_outcomes_are_bounded_but_available(self) -> None:
        outcomes = outcome_catalog(self.forward, self.fixture, ElementScore(0, 0))
        labels = {outcome.label for outcome in outcomes}
        self.assertIn("scores twice", labels)
        self.assertIn("scores a hat-trick", labels)
        self.assertIn("gets two assists", labels)
        self.assertLess(len(outcomes), 15)


if __name__ == "__main__":
    unittest.main()
