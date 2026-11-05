from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from fpl_forfeit.assistant.external_forecasts import ForecastRow
from fpl_forfeit.assistant.service import AssistantService
from tests.assistant_fixtures import NOW, FixtureClient


class PrivateExternalComparisonTests(unittest.TestCase):
    def test_external_means_use_statistical_member_and_label_missing_coverage(self):
        service = AssistantService(FixtureClient(), samples=4, now=lambda: NOW)
        baseline = service.statistical_forecast()
        service.official_snapshot = {"bootstrap": {"events": [{"deadline_time": NOW.isoformat()}]}}
        row = ForecastRow(3, 7, 8)
        captured = {
            "sources": {"fpl_cat": {3: row}},
            "status": {"fpl_cat": {"state": "ready"}},
            "ensemble_mean": {3: {"expected_points": 8}},
            "fallback": None,
        }
        alternate = replace(baseline, players=deepcopy(baseline.players))
        alternate.players[3][7]["expected_points"] = 1000
        with patch(
            "fpl_forfeit.assistant.external_forecasts.load_available_forecasts",
            return_value=captured,
        ) as loader:
            comparison = service.external_comparison(alternate, (7,))[7]
            self.assertEqual(loader.call_count, 1)
            rows = comparison["players"]
            self.assertEqual(
                rows[3]["ensemble"]["expected_points"],
                (baseline.players[3][7]["expected_points"] + 8) / 2,
            )
            self.assertEqual(
                rows[3]["ensemble"]["members"]["internal_statistical"],
                baseline.players[3][7]["expected_points"],
            )
            self.assertFalse(rows[3]["ensemble"]["distribution_available"])
            self.assertTrue(rows[4]["ensemble"]["fallback"])
            self.assertEqual(
                rows[4]["ensemble"]["expected_points"], baseline.players[4][7]["expected_points"]
            )
            self.assertIsNone(rows[4]["external_mean"])
            self.assertEqual(comparison["status"]["coverage_count"], 1)
            service.external_comparison(alternate, (7,))
            self.assertEqual(loader.call_count, 1)

    def test_point_only_ensemble_cannot_select_simulated_planning(self):
        service = AssistantService(FixtureClient(), samples=4, now=lambda: NOW)
        with self.assertRaises(ValueError), service.using_model("ensemble"):
            service.forecast()
        self.assertEqual(service.client.catalogue_calls, 0)


if __name__ == "__main__":
    unittest.main()
