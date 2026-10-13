from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fpl_forfeit.api import parse_ml_history
from fpl_forfeit.assistant.ml_features import FEATURE_COLUMNS, STATS, historical_features
from fpl_forfeit.assistant.ml_inference import LiveML, RidgeModel, predict_player
from fpl_forfeit.assistant.prospective import ForecastArchive, immutable
from fpl_forfeit.forecasting.horizon import forecast_horizon
from tests.assistant_fixtures import NOW, FixtureClient, catalogue


def source():
    kickoff = (NOW - timedelta(days=7)).isoformat()
    fixture = {
        "id": 100,
        "event": 6,
        "finished": True,
        "kickoff_time": kickoff,
        "team_h": 1,
        "team_a": 2,
    }
    row = {
        **dict.fromkeys(STATS, 0),
        "element": 1,
        "fixture": 100,
        "round": 6,
        "was_home": True,
        "opponent_team": 2,
        "kickoff_time": kickoff,
        "minutes": 90,
        "starts": 1,
        "total_points": 6,
        "expected_goals": "0.2",
        "expected_assists": "0.1",
    }
    upcoming = {
        "id": 111,
        "event": 7,
        "finished": False,
        "kickoff_time": (NOW + timedelta(days=7)).isoformat(),
        "team_h": 1,
        "team_a": 2,
    }
    snapshot = {
        "fixtures": [fixture, upcoming],
        "bootstrap": {
            "events": [{"id": 1, "deadline_time": "2026-08-01T10:00:00Z"}],
            "elements": [{"id": 1, "code": 555}],
        },
        "source_finished_utc": NOW.isoformat(),
    }
    history = {
        "payload": {"history": [row], "history_past": [{"total_points": 9999}]},
        "retrieved_at": NOW.isoformat(),
    }
    return snapshot, history


