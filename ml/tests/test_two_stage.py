from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(p) for p in ("numpy", "sklearn", "lightgbm", "pyarrow"))


@unittest.skipUnless(AVAILABLE, "Install ml/training/requirements.lock.txt")
class TwoStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from ml.training.two_stage_experiment import DEFAULT_CONFIG
        cls.np = np
        cls.config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        cls.config["threads"] = 1

    def sample(self):
        from ml.dataset import FEATURE_COLUMNS
        rng = self.np.random.default_rng(72)
        x = self.np.zeros((100, len(FEATURE_COLUMNS)))
        x[:, :4] = rng.normal(size=(100, 4))
        x[:, FEATURE_COLUMNS.index(self.config["probability_reference_feature"])] = rng.uniform(size=100)
        appeared = x[:, 0] + rng.normal(size=100) > 0
        minutes = appeared.astype(float) * 90
        y = self.np.where(appeared, 2 + x[:, 1], 0)
        y[self.np.flatnonzero(~appeared)[0]] = -1  # genuine nonzero nonappearance contribution
        return x, y, minutes

    def test_config_prevents_consumed_season_reuse_and_search(self):
        from ml.training.two_stage_experiment import validate_config
        validate_config(self.config)
        for change in ({"splits": {"train": "2023-24", "validation": "2025-26"}},
                       {"splits": {"train": "2025-26", "validation": "2024-25"}},
                       {"parameter_search": True}, {"validation_refit": True}, {"prediction_clipping": True},
                       {"reference_ridge": {"alpha": 1., "solver": "cholesky"}}, {"threads": 64}):
            with self.assertRaises(ValueError):
                validate_config({**self.config, **change})

    def test_runner_reads_only_initial_and_validation_seasons(self):
        from ml.training.two_stage_experiment import DEFAULT_CONFIG, run
        from ml.sources import ROOT
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as folder, patch("ml.training.two_stage_experiment.load_population") as loader:
            loader.side_effect = RuntimeError("stop before fit")
            with self.assertRaisesRegex(RuntimeError, "stop before"):
                run(Path(folder), Path(folder), DEFAULT_CONFIG)
            self.assertEqual(loader.call_args.args[1], ["2023-24", "2024-25"])
            self.assertNotIn("2025-26", loader.call_args.args[2])

    def test_outputs_cannot_escape_ml_artifacts(self):
        from ml.sources import ROOT
        from ml.training.two_stage_experiment import safe_output
        with self.assertRaises(ValueError):
            safe_output(ROOT / "data/processed")
        safe_output(ROOT / "artifacts/reproduction")

    def test_total_expectation_handles_zero_minute_negative_points(self):
        from ml.training.two_stage_models import combine_points
        p = self.np.array([0., .25, 1.])
        result = combine_points(p, self.np.array([5., 5., -2.]), -1.)
        self.np.testing.assert_array_equal(result, [-1., .5, -2.])
        self.np.testing.assert_array_equal(combine_points(p, self.np.full(3, 4.), 0.), [0., 1., 4.])

    def test_invalid_probabilities_and_nonfinite_contributions_fail(self):
        from ml.training.two_stage_models import combine_points
        for p in ([-.01], [1.01], [self.np.nan], [self.np.inf]):
            with self.assertRaises(ValueError):
                combine_points(self.np.array(p), self.np.array([3.]), 0.)
        with self.assertRaises(ValueError):
            combine_points(self.np.array([.5]), self.np.array([self.np.nan]), 0.)

    def test_nonappearance_mean_is_train_only_and_classifier_probabilities_bounded(self):
        from ml.training.two_stage_models import fit_two_stage, predict_two_stage
        x, y, minutes = self.sample()
        model, timing = fit_two_stage(x, y, minutes, self.config)
        self.assertEqual(model.nonappearance_mean, y[minutes == 0].mean())
        self.assertEqual(timing["nonappearance_nonzero_points_rows"], 1)
        prediction, _ = predict_two_stage(model, x, self.config)
        self.assertTrue(((prediction["appearance_probability"] >= 0) & (prediction["appearance_probability"] <= 1)).all())
        self.np.testing.assert_allclose(prediction["two_stage"], prediction["appearance_probability"] * prediction["conditional_points"]
                                       + (1 - prediction["appearance_probability"]) * model.nonappearance_mean)

    def test_missing_values_use_frozen_training_preprocessing(self):
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.two_stage_models import fit_two_stage, predict_two_stage
        x, y, minutes = self.sample()
        x[::5, 1] = self.np.nan
        future_column = FEATURE_COLUMNS.index("pre_gw_defensive_contribution_sum")
        x[:, future_column] = self.np.nan
        model, _ = fit_two_stage(x, y, minutes, self.config)
        metadata = deepcopy(model.preprocessor.metadata())
        validation = x[:7].copy()
        validation[:, 1] = self.np.nan
        validation[:, FEATURE_COLUMNS.index(self.config["probability_reference_feature"])] = self.np.nan
        before, _ = predict_two_stage(model, validation, self.config)
        validation[:, future_column] = 99999
        after, _ = predict_two_stage(model, validation, self.config)
        for key in before:
            self.np.testing.assert_array_equal(before[key], after[key])
            self.assertTrue(self.np.isfinite(after[key]).all())
        self.assertEqual(model.preprocessor.metadata(), metadata)
        self.np.testing.assert_array_equal(after["lagged_appearance_probability"], self.np.full(7, model.appearance_prevalence))

    def test_reference_matches_original_ridge_fit(self):
        from sklearn.linear_model import Ridge
        from ml.training.two_stage_models import fit_two_stage
        x, y, minutes = self.sample()
        model, _ = fit_two_stage(x, y, minutes, self.config)
        independent = Ridge(**self.config["reference_ridge"]).fit(model.preprocessor.transform(x), y)
        self.np.testing.assert_allclose(model.reference.predict(model.preprocessor.transform(x)),
                                       independent.predict(model.preprocessor.transform(x)), rtol=0, atol=1e-12)

    def test_repeated_fit_and_saved_reload_reproduce_predictions(self):
        import joblib
        from ml.training.two_stage_models import fit_two_stage, predict_two_stage
        x, y, minutes = self.sample()
        model1, _ = fit_two_stage(x, y, minutes, self.config)
        model2, _ = fit_two_stage(x, y, minutes, self.config)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.joblib"
            joblib.dump(model1, path)
            restored = joblib.load(path)
        before, _ = predict_two_stage(model1, x, self.config)
        for model in (model2, restored):
            after, _ = predict_two_stage(model, x, self.config)
            for name in before:
                self.np.testing.assert_allclose(before[name], after[name], rtol=0, atol=1e-12)

    def test_single_class_or_unknown_minutes_cannot_fit(self):
        from ml.training.two_stage_models import fit_two_stage
        x, y, minutes = self.sample()
        for invalid in (self.np.zeros(len(y)), self.np.full(len(y), 90.), self.np.full(len(y), self.np.nan)):
            with self.assertRaises(ValueError):
                fit_two_stage(x, y, invalid, self.config)

    def test_regular_starters_rule_is_predeadline_and_missing_fails_closed(self):
        from ml.dataset import FEATURE_COLUMNS
        from ml.training.two_stage_models import regular_starters
        x, _, _ = self.sample()
        for suffix, values in (("count", [5, 4, 5, 5]), ("start_rate", [.8, 1., .79, self.np.nan]),
                               ("minutes_mean", [60., 90., 90., 90.])):
            x[:4, FEATURE_COLUMNS.index(f"pre_gw_last5_gws_{suffix}")] = values
        self.np.testing.assert_array_equal(regular_starters(x[:4], self.config), [True, False, False, False])

    def test_binary_metrics_and_calibration_edges(self):
        from ml.evaluation.two_stage_metrics import probability_metrics, points_calibration
        actual = self.np.array([0., 1.])
        m = probability_metrics(actual, self.np.array([0., 1.]), 10)
        self.assertEqual((m["brier"], m["equal_width_ece"]), (0., 0.))
        self.assertEqual(sum(b["n"] for b in m["calibration"]), 2)
        self.assertEqual(m["calibration"][-1]["n"], 1)
        self.assertTrue(self.np.isfinite(m["log_loss"]))
        self.assertEqual(probability_metrics(actual, self.np.array([.5, .5]), 10)["brier"], .25)
        calibrated = points_calibration(self.np.array([-1., 0., 2.]), self.np.array([-1., 0., 2.]), [0., 2.])
        self.assertEqual([b["n"] for b in calibrated["bands"]], [1, 1, 1])
        self.assertEqual(calibrated["weighted_absolute_band_gap"], 0.)

    def test_paired_intervals_are_reproducible_and_keep_entire_gw_blocks(self):
        from ml.evaluation.two_stage_metrics import paired_uncertainty
        config = deepcopy(self.config)
        config["uncertainty"]["bootstrap_draws"] = 100
        y = self.np.zeros(24)
        gw = self.np.repeat(self.np.arange(8), 3)
        a = paired_uncertainty(y, self.np.ones(24), y, gw, config)
        self.assertEqual(a, paired_uncertainty(y, self.np.ones(24), y, gw, config))
        for metric in ("mae", "rmse", "bias"):
            self.assertEqual(a["differences_contender_minus_ridge"][metric]["ci95"], [-1., -1.])
        with self.assertRaises(ValueError):
            paired_uncertainty(y, y, y, gw * 2, config)

    def test_future_and_target_dgw_results_do_not_change_predictions(self):
        from ml.dataset import FEATURE_COLUMNS, FLOAT_STATS, INT_STATS, gameweek_dataset
        from ml.tests.test_readiness import record
        from ml.training.two_stage_models import fit_two_stage, predict_two_stage
        x, y, minutes = self.sample()
        model, _ = fit_two_stage(x, y, minutes, self.config)
        data = [record(g) for g in range(1, 15)]
        first = data[11]
        first["team_gw_fixture_ids"] = [112, 212]
        data.append({**first, "fixture_id": 212, "kickoff_utc": first["kickoff_utc"] + timedelta(days=1)})
        deadline = {("2024-25", 12): first["kickoff_utc"] - timedelta(hours=2)}
        def prediction(rows):
            target = next(r for r in gameweek_dataset(rows, deadline) if r["gameweek"] == 12)
            features = self.np.array([[self.np.nan if target[c] is None else target[c] for c in FEATURE_COLUMNS]], dtype=float)
            return predict_two_stage(model, features, self.config)[0]
        before = prediction(data)
        changed = deepcopy(data)
        for row in changed:
            if row["gameweek"] >= 12:
                for c in (*INT_STATS, *FLOAT_STATS):
                    row[c] = 9999
        after = prediction(changed)
        for name in before:
            self.np.testing.assert_array_equal(before[name], after[name])

    def test_later_season_labels_cannot_change_validation_model_or_training_hash(self):
        from ml.training.data import Population, subset
        from ml.training.two_stage_experiment import population_hashes
        from ml.training.two_stage_models import fit_two_stage, predict_two_stage
        x, y, minutes = self.sample()
        season = self.np.repeat(["2023-24", "2024-25", "2025-26", "2025-26"], 25)
        pop = Population(x, y, season, self.np.tile(self.np.arange(2, 27), 4), self.np.array([f"p{i}" for i in range(100)]),
                         minutes, self.np.full(100, "MID"), {s: {} for s in set(season)})
        train, val = subset(pop, "2023-24"), subset(pop, "2024-25")
        model, _ = fit_two_stage(train.x, train.y, train.minutes, self.config)
        before, _ = predict_two_stage(model, val.x, self.config)
        train_hash = population_hashes(train)
        pop.y[pop.season == "2025-26"] = 99999
        pop.minutes[pop.season == "2025-26"] = 99999
        altered = subset(pop, "2023-24")
        self.assertEqual(train_hash, population_hashes(altered))
        refit, _ = fit_two_stage(altered.x, altered.y, altered.minutes, self.config)
        after, _ = predict_two_stage(refit, val.x, self.config)
        for name in before:
            self.np.testing.assert_allclose(before[name], after[name], rtol=0, atol=1e-12)


if __name__ == "__main__":
    unittest.main()
