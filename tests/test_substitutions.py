from __future__ import annotations

import unittest

from fpl_forfeit.models import ElementScore
from fpl_forfeit.substitutions import effective_multipliers, score_manager

from tests.helpers import manager, players, scores


class SubstitutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.players = players()
        self.complete = {player.team_id: True for player in self.players.values()}

    def test_one_minute_blocks_autosub(self) -> None:
        values = scores()
        values[2] = ElementScore(1, 1)
        values[5] = ElementScore(9, 90)
        multipliers = effective_multipliers(
            manager(), self.players, values, self.complete
        )
        self.assertEqual(multipliers[2], 1)
        self.assertEqual(multipliers[5], 0)

    def test_zero_minutes_allows_later_defender_when_first_bench_mid_is_illegal(self) -> None:
        values = scores()
        values[2] = ElementScore(0, 0)
        values[11] = ElementScore(8, 90)  # First outfield bench: would make 2-5-3.
        values[5] = ElementScore(6, 90)   # Second outfield bench: legal defender swap.
        multipliers = effective_multipliers(
            manager(), self.players, values, self.complete
        )
        self.assertEqual(multipliers[2], 0)
        self.assertEqual(multipliers[11], 0)
        self.assertEqual(multipliers[5], 1)

    def test_pending_zero_minutes_does_not_trigger_early_sub(self) -> None:
        values = scores()
        values[2] = ElementScore(0, 0)
        values[5] = ElementScore(6, 90)
        incomplete = dict(self.complete)
        incomplete[self.players[2].team_id] = False
        multipliers = effective_multipliers(manager(), self.players, values, incomplete)
        self.assertEqual(multipliers[2], 1)
        self.assertEqual(multipliers[5], 0)

    def test_goalkeeper_only_replaces_goalkeeper(self) -> None:
        values = scores()
        values[1] = ElementScore(0, 0)
        values[15] = ElementScore(7, 90)
        multipliers = effective_multipliers(
            manager(), self.players, values, self.complete
        )
        self.assertEqual(multipliers[1], 0)
        self.assertEqual(multipliers[15], 1)

    def test_captain_hands_to_vice_only_after_confirmed_absence(self) -> None:
        values = scores()
        values[12] = ElementScore(0, 0)
        values[7] = ElementScore(5, 90)
        multipliers = effective_multipliers(
            manager(captain=12, vice=7), self.players, values, self.complete
        )
        self.assertEqual(multipliers[12], 0)
        self.assertEqual(multipliers[7], 2)

    def test_triple_captain_and_bench_boost(self) -> None:
        values = scores()
        triple = effective_multipliers(
            manager(chip="3xc"), self.players, values, self.complete
        )
        boosted = effective_multipliers(
            manager(chip="bboost"), self.players, values, self.complete
        )
        self.assertEqual(triple[12], 3)
        self.assertTrue(all(value >= 1 for value in boosted.values()))
        self.assertEqual(boosted[12], 2)

    def test_transfer_cost_is_subtracted(self) -> None:
        values = scores(default_points=1)
        plain = score_manager(manager(), self.players, values, self.complete)
        hit = score_manager(
            manager(transfer_cost=4), self.players, values, self.complete
        )
        self.assertEqual(hit, plain - 4)


if __name__ == "__main__":
    unittest.main()

