from __future__ import annotations

import json
import unittest

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from tests.helpers import league_state, manager, scores


class AnalysisSerializationTests(unittest.TestCase):
    def test_frontend_payload_has_required_structure(self) -> None:
        state = league_state((manager(1), manager(2, transfer_cost=4)), scores())
        payload = analysis_to_dict(analyse_state(state))

        self.assertEqual(payload["api_version"], "2")
        self.assertEqual(payload["league"]["id"], 123)
        self.assertIn("current_last_entry_ids", payload["summary"])
        self.assertIn("at_risk_entry_ids", payload["summary"])
        self.assertEqual(len(payload["managers"]), 2)
        manager_payload = payload["managers"][0]
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
                "core_condition",
                "scenarios",
                "search",
                "squad",
            }.issubset(manager_payload)
        )
        self.assertIn(payload["league"]["status"], {"upcoming", "in_progress", "complete"})
        self.assertIn(payload["league"]["phase"], {"early", "late", "live", "complete"})
        self.assertEqual(payload["model"]["minimum_remaining_player_contribution"], -10)
        self.assertEqual(payload["model"]["maximum_remaining_player_contribution"], 35)
        self.assertFalse(payload["model"]["bounds_are_theoretical_maxima"])
        self.assertIsInstance(json.dumps(payload), str)


if __name__ == "__main__":
    unittest.main()
