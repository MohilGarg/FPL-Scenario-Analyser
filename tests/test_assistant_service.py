from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

from fpl_forfeit.assistant.service import AssistantService
from fpl_forfeit.forecasting.horizon import OutcomeSamples, forecast_horizon
from tests.assistant_fixtures import NOW, FixtureClient, catalogue, snapshot


class AssistantPlanningTests(unittest.TestCase):
    def setUp(self):
        self.client = FixtureClient()
        self.service = AssistantService(self.client, samples=4, now=lambda: NOW)
        source = catalogue()
        roster = dict(source.players)
        for pid, template, price in ((16, 3, 70), (17, 8, 55), (18, 13, 60)):
            roster[pid] = replace(
                roster[template],
                id=pid,
                name=f"Candidate {pid}",
                team_id=8,
                team_name="Club 8",
                price_tenths=price,
            )
        self.forecast = forecast_horizon(
            replace(source, players=roster), tuple(range(7, 13)), samples=4
        )
        for gw in self.forecast.gameweeks:
            self.forecast.outcomes[gw] = {
                pid: OutcomeSamples((pid,) * 4, (90,) * 4, bytes([1] * 4)) for pid in roster
            }
            for pid in roster:
                self.forecast.players[pid][gw]["expected_points"] = float(pid)
        self.service.forecasts.set(0, self.forecast)
        self.service.load_squad("one", 42)

    def test_unknown_account_values_and_session_isolation(self):
        squad = self.service.squad("one")
        self.assertIsNone(squad["free_transfers"])
        self.assertTrue(all(p["selling_price_tenths"] is None for p in squad["players"]))
        with self.assertRaisesRegex(ValueError, "Load your public squad"):
            self.service.squad("two")
        self.service.lineup("one")
        self.assertIsNone(self.service.plans.get("two"))
        self.service.clear_session("one")
        self.assertIsNone(self.service.squads.get("one"))
        self.assertIsNone(self.service.plans.get("one"))

    def test_horizon_and_lineup_switching_share_forecast_and_plan_caches(self):
        service = AssistantService(self.client, samples=4, now=lambda: NOW)
        service.predictions(1)
        service.predictions(6)
        service.load_squad("one", 42)
        first = service.optimal("one", tuple(range(1, 16)), 7)
        self.assertIs(first, service.optimal("one", tuple(reversed(range(1, 16))), 7))
        self.assertEqual(self.client.catalogue_calls, 1)
        service.refresh()
        self.assertIsNone(service.plans.get("one"))
        service.predictions(3)
        self.assertEqual(self.client.catalogue_calls, 2)

    def test_deadline_invalidates_cached_forecast(self):
        self.service.now = lambda: NOW + timedelta(days=2)
        forecast = self.service.forecast()
        self.assertEqual(forecast.gameweeks[0], 8)
        self.assertEqual(self.client.catalogue_calls, 1)

    def test_loaded_and_manual_lineups_use_same_joint_scoring(self):
        result = self.service.lineup("one", 12)
        changed = self.service.lineup("one", 12, draft=result["recommended"])
        self.assertEqual(changed["expected_gain"], 0)
        self.assertEqual(len(result["player_projections"]), 15)
        self.assertIn(12, result["gameweek_deadlines"])

    def test_budget_uses_actual_selling_not_market_price(self):
        with self.assertRaisesRegex(ValueError, "actual selling price"):
            self.service.transfers("one", [(3, 16)], 20, 1, {})
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.service.transfers("one", [(3, 16)], 17, 1, {3: 51})
        plan = self.service.transfers("one", [(3, 16)], 20, 1, {3: 51})
        self.assertEqual(plan["selling_proceeds_tenths"], 51)
        self.assertEqual(plan["purchase_cost_tenths"], 70)
        self.assertEqual(plan["bank_after_tenths"], 1)
        self.assertEqual(plan["hit"], 0)
        self.assertGreater(plan["gain_next"], 0)

    def test_hits_are_once_and_free_transfers_follow_published_cap(self):
        plan = self.service.transfers("one", [(3, 16), (8, 17)], 100, 1, {3: 51, 8: 55})
        self.assertEqual(plan["hit"], 4)
        self.assertEqual([row["hit"] for row in plan["gameweeks"]], [4, 0, 0, 0, 0, 0])
        first = plan["gameweeks"][0]
        self.assertEqual(first["net_gain"], first["proposed"] - first["baseline"] - 4)
        self.assertEqual(plan["gain_3"], sum(row["net_gain"] for row in plan["gameweeks"][:3]))
        self.assertEqual(self.service.transfers("one", [], 0, 5, {})["gain_5"], 0)
        with self.assertRaisesRegex(ValueError, "free transfers"):
            self.service.transfers("one", [], 0, 6, {})

    def test_transfer_legality_position_duplicates_and_club_limit(self):
        for moves in ([(3, 17)], [(3, 16), (4, 16)], [(3, 16), (3, 18)], [(16, 3)], [(3, 4)]):
            with self.assertRaises(ValueError):
                self.service.transfers("one", moves, 100, 5, {3: 50, 4: 50, 16: 50})
        players = self.forecast.catalogue.players
        players[5] = replace(players[5], team_id=1)
        players[16] = replace(players[16], team_id=1)
        with self.assertRaisesRegex(ValueError, "club limit"):
            self.service.transfers("one", [(3, 16)], 100, 1, {3: 50})

    def test_unknown_fixture_coverage_prevents_complete_gains(self):
        self.forecast.players[16][8]["expected_points"] = None
        plan = self.service.transfers("one", [(3, 16)], 100, 1, {3: 50})
        self.assertIsNotNone(plan["gain_next"])
        self.assertIsNone(plan["gain_3"])
        self.assertIsNone(plan["gain_5"])
        self.assertIsNone(plan["gameweeks"][1]["net_gain"])

    def test_single_transfer_search_ranks_full_squad_gains_and_includes_hit(self):
        result = self.service.transfer_search("one", 3, 100, 0, {3: 50}, 3)
        self.assertEqual(result["evaluated_candidates"], 1)
        self.assertEqual(result["no_transfer_gain"], 0)
        plan = result["recommendations"][0]
        self.assertEqual(plan["moves"][0]["in"]["player_id"], 16)
        self.assertEqual(plan["hit"], 4)
        self.assertEqual(plan["gain_horizon"], plan["gain_3"])
        self.assertEqual(self.client.catalogue_calls, 0)
        with self.assertRaisesRegex(ValueError, "selling price"):
            self.service.transfer_search("one", 3, 100, 1, {}, 3)

    def test_transfer_search_shortlist_is_bounded_and_unaffordable_players_excluded(self):
        players = self.forecast.catalogue.players
        for pid in range(19, 31):
            players[pid] = replace(players[16], id=pid, name=f"Candidate {pid}")
            for gw in self.forecast.gameweeks:
                self.forecast.players[pid] = {
                    g: dict(self.forecast.players[16][g], expected_points=float(pid))
                    for g in self.forecast.gameweeks
                }
                self.forecast.outcomes[gw][pid] = OutcomeSamples(
                    (pid,) * 4, (90,) * 4, bytes([1] * 4)
                )
        result = self.service.transfer_search("one", 3, 100, 1, {3: 50}, 1)
        self.assertEqual(result["eligible_candidates"], 13)
        self.assertEqual(result["evaluated_candidates"], 8)
        gains = [p["gain_horizon"] for p in result["recommendations"]]
        self.assertEqual(gains, sorted(gains, reverse=True))
        none = self.service.transfer_search("one", 3, 0, 1, {3: 50}, 1)
        self.assertEqual(none["recommendations"], [])

    def test_chip_values_account_for_used_unknown_and_repeated_windows(self):
        values = self.service.chips("one")["comparisons"]
        boost = next(r for r in values if r["gameweek"] == 7 and r["chip"] == "bboost")
        triple = next(r for r in values if r["gameweek"] == 7 and r["chip"] == "3xc")
        # Analytically optimal XI is 104 base + captain 15 = 119; all 15 sum to 120.
        self.assertEqual(boost["expected_gain"], 16)
        self.assertEqual(triple["expected_gain"], 15)
        self.service.squads.set("one", replace(snapshot(), chips_used=(("bboost", 2), ("3xc", 4))))
        self.assertTrue(
            all(r["expected_gain"] is None for r in self.service.chips("one")["comparisons"])
        )
        self.service.squads.set("one", replace(snapshot(), chips_used=None))
        self.assertTrue(
            all(r["availability"] == "unknown" for r in self.service.chips("one")["comparisons"])
        )
        self.service.squads.set(
            "one", replace(snapshot(), chips_used=(("bboost", 20), ("3xc", 20)))
        )
        self.assertTrue(
            all(r["expected_gain"] is not None for r in self.service.chips("one")["comparisons"])
        )

    def test_unsupported_season_rules_disable_optimisation_but_not_predictions(self):
        self.forecast.catalogue = replace(self.forecast.catalogue, club_limit=4)
        self.assertEqual(len(self.service.predictions(1)["players"]), 18)
        with self.assertRaisesRegex(ValueError, "verified rules"):
            self.service.lineup("one")
