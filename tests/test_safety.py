from __future__ import annotations

import unittest

from fpl_forfeit.models import Fixture
from fpl_forfeit.safety import assess_safety, conservative_score_bounds
from fpl_forfeit.settings import (
    DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION,
    DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION,
)
from tests.helpers import league_state, manager, scores


class SafetyTests(unittest.TestCase):
    def test_manager_is_safe_when_someone_is_guaranteed_below(self) -> None:
        managers = (manager(1), manager(2))
        state = league_state(managers, scores())
        assessments = assess_safety(state, {1: 50, 2: 10})
        self.assertTrue(assessments[1].safe)
        self.assertFalse(assessments[2].safe)

    def test_strict_last_treats_guaranteed_tie_as_safe(self) -> None:
        state = league_state((manager(1), manager(2)), scores())
        tied_counts = assess_safety(state, {1: 50, 2: 50}, allow_tied_last=True)
        strictly_lowest = assess_safety(state, {1: 50, 2: 50}, allow_tied_last=False)
        self.assertFalse(tied_counts[1].safe)
        self.assertTrue(strictly_lowest[1].safe)

    def test_default_bounds_are_minus_ten_and_plus_thirty_five(self) -> None:
        self.assertEqual(DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION, -10)
        self.assertEqual(DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION, 35)

    def test_double_gameweek_uses_one_total_player_contribution_bound(self) -> None:
        values = scores()
        fixtures = (
            Fixture(1, 1, 2, 98, "Team 2", "Other A", False, False),
            Fixture(2, 1, 99, 2, "Other B", "Team 2", False, False),
        )
        state = league_state((manager(1),), values, fixtures)
        bounds = conservative_score_bounds(state, {1: 50})[1]
        self.assertEqual(bounds.lower, 40)
        self.assertEqual(bounds.upper, 85)

    def test_captain_multiplier_applies_to_total_gameweek_bound_once(self) -> None:
        fixtures = (
            Fixture(1, 1, 12, 98, "Team 12", "Other A", False, False),
            Fixture(2, 1, 99, 12, "Other B", "Team 12", False, False),
        )
        state = league_state((manager(1, captain=12),), scores(), fixtures)
        bounds = conservative_score_bounds(state, {1: 50})[1]
        self.assertEqual(bounds.lower, 30)
        self.assertEqual(bounds.upper, 120)


if __name__ == "__main__":
    unittest.main()
