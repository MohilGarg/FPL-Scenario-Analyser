from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression, PoissonRegressor
from threadpoolctl import threadpool_limits

from fpl_forfeit.assistant.component_features import FEATURE_COLUMNS, TEAM_COLUMNS
from fpl_forfeit.assistant.component_model import ComponentModel
from ml.train_fixture_model import fit, pre


class PortableTrainingTests(unittest.TestCase):
    def rows(self):
        return [{**dict.fromkeys(FEATURE_COLUMNS, 0), "position_GK": 1, "history_count": 3+i%5,
                 "minutes5": None if i%7 == 0 else 15*(i%6), "own_gf": 1+i%3,
                 "target_minutes": (0, 20, 45, 90)[i%4], "target_starts": int(i%4 >= 2),
                 "target_goals_scored": int(i%9 == 0), "target_assists": int(i%5 == 0),
                 "target_saves": i%6, "target_yellow_cards": int(i%6 == 0), "target_goals": i%4,
                 "source_xP": 9999, "target_total_points": 999} for i in range(40)]

    def test_preprocessing_ignores_outcome_metadata_and_keeps_real_zero(self):
        rows = self.rows()
        _, metadata = pre(rows, ("minutes5", "history_count"))
        self.assertEqual(metadata["median"][0], 30)
        changed = [{**r, "target_total_points": -999, "source_xP": -999} for r in rows]
        _, second = pre(changed, ("minutes5", "history_count"))
        self.assertEqual(metadata, second)
        self.assertNotIn("target_total_points", FEATURE_COLUMNS)

    def test_portable_exports_reproduce_scikit_learn_and_fixed_fits(self):
        rows, config = self.rows(), json.loads(Path("ml/fixture_model_config.json").read_bytes())
        with threadpool_limits(limits=2):
            first = fit(rows, rows, config)
            second = fit(rows, rows, config)
            self.assertEqual(first, second)
            model = ComponentModel(data=first)
            x, _ = pre(rows, FEATURE_COLUMNS)
            logistic = LogisticRegression(C=config["logistic_C"], max_iter=500).fit(x, np.array([i%4 for i in range(len(rows))]))
            np.testing.assert_allclose(model.predict("playing_state", rows[3]), logistic.predict_proba(x[3:4])[0], rtol=0, atol=1e-12)
            x, _ = pre(rows, TEAM_COLUMNS)
            poisson = PoissonRegressor(alpha=1, max_iter=300).fit(x, np.array([r["target_goals"] for r in rows]))
            self.assertAlmostEqual(model.predict("team_goals", rows[3]), poisson.predict(x[3:4])[0], places=12)


if __name__ == "__main__":
    unittest.main()