class LiveRidgeTests(unittest.TestCase):
    def setUp(self):
        self.model = RidgeModel()
        self.forecast = forecast_horizon(catalogue(), (7, 8), samples=4)
        self.forecast.players[1][7].update(coverage="published", fixture_ids=[111])
        self.snapshot, self.history = source()

    def predict(self, history=None):
        return predict_player(
            self.model,
            self.forecast,
            self.snapshot,
            1,
            self.history if history is None else history,
            NOW,
        )

    def test_full_historical_feature_contract_parity(self):
        from ml.dataset import FEATURE_COLUMNS as original_schema
        from ml.dataset import historical_features as original

        rows = parse_ml_history(self.history["payload"], self.snapshot["fixtures"], 1, 7, NOW)
        self.assertEqual(FEATURE_COLUMNS, original_schema)
        self.assertEqual(historical_features(rows), original(rows))
        # A second eligible DGW leg, zero minutes and unknown xA preserve denominators/nulls.
        extra = {
            **rows[0],
            "fixture_id": 101,
            "minutes": 0,
            "starts": 0,
            "expected_assists": None,
            "kickoff_utc": rows[0]["kickoff_utc"] + timedelta(days=1),
        }
        self.assertEqual(historical_features(rows + [extra]), original(rows + [extra]))

    def test_next_gw_only_no_fixture_opponent_or_availability_inputs(self):
        result = self.predict()
        self.assertEqual(result[7]["source"], "ridge")
        self.assertEqual(result[8]["source"], "statistical_fallback")
        self.assertIn("multi_step_history_not_observed", result[8]["fallback_reasons"])
        self.assertIsNone(result[8]["difference"])
        self.assertNotIn("opponent_team", FEATURE_COLUMNS)

    def test_new_missing_and_transferred_players_fall_back_without_imputing_history(self):
        no_history = {**self.history, "payload": {"history": []}}
        self.assertIn("no_eligible_season_history", self.predict(no_history)[7]["fallback_reasons"])
        bad = deepcopy(self.history)
        bad["payload"]["history"][0]["expected_goals"] = None
        self.assertIn("missing_core_history", self.predict(bad)[7]["fallback_reasons"])
        self.snapshot["fixtures"][0]["team_h"] = 3
        self.assertIn("transfer_since_last_observation", self.predict()[7]["fallback_reasons"])

    def test_blanks_doubles_and_unknown_schedule_do_not_scale_ridge(self):
        for coverage, fixtures, reason in (
            ("confirmed_blank", [], "blank_outside_training_population"),
            ("published", [1, 2], "double_fixture_effect_not_trained"),
            ("partial", [1], "schedule_incomplete"),
        ):
            self.forecast.players[1][7].update(coverage=coverage, fixture_ids=fixtures)
            row = self.predict()[7]
            self.assertIsNone(row["expected_points"])
            self.assertEqual(
                row["comparison_points"], self.forecast.players[1][7]["expected_points"]
            )
            self.assertIn(reason, row["fallback_reasons"])

    def test_future_rows_and_24_hour_buffer_do_not_enter_features(self):
        before = self.predict()[7]["expected_points"]
        future = {**self.history["payload"]["history"][0], "round": 7, "total_points": 9999}
        self.history["payload"]["history"].append(future)
        self.assertEqual(before, self.predict()[7]["expected_points"])
        self.history["payload"]["history"] = [self.history["payload"]["history"][0]]
        stamp = (NOW - timedelta(hours=24)).isoformat()
        self.history["payload"]["history"][0]["kickoff_time"] = stamp
        self.snapshot["fixtures"][0]["kickoff_time"] = stamp
        self.assertIn("no_eligible_season_history", self.predict()[7]["fallback_reasons"])

    def test_unknown_noncore_values_use_training_medians_and_bad_schema_fails(self):
        values = historical_features(
            parse_ml_history(self.history["payload"], self.snapshot["fixtures"], 1, 7, NOW)
        )
        values["pre_gw_bps_sum"] = None
        self.assertIsInstance(self.model.predict(values), float)
        with self.assertRaises(ValueError):
            self.model.predict({**values, "xP": 999})
        self.assertEqual(self.model.predict(values), RidgeModel().predict(values))

    def test_missing_model_never_raises_through_application(self):
        worker = LiveML(FixtureClient(), now=lambda: NOW)
        with (
            patch("fpl_forfeit.assistant.ml_inference.RidgeModel", side_effect=FileNotFoundError),
            self.assertLogs("fpl_forfeit.assistant.ml_inference", level="ERROR"),
        ):
            worker.build(self.forecast, self.snapshot)
        self.assertEqual(worker.status["state"], "unavailable")
        row = worker.player(1, 7, self.forecast.players[1][7])
        self.assertEqual(row["source"], "statistical_fallback")

    def test_negative_estimates_are_not_clipped(self):
        self.model.data["coefficients"] = [0.0] * len(self.model.data["kept"])
        self.model.data["intercept"] = -0.5
        self.assertEqual(self.predict()[7]["expected_points"], -0.5)

    def test_availability_flags_route_to_statistical_model_not_a_ridge_multiplier(self):
        roster = dict(self.forecast.catalogue.players)
        roster[1] = replace(roster[1], availability_status="d", chance_of_playing=75)
        self.forecast.catalogue = replace(self.forecast.catalogue, players=roster)
        row = self.predict()[7]
        self.assertIsNone(row["expected_points"])
        self.assertIn("availability_requires_statistical_adjustment", row["fallback_reasons"])

    def test_missing_upcoming_kickoff_is_explicit_fallback(self):
        self.snapshot["fixtures"][1]["kickoff_time"] = None
        self.assertIn("published_fixture_time_unavailable", self.predict()[7]["fallback_reasons"])

    def test_refresh_discards_old_jobs_and_never_mixes_forecast_generations(self):
        worker = LiveML(FixtureClient(), now=lambda: NOW)
        old_generation = (self.forecast.catalogue.fetched_at.isoformat(), self.forecast.gameweeks)
        worker.generation = old_generation
        worker.rows = {1: self.predict()}
        different = ("new-source", self.forecast.gameweeks)
        self.assertEqual(
            worker.player(1, 7, self.forecast.players[1][7], generation=different)["source"],
            "statistical_fallback",
        )
        new = deepcopy(self.forecast)
        new.catalogue = replace(
            new.catalogue, fetched_at=new.catalogue.fetched_at + timedelta(seconds=1)
        )
        worker.running = True  # a previous job is still collecting histories
        worker.request(new, self.snapshot)
        self.assertEqual(worker.rows, {})
        worker.build(self.forecast, self.snapshot, old_generation)
        self.assertEqual(worker.rows, {})
        self.assertEqual(worker.status["state"], "loading")
        with patch("fpl_forfeit.assistant.ml_inference.Thread") as thread:
            worker.request(new, self.snapshot)
            thread.return_value.start.assert_called_once()

    def test_private_horizon_uses_only_published_future_deadlines(self):
        from fpl_forfeit.assistant.service import AssistantService

        class Client(FixtureClient):
            def forecast_catalogue(self):
                original = catalogue()
                return replace(
                    original,
                    gameweeks=tuple(
                        replace(g, deadline=None) if g.id == 8 else g for g in original.gameweeks
                    ),
                )

        service = AssistantService(Client(), samples=4, now=lambda: NOW)
        self.assertNotIn(8, service.forecast().gameweeks)
        self.assertEqual(service.forecast().gameweeks[0], 7)

    def test_records_are_immutable_and_late_or_future_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = ForecastArchive(Path(folder))
            predictions = {1: self.predict()}
            path = archive.record(
                self.forecast, self.snapshot, {1: self.history}, predictions, NOW, self.model
            )
            original = path.read_bytes()
            archive.record(self.forecast, self.snapshot, {1: self.history}, {}, NOW, self.model)
            self.assertEqual(path.read_bytes(), original)
            saved = json.loads(gzip.decompress(original))
            self.assertNotIn("squad", saved)
            deadline = next(g.deadline for g in self.forecast.catalogue.gameweeks if g.id == 7)
            with self.assertRaises(ValueError):
                archive.record(self.forecast, self.snapshot, {}, {}, deadline, self.model)
            bad = deepcopy(self.history)
            bad["retrieved_at"] = (NOW + timedelta(seconds=1)).isoformat()
            with self.assertRaises(ValueError):
                archive.record(self.forecast, self.snapshot, {1: bad}, {}, NOW, self.model)

    def test_canonical_hashes_survive_numeric_key_json_round_trips(self):
        from fpl_forfeit.assistant.prospective import canonical

        data = {2: {8: 1.5}, 10: {7: -0.5}}
        self.assertEqual(canonical(data), canonical(json.loads(canonical(data))))

    def test_generation_after_deadline_cannot_be_labelled_as_early_feature_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            deadline = next(g.deadline for g in self.forecast.catalogue.gameweeks if g.id == 7)
            with self.assertRaises(ValueError):
                ForecastArchive(Path(folder)).record(
                    self.forecast,
                    self.snapshot,
                    {1: self.history},
                    {1: self.predict()},
                    deadline,
                    self.model,
                    feature_asof=NOW,
                )

    def test_actuals_only_after_official_final_and_unknown_points_remain_null(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = ForecastArchive(Path(folder))
            immutable(
                Path(folder) / "test.index.json",
                json.dumps({"season": "2026-27", "gameweeks": [7]}).encode(),
            )

            class Client:
                calls = 0

                def final_event_snapshot(self, gw):
                    self.calls += 1
                    return {
                        "payload": {
                            "elements": [
                                {"id": 1, "stats": {"total_points": -1}},
                                {"id": 2, "stats": {}},
                            ]
                        },
                        "retrieved_at": NOW.isoformat(),
                    }

            client = Client()
            bootstrap = {
                "events": [
                    {"id": 1, "deadline_time": "2026-08-01T10:00:00Z"},
                    {"id": 7, "finished": True, "data_checked": False},
                ]
            }
            self.assertEqual(archive.finalize(bootstrap, client), 0)
            bootstrap["events"][1]["data_checked"] = True
            self.assertEqual(archive.finalize(bootstrap, client), 1)
            self.assertEqual(archive.finalize(bootstrap, client), 0)
            self.assertEqual(client.calls, 1)
            record = json.loads(gzip.decompress(next(Path(folder).glob("actual*.gz")).read_bytes()))
            self.assertIsNone(record["actual_points"]["2"])


if __name__ == "__main__":
    unittest.main()
