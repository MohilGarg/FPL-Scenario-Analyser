from __future__ import annotations

import importlib.util
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(p) for p in ("numpy", "sklearn", "pyarrow", "lightgbm"))


@unittest.skipUnless(AVAILABLE, "Install the isolated ML environment")
class PredictionBenchmarkTests(unittest.TestCase):
    def dataset(self):
        import numpy as np

        from ml.dataset import FEATURE_COLUMNS
        from ml.prediction_benchmark import Dataset

        rng = np.random.default_rng(42)
        n = 80
        start = datetime(2023, 8, 1, tzinfo=UTC)
        deadline = np.asarray(
            [start + timedelta(days=7 * (i // 4)) for i in range(n)], dtype=object
        )
        x = np.zeros((n, len(FEATURE_COLUMNS)))
        x[:, :5] = rng.normal(size=(n, 5))
        return Dataset(
            x=x,
            y=x[:, 0] + rng.normal(size=n),
            season=np.full(n, "2023-24"),
            gw=np.repeat(np.arange(1, 21), 4),
            keys=np.asarray([f"p{i}" for i in range(n)]),
            deadline=deadline,
            ready=np.asarray([d + timedelta(days=2) for d in deadline], dtype=object),
            minutes=np.full(n, 90),
            starts=np.ones(n),
            reaches60=np.ones(n, dtype=bool),
            fixtures=np.ones(n),
            position=np.full(n, "MID"),
            audit={},
        )

    def test_readiness_excludes_late_result_and_entire_current_deadline(self):
        from ml.prediction_benchmark import ready_before

        data = self.dataset()
        cutoff = data.deadline[40]
        data.ready[0] = cutoff + timedelta(days=1)
        mask = ready_before(data, cutoff)
        self.assertFalse(mask[0])
        self.assertTrue(mask[1])
        self.assertFalse(mask[40:44].any())

    def test_alpha_search_is_past_only_with_fold_local_preprocessing(self):
        import numpy as np

        from ml.prediction_benchmark import select_alpha
        from ml.training.models import Preprocessor

        data = self.dataset()
        available = data.gw <= 16
        original_fit = Preprocessor.fit
        seen = []

        def observed_fit(x, names):
            seen.append(len(x))
            return original_fit(x, names)

        with patch("ml.prediction_benchmark.Preprocessor.fit", side_effect=observed_fit):
            chosen, folds = select_alpha(data, available)
        changed = self.dataset()
        changed.y[~available] = 1e12
        changed.x[~available] = -1e12
        chosen2, folds2 = select_alpha(changed, available)
        self.assertEqual((chosen, folds), (chosen2, folds2))
        self.assertEqual(len(seen), len(folds))
        self.assertEqual(seen, [f["train_rows"] for f in folds])
        for fold in folds:
            self.assertLess(fold["latest_training_result_ready"], fold["first_validation_deadline"])
        self.assertTrue(np.isfinite(chosen))

    def test_paired_rmse_and_mae_bootstrap_keeps_deadlines_together(self):
        import numpy as np

        from ml.prediction_benchmark import paired

        data = self.dataset()
        actual = np.zeros(80)
        result = paired(actual, np.ones(80), actual, data.deadline, draws=100)
        self.assertEqual(result["mae_delta"], -1)
        self.assertEqual(result["rmse_delta"], -1)
        self.assertEqual(result["mae_ci95"], [-1, -1])
        self.assertEqual(result["rmse_ci95"], [-1, -1])
        self.assertEqual(result["deadlines_improved_rmse"], 20)
        self.assertEqual(result, paired(actual, np.ones(80), actual, data.deadline, draws=100))

    def test_probability_metrics_reject_missing_and_calibrate_all_rows(self):
        import numpy as np

        from ml.prediction_benchmark import probability_scores

        result = probability_scores(np.array([0, 1, 1]), np.array([0.0, 0.5, 1.0]))
        self.assertAlmostEqual(result["brier"], 1 / 12)
        self.assertEqual(sum(b["n"] for b in result["calibration"]), 3)
        with self.assertRaises(ValueError):
            probability_scores(np.array([0, 1]), np.array([0.0, np.nan]))

    def test_interpretable_features_exclude_target_and_evaluation_fields(self):
        from ml.prediction_benchmark import MINUTES, SMALL

        for feature in (*SMALL, *MINUTES):
            self.assertTrue(feature.startswith("pre_gw_"))
            self.assertNotIn("observed_", feature)
            self.assertNotIn("target_", feature)

    def test_minutes_comparison_excludes_same_gw_and_late_results(self):
        from ml.evaluate_playing_time import prior_history

        cutoff = datetime(2024, 10, 1, tzinfo=UTC)
        rows = [
            {
                "fixture_id": 1,
                "gameweek": 4,
                "finished": True,
                "kickoff_utc": cutoff - timedelta(days=2),
            },
            {
                "fixture_id": 2,
                "gameweek": 4,
                "finished": True,
                "kickoff_utc": cutoff - timedelta(hours=23),
            },
            {
                "fixture_id": 3,
                "gameweek": 5,
                "finished": True,
                "kickoff_utc": cutoff - timedelta(days=2),
            },
            {
                "fixture_id": 4,
                "gameweek": 4,
                "finished": True,
                "kickoff_utc": cutoff - timedelta(hours=24),
            },
        ]
        self.assertEqual([r["fixture_id"] for r in prior_history(rows, 5, cutoff)], [1])


if __name__ == "__main__":
    unittest.main()
