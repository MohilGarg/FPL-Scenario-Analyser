from __future__ import annotations

import json
import unittest
from dataclasses import replace

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from fpl_forfeit.models import Fixture
from tests.helpers import league_state, manager, scores

LIVE_FIXTURE = (Fixture(1, 1, 12, 99, "Team 12", "Opponent", False, False),)


class AnalysisSerializationTests(unittest.TestCase):
    def test_frontend_payload_has_required_structure(self) -> None:
        state = league_state((manager(1), manager(2, transfer_cost=4)), scores())
        payload = analysis_to_dict(analyse_state(state))

        self.assertEqual(payload["api_version"], "3")
        self.assertEqual(payload["league"]["id"], 123)
        self.assertTrue(
            {"current_last_entry_ids", "at_risk_entry_ids", "safe_entry_ids"}.issubset(
                payload["summary"]
            )
        )
        self.assertEqual(len(payload["managers"]), 2)
        self.assertTrue(
            {
                "entry_id",
                "manager_name",
                "team_name",
                "effective_score",
                "official_points",
                "raw_official_points",
                "remaining_player_count",
                "status",
                "bounds",
                "safety_detail",
                "squad",
            }.issubset(payload["managers"][0])
        )
        for retired in ("scenarios", "search", "core_condition"):
            self.assertNotIn(retired, payload["managers"][0])
        self.assertNotIn("outlook", payload)
        self.assertIn(payload["league"]["status"], {"upcoming", "in_progress", "complete"})
        self.assertEqual(payload["model"]["minimum_remaining_player_contribution"], -10)
        self.assertEqual(payload["model"]["maximum_remaining_player_contribution"], 35)
        self.assertFalse(payload["model"]["bounds_are_theoretical_maxima"])
        self.assertIn("-10", payload["model"]["safety_note"])
        self.assertIn("+35", payload["model"]["safety_note"])
        self.assertIsInstance(json.dumps(payload), str)


class LiveStatusTests(unittest.TestCase):
    def test_safe_needs_an_opponent_who_cannot_catch_up(self) -> None:
        # No remaining fixtures: the 4-point hit leaves manager 2 below with no way back.
        state = league_state((manager(1), manager(2, transfer_cost=4)), scores())
        payload = analysis_to_dict(analyse_state(state))
        status = {m["entry_id"]: m["status"] for m in payload["managers"]}
        self.assertEqual(status, {1: "safe", 2: "at_risk"})
        self.assertEqual(payload["managers"][1]["safety_witness"], "Manager 2")
        self.assertTrue(payload["summary"]["last_place_decided"])
        self.assertEqual(payload["differentials"], [])

    def test_unresolved_bottom_lists_only_players_that_separate_at_risk_managers(self) -> None:
        state = league_state(
            (
                manager(1, captain=12),
                manager(2, captain=13),
                manager(3, captain=13, transfer_cost=4),
            ),
            scores(),
            LIVE_FIXTURE,
        )
        payload = analysis_to_dict(analyse_state(state))
        self.assertEqual(sorted(payload["summary"]["at_risk_entry_ids"]), [1, 2, 3])
        self.assertFalse(payload["summary"]["last_place_decided"])
        self.assertEqual([d["player_id"] for d in payload["differentials"]], [12])
        exposure = {
            e["entry_id"]: e["multiplier"] for e in payload["differentials"][0]["exposures"]
        }
        self.assertEqual(exposure, {1: 2, 2: 1, 3: 1})
        self.assertEqual(sorted(payload["differential_manager_ids"]), [1, 2, 3])

    def test_safe_managers_are_left_out_of_the_separating_players(self) -> None:
        state = league_state(
            (
                manager(1, captain=12),
                manager(2, captain=13),
                manager(3, captain=13, transfer_cost=4),
                manager(4, captain=7, transfer_cost=4),
            ),
            scores(),
            LIVE_FIXTURE,
        )
        state.live_scores[13] = replace(state.live_scores[13], points=100)
        payload = analysis_to_dict(analyse_state(state))
        status = {m["entry_id"]: m["status"] for m in payload["managers"]}
        self.assertEqual(status, {1: "at_risk", 2: "safe", 3: "safe", 4: "at_risk"})
        self.assertEqual(sorted(payload["differential_manager_ids"]), [1, 4])
        self.assertEqual([d["player_id"] for d in payload["differentials"]], [12])
        exposure = {
            e["entry_id"]: e["multiplier"] for e in payload["differentials"][0]["exposures"]
        }
        self.assertEqual(exposure, {1: 2, 4: 1})

    def test_completed_gameweek_shows_only_the_final_result(self) -> None:
        state = league_state((manager(1), manager(2, transfer_cost=4)), scores())
        state.raw["bootstrap"] = {"events": [{"id": 1, "finished": True}]}
        payload = analysis_to_dict(analyse_state(state))
        self.assertEqual(payload["league"]["status"], "complete")
        self.assertEqual(
            {m["entry_id"]: m["status"] for m in payload["managers"]}, {1: "clear", 2: "last"}
        )
        self.assertTrue(all(m["safety_detail"] is None for m in payload["managers"]))
        self.assertEqual(payload["differentials"], [])


if __name__ == "__main__":
    unittest.main()
