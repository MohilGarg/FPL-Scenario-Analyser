from __future__ import annotations

import unittest

from fpl_forfeit.exposures import differential_exposures, effective_exposures
from fpl_forfeit.models import ElementScore
from tests.helpers import manager, players, scores


class ExposureTests(unittest.TestCase):
    def test_shared_player_multiplier_is_preserved(self) -> None:
        pool = players()
        values = scores()
        complete = {player.team_id: True for player in pool.values()}
        managers = (
            manager(1, captain=12),
            manager(2, captain=13),
        )
        exposures = effective_exposures(managers, pool, values, complete)
        self.assertEqual(exposures[12], {1: 2, 2: 1})
        differentials = differential_exposures(exposures, [1, 2])
        self.assertIn(12, differentials)
        self.assertNotIn(2, differentials)  # Identical starting defender exposure.

    def test_pending_captain_has_remaining_double_exposure(self) -> None:
        pool = players()
        values = scores()
        values[12] = ElementScore(0, 0)
        complete = {player.team_id: True for player in pool.values()}
        complete[pool[12].team_id] = False
        exposures = effective_exposures((manager(1, captain=12),), pool, values, complete)
        self.assertEqual(exposures[12][1], 2)


if __name__ == "__main__":
    unittest.main()
