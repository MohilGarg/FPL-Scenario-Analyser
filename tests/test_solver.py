from __future__ import annotations

import unittest

from fpl_forfeit.models import Fixture
from fpl_forfeit.solver import solve_candidate

from tests.helpers import league_state, manager, scores


class SolverTests(unittest.TestCase):
    def test_captain_differential_return_can_make_other_manager_last(self) -> None:
        candidate = manager(1, captain=13)
        opponent = manager(2, captain=12, transfer_cost=4)
        values = scores(default_points=0, default_minutes=90)
        fixture = Fixture(88, 1, 12, 1, "Team 12", "Team 1", False, False)
        state = league_state((candidate, opponent), values, (fixture,))
        result = solve_candidate(
            state,
            candidate,
            limit=2,
            max_relevant=1,
            max_nodes=100,
        )
        self.assertTrue(result.scenarios)
        self.assertTrue(
            any(
                outcome.player_id == 12
                and ("scores" in outcome.label or "assist" in outcome.label)
                for outcome in result.scenarios[0].outcomes
            )
        )
        scenario = result.scenarios[0]
        self.assertLessEqual(scenario.final_scores[1], scenario.final_scores[2])


if __name__ == "__main__":
    unittest.main()
