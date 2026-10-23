"""Focused checks for descriptive subsamples and calibration-band accounting."""

from __future__ import annotations

import unittest

from ml.ols.diagnostics import BAND_LABELS, summarise


def observation(minutes: float, actual: float, predicted: float, baseline: float = 0) -> dict:
    return {
        "season": "2024-25",
        "recent_minutes": minutes,
        "actual": actual,
        "prediction": predicted,
        "baseline": baseline,
        "observed_minutes": 0,
    }


class DiagnosticTests(unittest.TestCase):
    def test_high_minutes_use_prior_regressor_and_retain_future_absences(self) -> None:
        rows = [observation(59, 20, 20), observation(60, 0, 2, 1), observation(90, 8, 4, 3)]
        accuracy, _ = summarise(rows)
        high = [r for r in accuracy if r["cohort"] == "high historical minutes"]
        self.assertEqual([r["n"] for r in high], [2, 2])
        self.assertEqual(high[0]["mae"], 3)
        self.assertAlmostEqual(high[0]["rmse"], 10**0.5)
        self.assertAlmostEqual(high[0]["r2"], 0.375)
        self.assertEqual(high[1]["mae"], 3)

    def test_exact_boundaries_and_negative_predictions(self) -> None:
        predictions = [-0.2, 0, 1, 2, 3, 4, 5, 6, 8]
        _, bands = summarise([observation(90, p + 1, p) for p in predictions])
        self.assertEqual([r["predicted_band"] for r in bands], list(BAND_LABELS))
        self.assertEqual([r["n"] for r in bands], [1] * len(predictions))
        self.assertEqual([r["mean_predicted"] for r in bands], predictions)
        for row in bands:
            self.assertAlmostEqual(row["actual_minus_predicted"], 1)

    def test_empty_bands_unknown_and_grouped_means_reconcile(self) -> None:
        rows = [observation(60, 2, 1.2), observation(60, 4, 1.8), observation(60, 8, 4.5)]
        _, bands = summarise(rows)
        self.assertEqual(sum(r["n"] for r in bands), len(rows))
        self.assertEqual(sum(r["n"] * r["mean_actual"] for r in bands if r["n"]), 14)
        self.assertEqual(bands[2]["mean_predicted"], 1.5)
        self.assertEqual(bands[2]["mean_actual"], 3)
        self.assertEqual(bands[0]["n"], 0)
        self.assertIsNone(bands[0]["mean_actual"])
        self.assertIsNone(bands[0]["mean_predicted"])
        self.assertIsNone(bands[0]["actual_minus_predicted"])

    def test_seasons_and_empty_high_minutes_cohort_stay_separate(self) -> None:
        row = {**observation(90, 10, 1), "season": "2025-26"}
        accuracy, bands = summarise([observation(0, 0, 1), row])
        first_high = [r for r in accuracy if r["season"] == "2024-25" and r["cohort"] != "all"]
        self.assertTrue(all(r["n"] == 0 and r["mae"] is None for r in first_high))
        means = [r["mean_actual"] for r in bands if r["n"]]
        self.assertEqual(means, [0, 10])


if __name__ == "__main__":
    unittest.main()
