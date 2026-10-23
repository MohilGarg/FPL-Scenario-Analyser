"""Offline regression tests for information timing and the educational calculation."""

from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import numpy as np
from sklearn.linear_model import LinearRegression

from ml.ols.data import (
    FEATURES,
    STATS,
    align_quality,
    build_rows,
    features_before,
    matrix,
    split_rows,
)
from ml.ols.run import explain_prediction, metrics

DEADLINE = datetime(2024, 2, 10, 11, tzinfo=UTC)


def match(gw: int, *, days: float = 7, points: int = 5, fixture: int | None = None) -> dict:
    return {
        "season": "2023-24",
        "player_key": "2023-24:1",
        "gameweek": gw,
        "fixture_id": fixture if fixture is not None else gw,
        "position": "MID",
        "kickoff_utc": DEADLINE - timedelta(days=days),
        "finished": True,
        **dict.fromkeys(STATS, 0),
        "minutes": 90,
        "starts": 1,
        "total_points": points,
        "expected_goals": 0.4,
        "expected_assists": 0.2,
    }


class FeatureTimingTests(unittest.TestCase):
    def test_target_and_future_mutations_do_not_change_features(self) -> None:
        history = [match(1, days=14), match(2), match(3, days=0.5), match(4, days=-7)]
        before = features_before(history, "2023-24", 3, DEADLINE)
        changed = deepcopy(history)
        for row in changed[2:]:
            row.update(dict.fromkeys(STATS, 999))
            row.update(position="FWD", was_home=True, opponent_id=12345)
        self.assertEqual(before, features_before(changed, "2023-24", 3, DEADLINE))
        self.assertEqual(before["values"]["recent_total_points"], 5)

    def test_strict_buffer_and_late_rearrangement(self) -> None:
        history = [match(1), match(2, days=1, points=100), match(3, days=-1, points=200)]
        values = features_before(history, "2023-24", 4, DEADLINE)
        self.assertEqual(values["values"]["recent_total_points"], 5)
        self.assertLess(values["history_latest_ready_utc"], DEADLINE)

    def test_earlier_target_dgw_leg_never_enters(self) -> None:
        history = [match(1, points=2), match(2, days=3, points=40)]
        self.assertEqual(
            features_before(history, "2023-24", 2, DEADLINE)["values"]["recent_total_points"], 2
        )

    def test_dgw_aggregation_zero_minutes_and_short_window(self) -> None:
        zero = match(2, points=0)
        zero.update(minutes=0, starts=0)
        history = [
            match(1, days=15, points=3, fixture=11),
            match(1, days=12, points=7, fixture=12),
            zero,
        ]
        values = features_before(history, "2023-24", 3, DEADLINE)
        self.assertEqual(values["history_gws"], 2)
        self.assertEqual(values["values"]["recent_total_points"], (3 + 7 + 0) / 2)
        self.assertEqual(values["values"]["recent_minutes"], 90)

    def test_latest_three_observed_gws_skip_unknown_blank(self) -> None:
        history = [match(g, points=g, days=40 - g) for g in (1, 2, 4, 5)]
        values = features_before(history, "2023-24", 6, DEADLINE)
        self.assertAlmostEqual(values["values"]["recent_total_points"], (2 + 4 + 5) / 3)

    def test_season_unfinished_and_unknown_kickoff_exclusion(self) -> None:
        other, unfinished, unknown = match(1), match(2), match(3)
        other["season"] = "2022-23"
        unfinished["finished"] = False
        unknown["kickoff_utc"] = None
        self.assertIsNone(features_before([other, unfinished, unknown], "2023-24", 4, DEADLINE))

    def test_missing_is_not_zero_and_cold_start_unpredicted(self) -> None:
        row = match(1)
        row["expected_goals"] = None
        self.assertIsNone(features_before([row], "2023-24", 2, DEADLINE))
        self.assertIsNone(features_before([match(1)], "2023-24", 1, DEADLINE))

    def test_position_from_latest_eligible_record(self) -> None:
        old, latest, target = match(1, days=14), match(2), match(3, days=-1)
        old["position"], latest["position"], target["position"] = "GK", "DEF", "FWD"
        values = features_before([old, latest, target], "2023-24", 3, DEADLINE)["values"]
        self.assertEqual(
            (values["is_defender"], values["is_midfielder"], values["is_forward"]), (1, 0, 0)
        )


