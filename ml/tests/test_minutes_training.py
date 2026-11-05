from __future__ import annotations

import importlib.util
import unittest
from datetime import timedelta

from tests.assistant_fixtures import NOW, catalogue


@unittest.skipUnless(importlib.util.find_spec("sklearn"), "Install isolated research dependencies")
class MinutesTrainingTests(unittest.TestCase):
    def test_same_deadline_and_unsettled_history_are_excluded(self):
        from ml.minutes_data import eligible_history

        rows = [
            {
                "gameweek": 6,
                "finished": True,
                "kickoff_utc": NOW - timedelta(hours=25),
                "fixture_id": 1,
            },
            {
                "gameweek": 6,
                "finished": True,
                "kickoff_utc": NOW - timedelta(hours=23),
                "fixture_id": 2,
            },
            {
                "gameweek": 7,
                "finished": True,
                "kickoff_utc": NOW - timedelta(days=3),
                "fixture_id": 3,
            },
        ]
        self.assertEqual([r["fixture_id"] for r in eligible_history(rows, 7, NOW)], [1])

    def test_training_priors_and_duration_support_include_long_substitutes(self):
        from fpl_forfeit.assistant.minutes_model import MinutesModel, history_features
        from ml.train_minutes_model import fit

        player = catalogue().players[3]
        rows = []
        for state, minutes, starts in ((0, 0, 0), (1, 10, 0), (2, 70, 0), (3, 40, 1), (4, 90, 1)):
            for _ in range(10):
                rows.append(
                    {
                        "player": player,
                        "history": [],
                        "state": state,
                        "minutes": minutes,
                        "ready": NOW,
                        "features": history_features(player, []),
                    }
                )
        model = MinutesModel(data=fit(rows))
        estimate = model.predict(player, [])
        self.assertGreater(estimate.states[2], 0)
        self.assertGreater(estimate.sixty_probability, 0)
        self.assertAlmostEqual(estimate.no_show_probability, 0.2, delta=0.02)
        self.assertLess(estimate.start_probability, 0.5)


if __name__ == "__main__":
    unittest.main()
