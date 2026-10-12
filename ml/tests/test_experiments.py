from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


AVAILABLE = all(importlib.util.find_spec(p) for p in ("numpy", "sklearn", "lightgbm"))


@unittest.skipUnless(AVAILABLE, "Install ml/training/requirements.lock.txt for experiment tests")
class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from ml.training.experiment import DEFAULT_CONFIG
        cls.np = np
        cls.config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))

    def test_fixed_chronological_splits_and_compute_caps(self):
        from ml.training.experiment import validate_config
        validate_config(self.config)
        for mutation in ({"splits": {"train": "2023-24", "validation": "2025-26", "test": "2024-25"}},
                         {"threads": 64}):
            with self.assertRaises(ValueError):
                validate_config({**self.config, **mutation})
        changed = deepcopy(self.config)
        changed["selection"]["early_stopping"] = True
        with self.assertRaises(ValueError):
            validate_config(changed)

    def test_training_medians_scaling_and_future_only_columns(self):
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.models import Preprocessor
        x = self.np.zeros((4, len(FEATURE_COLUMNS)))
        x[:, 0] = [1, 3, self.np.nan, self.np.nan]
        future_only = FEATURE_COLUMNS.index("pre_gw_defensive_contribution_sum")
        x[:, future_only] = self.np.nan
        pre = Preprocessor.fit(x, FEATURE_COLUMNS)
        self.assertEqual(pre.median.tolist(), [2.0])
        self.assertEqual(pre.mean.tolist(), [2.0])
        before = deepcopy(pre.metadata())
        future = x.copy()
        future[:, future_only] = 999999
        self.np.testing.assert_array_equal(pre.transform(x), pre.transform(future))
        future[:, 0] = 999999
        pre.transform(future)
        self.assertEqual(pre.metadata(), before)
        self.assertIn("pre_gw_defensive_contribution_sum", pre.dropped_empty)

    def test_recent_average_is_exact_approved_preceding_gw_mean(self):
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.models import Preprocessor, predict_models
        x = self.np.zeros((4, len(FEATURE_COLUMNS)))
        index = FEATURE_COLUMNS.index(self.config["recent_average"]["feature"])
        x[:, index] = [0, 1, 2.5, -1]
        pre = Preprocessor.fit(x, FEATURE_COLUMNS)
        predictions, _ = predict_models(pre, {"recent_average": None}, x, self.config)
        self.np.testing.assert_array_equal(predictions["recent_average"], [0, 1, 2.5, -1])

    def test_schema_cannot_accept_target_or_evaluation_metadata(self):
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.models import Preprocessor
        for field in ("target_points", "observed_minutes", "position", "evaluation_eligible", "xP", "fixture_records"):
            self.assertNotIn(field, FEATURE_COLUMNS)
        with self.assertRaises(ValueError):
            Preprocessor.fit(self.np.arange(8).reshape(4, 2), ("pre_gw_history_fixtures", "target_points"))

    def test_metrics_bias_direction_and_nonfinite_rejection(self):
        from ml.evaluation.metrics import metrics
        m = metrics(self.np.array([0., 2.]), self.np.array([1., 1.]))
        self.assertEqual((m["mae"], m["rmse"], m["bias"]), (1., 1., 0.))
        self.assertEqual(metrics(self.np.array([0.]), self.np.array([2.]))["bias"], 2.)
        with self.assertRaises(ValueError):
            metrics(self.np.array([0.]), self.np.array([self.np.nan]))

    def test_selection_uses_validation_only_and_deterministic_tie_break(self):
        from ml.evaluation.metrics import choose_model
        scores = {"ridge": {"all": {"mae": 1., "rmse": 2.}},
                  "lightgbm": {"all": {"mae": 1., "rmse": 1.5}},
                  "recent_average": {"all": {"mae": 2., "rmse": 2.}}}
        self.assertEqual(choose_model(scores), "lightgbm")

    def test_paired_block_uncertainty_reproducible_and_directional(self):
        from ml.evaluation.metrics import paired_mae_uncertainty
        y = self.np.zeros(24)
        kwargs = {"seed": 3, "draws": 100, "block_length": 4}
        a = paired_mae_uncertainty(y, self.np.ones(24), self.np.zeros(24), self.np.repeat(self.np.arange(8), 3), **kwargs)
        b = paired_mae_uncertainty(y, self.np.ones(24), self.np.zeros(24), self.np.repeat(self.np.arange(8), 3), **kwargs)
        self.assertEqual(a, b)
        self.assertEqual(a["ci95"], [-1., -1.])

    def test_tiny_fitted_predictions_and_saved_models_reproducible(self):
        import joblib
        import lightgbm as lgb
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.models import fit_models, predict_models
        from ml.training.experiment import save_fitted
        rng = self.np.random.default_rng(8)
        x = self.np.zeros((80, len(FEATURE_COLUMNS)))
        x[:, :4] = rng.normal(size=(80, 4))
        x[:, FEATURE_COLUMNS.index(self.config["recent_average"]["feature"])] = rng.normal(size=80)
        y = 2 * x[:, 0] + x[:, 1] ** 2
        config = deepcopy(self.config)
        config["threads"] = 1
        config["lightgbm"]["num_boost_round"] = 12
        config["lightgbm"]["params"]["min_data_in_leaf"] = 5
        pre1, models1, _ = fit_models(x, y, config)
        pre2, models2, _ = fit_models(x, y, config)
        a, _ = predict_models(pre1, models1, x[:7], config)
        b, _ = predict_models(pre2, models2, x[:7], config)
        for model in a:
            self.np.testing.assert_allclose(a[model], b[model], rtol=0, atol=1e-12)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            save_fitted(path, pre1, models1, config)
            ridge = joblib.load(path / "ridge.joblib")
            self.np.testing.assert_allclose(ridge["model"].predict(ridge["preprocessor"].transform(x[:7])), a["ridge"])
            restored = lgb.Booster(model_file=str(path / "lightgbm.txt"))
            self.np.testing.assert_allclose(restored.predict(pre1.transform(x[:7]), num_threads=1), a["lightgbm"])

    def test_selection_reads_only_training_validation_before_holdout(self):
        from ml.training.experiment import DEFAULT_CONFIG, select
        with tempfile.TemporaryDirectory() as folder, patch("ml.training.experiment.load_population") as loader:
            loader.side_effect = RuntimeError("stop before fitting")
            with self.assertRaisesRegex(RuntimeError, "stop before"):
                select(Path(folder), Path(folder), DEFAULT_CONFIG)
            self.assertEqual(loader.call_args.args[1], ["2023-24", "2024-25"])

    def test_mutating_later_season_outcomes_does_not_change_validation_predictions(self):
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.data import Population, subset
        from ml.training.models import fit_models, predict_models
        from ml.evaluation.metrics import choose_model, evaluate
        rng = self.np.random.default_rng(11)
        x = self.np.zeros((90, len(FEATURE_COLUMNS)))
        x[:, :4] = rng.normal(size=(90, 4))
        y = x[:, 0] ** 2 + x[:, 1]
        seasons = self.np.repeat(["2023-24", "2024-25", "2025-26"], 30)
        exclusions = {s: {} for s in set(seasons)}
        population = Population(x, y, seasons, self.np.tile(self.np.arange(2, 32), 3),
                                self.np.array([f"p{i}" for i in range(90)]), self.np.full(90, 90.),
                                self.np.full(90, "MID"), exclusions)
        config = deepcopy(self.config)
        config["threads"] = 1
        config["lightgbm"]["num_boost_round"] = 10
        config["lightgbm"]["params"]["min_data_in_leaf"] = 5
        train, validation = subset(population, "2023-24"), subset(population, "2024-25")
        pre, models, _ = fit_models(train.x, train.y, config)
        before, _ = predict_models(pre, models, validation.x, config)
        population.y[population.season == "2025-26"] = 999999
        altered_train = subset(population, "2023-24")
        pre2, models2, _ = fit_models(altered_train.x, altered_train.y, config)
        after, _ = predict_models(pre2, models2, validation.x, config)
        for model in before:
            self.np.testing.assert_allclose(before[model], after[model], rtol=0, atol=1e-12)
        self.assertEqual(choose_model(evaluate(validation, before)), choose_model(evaluate(validation, after)))

    def test_holdout_stage_checks_frozen_fingerprints_and_reselection_guard(self):
        from ml.training.experiment import DEFAULT_CONFIG, select, test_holdout
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "holdout_started.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "already"):
                test_holdout(path, path, DEFAULT_CONFIG)
            with self.assertRaisesRegex(ValueError, "forbidden"):
                select(path, path, DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as folder, patch("ml.training.experiment.fingerprints", return_value={}):
            path = Path(folder)
            (path / "selection.json").write_text(json.dumps({"fingerprints": {"changed": True}, "holdout_loaded": False}))
            with self.assertRaisesRegex(ValueError, "changed"):
                test_holdout(path, path, DEFAULT_CONFIG)

    def test_zero_minutes_are_kept_and_unverified_position_is_only_a_group(self):
        import pyarrow as pa
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.data import candidate_population
        from ml.dataset import timestamp
        cutoff = timestamp("2024-01-01T10:00:00Z")
        feature = {"season": ["2023-24"] * 3, "player_key": ["a", "b", "c"], "gameweek": [2] * 3,
                   "feature_cutoff_utc": [cutoff] * 3, **{c: [0.] * 3 for c in FEATURE_COLUMNS}}
        feature["pre_gw_history_fixtures"] = [1., 0., 1.]
        feature["xP"] = [999.] * 3  # never selected even if present in an input table
        outcomes = pa.table({"player_key": ["a", "b", "c"], "gameweek": [2] * 3,
                             "target_points": [0., 5., 9.], "target_complete": [True] * 3,
                             "observed_minutes": [0., 90., 90.], "position": ["MID"] * 3,
                             "element": [1, 2, 3], "player_code": [10, 20, 30]})
        quality = pa.table({"player_key": ["a", "b", "c"], "gameweek": [2] * 3,
                            "evaluation_eligible": [True, True, False]})
        data = candidate_population(pa.table(feature), outcomes, quality,
                                    {("2023-24", 2): cutoff}, {})
        self.assertEqual(data.y.tolist(), [0.])
        self.assertEqual(data.minutes.tolist(), [0.])
        self.assertEqual(data.position.tolist(), ["UNKNOWN"])
        self.assertEqual(data.x.shape, (1, len(FEATURE_COLUMNS)))


if __name__ == "__main__":
    unittest.main()