class AlignmentAndCalculationTests(unittest.TestCase):
    def test_quality_join_is_by_key_and_rejects_missing_duplicates(self) -> None:
        a, b = {"player_key": "a", "gameweek": 2}, {"player_key": "b", "gameweek": 2}
        flags = align_quality([a, b], [{**b, "flag": 2}, {**a, "flag": 1}])
        self.assertEqual(flags["a", 2]["flag"], 1)
        with self.assertRaisesRegex(ValueError, "keys differ"):
            align_quality([a, b], [a])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            align_quality([a, a], [a, a])

    def test_whitelist_order_and_unexpected_regressors(self) -> None:
        values = {f: float(i) for i, f in reversed(list(enumerate(FEATURES)))}
        row = {"values": values, "actual": 99999, "name": "ignored"}
        np.testing.assert_array_equal(matrix([row])[0], np.arange(len(FEATURES)))
        values["target_points"] = 999
        with self.assertRaisesRegex(ValueError, "unexpected"):
            matrix([row])

    def test_full_contribution_sum_and_known_ols_solution(self) -> None:
        x = np.vstack([np.zeros(len(FEATURES)), np.eye(len(FEATURES))])
        coefficients = np.arange(1, len(FEATURES) + 1) / 4
        model = LinearRegression().fit(x, 2 + x @ coefficients)
        row = {"values": dict(zip(FEATURES, np.arange(len(FEATURES))))}
        explanation = explain_prediction(model, row)
        self.assertAlmostEqual(explanation["intercept"], 2)
        np.testing.assert_allclose(model.coef_, coefficients, atol=1e-12)
        self.assertAlmostEqual(
            explanation["prediction"], 2 + np.arange(len(FEATURES)) @ coefficients
        )
        self.assertAlmostEqual(
            explanation["prediction"],
            explanation["intercept"] + sum(c["contribution"] for c in explanation["contributions"]),
        )

    def test_metrics_and_undefined_cases(self) -> None:
        self.assertEqual(
            metrics(np.array([0.0, 2.0]), np.array([1.0, 1.0])),
            {"n": 2, "mae": 1, "rmse": 1, "r2": 0},
        )
        self.assertIsNone(metrics(np.array([0.0, 0.0]), np.array([1.0, 1.0]))["r2"])
        self.assertEqual(metrics(np.array([]), np.array([]))["n"], 0)

    def test_split_purges_training_labels_not_ready_at_evaluation(self) -> None:
        train = {
            "season": "2023-24",
            "gameweek": 28,
            "deadline_utc": DEADLINE - timedelta(days=7),
            "target_ready_utc": DEADLINE - timedelta(days=1),
        }
        late = {**train, "target_ready_utc": DEADLINE}
        validation = {"season": "2023-24", "gameweek": 29, "deadline_utc": DEADLINE}
        fitting, evaluation, purged = split_rows([train, late, validation], "develop")
        self.assertEqual((fitting, evaluation, purged), ([train], [validation], 1))

    def test_final_split_never_trains_on_evaluation_season(self) -> None:
        train = {
            "season": "2023-24",
            "gameweek": 38,
            "deadline_utc": DEADLINE - timedelta(days=100),
            "target_ready_utc": DEADLINE - timedelta(days=99),
        }
        validation = {"season": "2024-25", "gameweek": 2, "deadline_utc": DEADLINE}
        retrospective = {
            "season": "2025-26",
            "gameweek": 2,
            "deadline_utc": DEADLINE + timedelta(days=365),
        }
        fitting, evaluation, _ = split_rows([retrospective, train, validation], "evaluate")
        self.assertEqual(fitting, [train])
        self.assertEqual(evaluation, [retrospective, validation])

    def test_target_reconciliation_and_zero_minute_rows(self) -> None:
        history, target = match(1, days=14), match(2, days=-1, points=0)
        target["minutes"] = 0
        outcomes = [
            {
                "season": "2023-24",
                "player_key": "2023-24:1",
                "gameweek": gw,
                "name": "Example",
                "target_complete": True,
                "fixture_coverage": "complete_final_schedule",
                "target_points": points,
                "observed_minutes": minutes,
            }
            for gw, points, minutes in ((1, 5, 90), (2, 0, 0))
        ]
        quality = [{**r, "evaluation_eligible": True} for r in outcomes]
        deadlines = {("2023-24", 1): DEADLINE - timedelta(days=15), ("2023-24", 2): DEADLINE}
        rows, _ = build_rows([history, target], outcomes, list(reversed(quality)), deadlines)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actual"], 0)
        outcomes[1]["target_points"] = 100
        with self.assertRaisesRegex(ValueError, "disagree"):
            build_rows([history, target], outcomes, quality, deadlines)


if __name__ == "__main__":
    unittest.main()
