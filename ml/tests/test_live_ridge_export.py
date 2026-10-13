"""Optional scientific-stack check: packaged runtime equals the original fitted artifact."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("joblib"), "Install the pinned ML environment")
class ExportParityTests(unittest.TestCase):
    def test_existing_fitted_ridge_and_portable_coefficients_match(self):
        import joblib
        import numpy as np

        from fpl_forfeit.assistant.ml_inference import RidgeModel
        from ml.dataset import FEATURE_COLUMNS

        path = Path("ml/artifacts/baselines-v1/final_models/ridge.joblib")
        if not path.exists():
            self.skipTest("Ignored original artifact unavailable; packaged JSON remains deployable")
        source = joblib.load(path)
        model = RidgeModel()
        rng = np.random.default_rng(2)
        x = rng.normal(size=(64, len(FEATURE_COLUMNS)))
        x[::3, ::2] = np.nan
        expected = source["model"].predict(source["preprocessor"].transform(x))
        actual = [
            model.predict(
                {c: None if np.isnan(v) else float(v) for c, v in zip(FEATURE_COLUMNS, row)}
            )
            for row in x
        ]
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)
