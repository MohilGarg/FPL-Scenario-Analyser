from __future__ import annotations

import unittest

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from fpl_forfeit.demo import DEMO_MODES, demo_state


class DemoStateTests(unittest.TestCase):
    def payload(self, mode: str) -> dict[str, object]:
        return analysis_to_dict(analyse_state(demo_state(mode)))  # type: ignore[arg-type]

    def test_every_demo_uses_real_twelve_manager_analysis_pipeline(self) -> None:
        expected_phases = {
            "early": "early",
            "late": "late",
            "live": "live",
            "complete": "complete",
        }
        for mode in DEMO_MODES:
            with self.subTest(mode=mode):
                payload = self.payload(mode)
                self.assertEqual(len(payload["managers"]), 12)  # type: ignore[arg-type]
                self.assertEqual(payload["league"]["phase"], expected_phases[mode])  # type: ignore[index]

    def test_early_demo_defers_narrow_scenarios_and_emphasises_differentials(self) -> None:
        payload = self.payload("early")
        self.assertEqual(payload["summary"]["scenario_search_state"], "broad")  # type: ignore[index]
        self.assertGreaterEqual(len(payload["differentials"]), 8)  # type: ignore[arg-type]
        self.assertTrue(all(not manager["scenarios"] for manager in payload["managers"]))  # type: ignore[index]

    def test_late_demo_has_eight_safe_and_four_at_risk_managers(self) -> None:
        payload = self.payload("late")
        self.assertEqual(len(payload["summary"]["safe_entry_ids"]), 8)  # type: ignore[index]
        self.assertEqual(len(payload["summary"]["at_risk_entry_ids"]), 4)  # type: ignore[index]
        at_risk = [manager for manager in payload["managers"] if manager["status"] != "safe"]  # type: ignore[index]
        self.assertTrue(all(manager["core_condition"] for manager in at_risk))
        self.assertTrue(all(manager["scenarios"] for manager in at_risk))

    def test_live_demo_exposes_live_fixture_points_and_captaincy(self) -> None:
        payload = self.payload("live")
        self.assertEqual(payload["league"]["fixtures"]["live"], 1)  # type: ignore[index]
        bruno = next(item for item in payload["differentials"] if item["player_name"] == "Bruno")  # type: ignore[index]
        self.assertEqual(bruno["fixture_status"], "live")
        self.assertEqual(bruno["minutes"], 68)
        self.assertIn(3, {item["multiplier"] for item in bruno["exposures"]})

    def test_completed_demo_has_final_loser_and_no_remaining_analysis(self) -> None:
        payload = self.payload("complete")
        self.assertEqual(payload["league"]["status"], "complete")  # type: ignore[index]
        self.assertEqual(payload["summary"]["current_last_entry_ids"], [1])  # type: ignore[index]
        self.assertEqual(payload["differentials"], [])
        self.assertTrue(
            all(manager["remaining_player_count"] == 0 for manager in payload["managers"])
        )  # type: ignore[index]

    def test_manager_details_include_hit_chip_and_confirmed_autosub(self) -> None:
        payload = self.payload("late")
        by_name = {manager["manager_name"]: manager for manager in payload["managers"]}  # type: ignore[index]
        self.assertEqual(by_name["Ben"]["transfer_cost"], 4)
        self.assertEqual(by_name["Chloe"]["active_chip"], "3xc")
        dev_statuses = {pick["autosub_status"] for pick in by_name["Dev"]["squad"]}
        self.assertIn("subbed_out", dev_statuses)
        self.assertIn("subbed_in", dev_statuses)

    def test_scenarios_have_share_text_bottom_scores_and_distinct_descriptions(self) -> None:
        payload = self.payload("late")
        alex = next(manager for manager in payload["managers"] if manager["manager_name"] == "Alex")  # type: ignore[index]
        descriptions = [scenario["description"] for scenario in alex["scenarios"]]
        self.assertEqual(len(descriptions), len(set(descriptions)))
        for scenario in alex["scenarios"]:
            self.assertIn("For Alex to finish last", scenario["share_text"])
            self.assertTrue(scenario["bottom_scores"])
            self.assertIn(
                scenario["plausibility"], {"More plausible", "Plausible", "Unusual", "Extreme"}
            )


if __name__ == "__main__":
    unittest.main()
