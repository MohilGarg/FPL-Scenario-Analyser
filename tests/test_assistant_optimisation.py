from __future__ import annotations

import unittest
from itertools import combinations, permutations

from fpl_forfeit.assistant.optimisation import LineupOptimiser, make_lineup, validate_squad
from fpl_forfeit.forecasting.horizon import OutcomeSamples
from fpl_forfeit.models import ElementScore, Position
from fpl_forfeit.substitutions import score_manager
from tests.assistant_fixtures import outcomes, players, snapshot


class LineupOptimisationTests(unittest.TestCase):
    def setUp(self):
        self.players = players()
        self.outcomes = outcomes()
        self.ids = tuple(self.players)

    def test_known_analytical_optimum_and_bench_order(self):
        result = LineupOptimiser(self.players, self.outcomes, self.ids).optimise()
        self.assertEqual(result["formation"], "3-4-3")
        self.assertEqual(result["expected_points"], 119)
        self.assertEqual(result["captain"], 15)
        self.assertEqual(result["vice_captain"], 14)
        self.assertEqual(result["bench_ids"], [1, 8, 4, 3])
        self.assertEqual(result["points_interval_80"], [119, 119])

    def test_matches_brute_force_legal_xis_for_certain_appearances(self):
        scores = {pid: ElementScore(pid, 90) for pid in self.ids}
        best = float("-inf")
        for starters in combinations(self.ids, 11):
            reserve = tuple(pid for pid in self.ids if pid not in starters)
            goalkeepers = [
                pid for pid in reserve if self.players[pid].position == Position.GOALKEEPER
            ]
            if len(goalkeepers) != 1:
                continue
            bench = (goalkeepers[0],) + tuple(pid for pid in reserve if pid != goalkeepers[0])
            ordered = sorted(starters, reverse=True)
            try:
                manager = make_lineup(
                    self.ids, self.players, starters, bench, ordered[0], ordered[1]
                )
            except ValueError:
                continue
            best = max(
                best, score_manager(manager, self.players, scores, {i: True for i in range(1, 9)})
            )
        self.assertEqual(
            LineupOptimiser(self.players, self.outcomes, self.ids).optimise()["expected_points"],
            best,
        )

    def test_captain_takeover_can_beat_highest_individual_mean(self):
        uncertain = {pid: OutcomeSamples((10,) * 4, (90,) * 4, bytes([1] * 4)) for pid in self.ids}
        uncertain[15] = OutcomeSamples((100, 100, 100, 0), (90, 90, 90, 0), bytes([1, 1, 1, 0]))
        uncertain[12] = OutcomeSamples((80,) * 4, (90,) * 4, bytes([1] * 4))
        result = LineupOptimiser(self.players, uncertain, self.ids).optimise()
        self.assertEqual(result["captain"], 15)
        self.assertEqual(result["vice_captain"], 12)
        self.assertEqual(result["expected_captaincy_extra"], 95)
        self.assertEqual(result["vice_takeover_probability"], 0.25)
        triple = LineupOptimiser(self.players, uncertain, self.ids).optimise("3xc")
        self.assertEqual(triple["expected_captaincy_extra"], 190)

    def test_grouped_autosub_scores_reconcile_with_shared_scorer_for_all_bench_orders(self):
        squad = snapshot()
        starters = tuple(p.player_id for p in squad.manager.picks[:11])
        uncertain = dict(self.outcomes)
        uncertain[3] = OutcomeSamples((0, 3), (0, 90), bytes([0, 1]))
        uncertain[4] = OutcomeSamples((0, -1), (0, 0), bytes([0, 1]))
        uncertain[13] = OutcomeSamples((0, 13), (0, 90), bytes([0, 1]))
        optimiser = LineupOptimiser(self.players, uncertain, self.ids)
        for ordering in permutations((6, 7, 15)):
            bench = (2,) + ordering
            evaluation = optimiser.evaluate(starters, bench, 13, 14)
            self.assertAlmostEqual(
                optimiser._base_score(starters, bench, None) + optimiser.captain_pairs[13, 14],
                evaluation["expected_points"],
            )
        result = optimiser.optimise()
        self.assertEqual(len(result["starting_ids"]), 11)

    def test_invalid_manual_xi_and_captain_are_rejected(self):
        picks = snapshot().manager.picks
        starters = tuple(p.player_id for p in picks[:11])
        bench = tuple(p.player_id for p in picks[11:])
        with self.assertRaisesRegex(ValueError, "at least three defenders"):
            make_lineup(
                self.ids,
                self.players,
                tuple(15 if p == 3 else p for p in starters),
                (2, 6, 7, 3),
                13,
                14,
            )
        with self.assertRaisesRegex(ValueError, "different starting"):
            make_lineup(self.ids, self.players, starters, bench, 15, 14)
        with self.assertRaisesRegex(ValueError, "reserve goalkeeper"):
            make_lineup(self.ids, self.players, starters, (6, 2, 7, 15), 13, 14)

    def test_bench_boost_includes_all_fifteen_and_only_one_extra_captain(self):
        result = LineupOptimiser(self.players, self.outcomes, self.ids).optimise("bboost")
        self.assertEqual(result["expected_points"], sum(self.ids) + 15)
        self.assertEqual(result["expected_base_points"], sum(self.ids))

    def test_zero_minute_card_is_not_captain_appearance(self):
        data = dict(self.outcomes)
        data[13] = OutcomeSamples((-1, -1), (0, 0), bytes([1, 1]))
        picks = snapshot().manager.picks
        result = LineupOptimiser(self.players, data, self.ids).evaluate(
            tuple(p.player_id for p in picks[:11]), tuple(p.player_id for p in picks[11:]), 13, 14
        )
        self.assertEqual(result["vice_takeover_probability"], 1)
        self.assertEqual(result["expected_captaincy_extra"], 14)

    def test_club_limit_is_enforced_for_changed_squads(self):
        from dataclasses import replace

        illegal = {pid: replace(p, team_id=1) if pid <= 4 else p for pid, p in self.players.items()}
        with self.assertRaisesRegex(ValueError, "club limit"):
            validate_squad(self.ids, illegal, club_limit=3)
