from __future__ import annotations

import base64
import gzip
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fpl_forfeit.assistant.ml_inference import RidgeModel, predict_player
from fpl_forfeit.assistant.prospective import ForecastArchive, canonical
from fpl_forfeit.forecasting.horizon import forecast_horizon
from ml.recording.archive import (
    create_bundle,
    digest,
    policy,
    read_bundle,
    tag_for,
    validate_release,
)
from ml.recording.evaluate import evaluate
from ml.recording.github import GitHubStore, StorageError
from ml.recording.run import RetryClient, due_slot, missing_stages
from tests.assistant_fixtures import NOW, catalogue
from tests.test_live_ridge import source


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.forecast = forecast_horizon(catalogue(), (7, 8), samples=4)
        self.forecast.players[1][7].update(coverage="published", fixture_ids=[111])
        self.snapshot, self.history = source()
        self.snapshot["source_started_utc"] = NOW.isoformat()
        self.model = RidgeModel()
        self.predictions = {
            1: predict_player(self.model, self.forecast, self.snapshot, 1, self.history, NOW)
        }
        # Keep the comparison fixture internally consistent and intentionally small.
        self.forecast.players = {1: self.forecast.players[1]}
        self.path = ForecastArchive(self.root / "raw").record(
            self.forecast,
            self.snapshot,
            {1: self.history},
            self.predictions,
            NOW,
            self.model,
            feature_asof=NOW,
        )
        self.context = {"code_commit": "a" * 40, "run_id": "123", "event": "schedule"}
        self.bundle = create_bundle(
            self.path, "advance", 7, self.context, self.root / "candidate" / "capture.zip"
        )
        self.manifest, self.record = read_bundle(self.bundle)
        self.deadline = next(g.deadline for g in self.forecast.catalogue.gameweeks if g.id == 7)

    def release(self, *, stage="advance", draft=False):
        return {
            "id": 99,
            "tag_name": tag_for(7, stage),
            "draft": draft,
            "immutable": not draft,
            "body": "```json\n"
            + json.dumps(
                {"bundle_sha256": digest(self.bundle.read_bytes()), "target_gameweeks": [7, 8]}
            )
            + "\n```",
            "published_at": None if draft else NOW.isoformat(),
            "html_url": "https://github.com/example/release",
            "assets": [
                {
                    "name": "capture.zip",
                    "state": "uploaded",
                    "size": self.bundle.stat().st_size,
                    "digest": "sha256:" + digest(self.bundle.read_bytes()),
                    "created_at": NOW.isoformat(),
                }
            ],
        }

    def bootstrap(self, lead):
        return {
            "events": [
                {"id": 1, "deadline_time": "2026-08-21T17:30:00Z"},
                {"id": 7, "deadline_time": (NOW + timedelta(hours=lead)).isoformat()},
            ]
        }

    def actual(self, value=5):
        return {
            "season": "2026-27",
            "gameweek": 7,
            "official_final_event": {
                "id": 7,
                "finished": True,
                "data_checked": True,
                "deadline_time": self.deadline.isoformat(),
            },
            "actual_points": {"1": value},
        }

    def test_deadline_windows_use_actual_start_time_not_scheduled_time(self):
        for hours, stage in (
            (110, "advance"),
            (25, "advance"),
            (24, "update"),
            (6, "final"),
            (2, "final"),
        ):
            self.assertEqual(due_slot(self.bootstrap(hours), NOW, []), (7, stage))
        for hours in (121, 1.5, 0, -2):
            self.assertIsNone(due_slot(self.bootstrap(hours), NOW, []))
        # A delayed workflow jumps to the currently valid slot, never recreates advance.
        self.assertEqual(due_slot(self.bootstrap(4), NOW, []), (7, "final"))
        self.assertIn(
            "GW07 advance window missed; never backfilled",
            missing_stages(self.bootstrap(4), NOW, []),
        )

    def test_one_immutable_capture_per_slot_and_unpublished_drafts_are_not_success(self):
        self.assertIsNone(due_slot(self.bootstrap(48), NOW, [self.release()]))
        self.assertEqual(
            due_slot(self.bootstrap(48), NOW, [self.release(draft=True)]), (7, "advance")
        )
        unsafe = self.release()
        unsafe["immutable"] = False
        with self.assertRaises(ValueError):
            validate_release(unsafe, self.bundle)

    def test_season_reset_and_explicit_stop_date(self):
        new_season = self.bootstrap(48)
        new_season["events"][0]["deadline_time"] = "2027-08-20T17:30:00Z"
        self.assertIsNone(due_slot(new_season, NOW, []))
        self.assertIsNone(due_slot(self.bootstrap(48), NOW.replace(year=2028), []))

    def test_release_bundle_preserves_original_bytes_and_cannot_be_replaced(self):
        manifest, record = validate_release(self.release(), self.bundle)
        self.assertEqual(record, self.record)
        self.assertEqual(manifest["record_sha256"], digest(self.path.read_bytes()))
        self.assertEqual(record["ml_predictions"], json.loads(canonical(self.predictions)))
        altered = json.loads(gzip.decompress(self.path.read_bytes()))
        altered["note"] = "changed"
        alternate = self.root / "altered.gz"
        alternate.write_bytes(gzip.compress(canonical(altered), mtime=0))
        with self.assertRaises(ValueError):
            create_bundle(alternate, "advance", 7, self.context, self.bundle)

    def test_raw_source_tampering_and_future_input_timestamps_fail(self):
        for mutate in (
            lambda r: r["official_snapshot"]["bootstrap"].update(fake=True),
            lambda r: r["official_histories"]["1"].update(
                retrieved_at=(NOW + timedelta(seconds=1)).isoformat()
            ),
        ):
            record = deepcopy(self.record)
            mutate(record)
            path = self.root / "bad.gz"
            path.write_bytes(gzip.compress(canonical(record), mtime=0))
            with self.assertRaises(ValueError):
                create_bundle(path, "advance", 7, self.context, self.root / "bad.zip")

    def test_provider_timestamps_prevent_backdating_or_late_publication(self):
        for field in ("published_at", "created_at"):
            release = self.release()
            if field == "created_at":
                release["assets"][0][field] = self.deadline.isoformat()
            else:
                release[field] = self.deadline.isoformat()
            with self.assertRaises(ValueError):
                validate_release(release, self.bundle)
        with self.assertRaises(ValueError):
            validate_release({**self.release(), "tag_name": tag_for(8, "advance")}, self.bundle)

    def test_existing_published_record_is_never_overwritten_with_newer_prediction(self):
        store = GitHubStore("example/repo", now=lambda: NOW)
        existing = self.root / "existing" / "capture.zip"
        existing.parent.mkdir()
        existing.write_bytes(self.bundle.read_bytes() + b"different")
        with (
            patch.object(store, "release", return_value=self.release()),
            patch.object(store, "fetch", return_value=existing),
            patch.object(store, "command") as command,
        ):
            with self.assertRaises(StorageError):
                store.publish(self.bundle, self.root)
            command.assert_not_called()

    def test_complete_draft_recovers_original_bytes_and_late_draft_cannot_publish(self):
        store = GitHubStore("example/repo", now=lambda: NOW)
        draft = self.release(draft=True)
        with (
            patch.object(store, "fetch", return_value=self.bundle),
            patch.object(store, "api") as api,
            patch.object(store, "release", return_value=self.release()),
        ):
            store.finish_draft(draft, self.root)
            self.assertEqual(api.call_args.kwargs["body"]["draft"], False)
        store.now = lambda: self.deadline
        with (
            patch.object(store, "fetch", return_value=self.bundle),
            patch.object(store, "api") as api,
        ):
            with self.assertRaises(StorageError):
                store.finish_draft(draft, self.root)
            api.assert_not_called()

    def test_exact_candidate_recovery_rejects_reconstruction(self):
        store = GitHubStore("example/repo", now=lambda: NOW)
        draft = self.release(draft=True)
        draft["assets"] = []
        draft["body"] = '```json\n{"bundle_sha256":"other-earlier-bytes"}\n```'
        with (
            patch.object(store, "release", return_value=draft),
            patch.object(store, "command") as command,
        ):
            with self.assertRaises(StorageError):
                store.recover(self.bundle, self.root)
            command.assert_not_called()

    def test_transient_github_requests_retry_without_clobber_or_deletion(self):
        store = GitHubStore("example/repo", sleep=lambda _: None)
        failed = subprocess.CompletedProcess([], 1, b"", b"HTTP 503 service unavailable")
        success = subprocess.CompletedProcess([], 0, b"[]", b"")
        with patch("ml.recording.github.subprocess.run", side_effect=[failed, success]) as execute:
            self.assertEqual(store.releases(), [])
            self.assertEqual(execute.call_count, 2)
            self.assertNotIn("--clobber", str(execute.call_args_list))

    def test_official_request_retry_is_transport_only(self):
        from fpl_forfeit.api import FPLAPIError

        client = RetryClient()
        with (
            patch(
                "ml.recording.run.FPLClient._get",
                side_effect=[FPLAPIError("timeout"), {"history": []}],
            ) as get,
            patch("ml.recording.run.time.sleep"),
        ):
            self.assertEqual(client._get("element-summary/1/"), {"history": []})
            self.assertEqual(get.call_count, 2)

    def test_monthly_keepalive_writes_only_separate_branch_and_no_dataset(self):
        store = GitHubStore("example/repo", now=lambda: NOW)
        old = {
            "content": base64.b64encode(canonical({"month": "2026-09"})).decode(),
            "sha": "old-file",
        }
        with patch.object(store, "api", side_effect=[old, {}]) as api:
            store.heartbeat("a" * 40)
            request = api.call_args.kwargs["body"]
            self.assertEqual(request["branch"], policy()["heartbeat_branch"])
            self.assertNotEqual(request["branch"], "main")
            self.assertLess(len(base64.b64decode(request["content"])), 512)
        old["content"] = base64.b64encode(canonical({"month": NOW.strftime("%Y-%m")})).decode()
        with patch.object(store, "api", return_value=old) as api:
            store.heartbeat("a" * 40)
            self.assertEqual(api.call_count, 1)

    def test_evaluation_is_paired_preserves_zero_and_negative_points(self):
        result = evaluate([(self.manifest, self.record)], {7: self.actual(-1)})
        self.assertEqual(result["metrics"]["all_roster_paired"]["statistical"]["n"], 1)
        observed = self.record["statistical_predictions"]["1"]["7"]["expected_points"]
        self.assertAlmostEqual(
            result["metrics"]["all_roster_paired"]["statistical"]["bias"], observed + 1
        )
        self.assertEqual(
            evaluate([(self.manifest, self.record)], {7: self.actual(0)})["metrics"][
                "all_roster_paired"
            ]["statistical"]["n"],
            1,
        )
        self.assertEqual(
            evaluate([(self.manifest, self.record)], {7: self.actual(None)})["exclusions"][
                "unknown_actual"
            ],
            1,
        )

    def test_capture_selection_never_uses_outcomes_or_future_gameweeks(self):
        final = {**self.manifest, "stage": "final"}
        deliberately_worse = deepcopy(self.record)
        deliberately_worse["ml_predictions"]["1"]["7"]["expected_points"] = 999
        deliberately_worse["ml_predictions"]["1"]["7"]["comparison_points"] = 999
        options = [(self.manifest, self.record), (final, deliberately_worse)]
        result = evaluate(options, {7: self.actual(5)})
        self.assertEqual(result["gameweeks"][0]["stage"], "final")
        self.assertEqual(result["metrics"]["direct_ridge_paired"]["ridge"]["mae"], 994)
        changed = deepcopy(deliberately_worse)
        changed["ml_predictions"]["1"]["8"]["comparison_points"] = -99999
        self.assertEqual(
            result, evaluate([(self.manifest, self.record), (final, changed)], {7: self.actual(5)})
        )

    def test_missing_nonfinal_actuals_and_changed_deadlines_are_excluded(self):
        self.assertEqual(
            evaluate([(self.manifest, self.record)], {})["exclusions"]["not_officially_final"], 1
        )
        bad = self.actual()
        bad["official_final_event"]["data_checked"] = False
        with self.assertRaises(ValueError):
            evaluate([(self.manifest, self.record)], {7: bad})
        bad = self.actual()
        bad["official_final_event"]["deadline_time"] = NOW.isoformat()
        self.assertEqual(
            evaluate([(self.manifest, self.record)], {7: bad})["exclusions"][
                "final_deadline_changed_or_crossed"
            ],
            1,
        )

    def test_actual_bundle_matches_raw_official_points_including_unknown_values(self):
        after = self.deadline + timedelta(days=3)
        payload = {"elements": [{"id": 1, "stats": {"total_points": -1}}, {"id": 2, "stats": {}}]}
        record = {
            **self.actual(-1),
            "actual_points": {"1": -1, "2": None},
            "source": {"payload": payload, "retrieved_at": after.isoformat()},
            "source_sha256": digest(canonical(payload)),
        }
        source = self.root / "actual.gz"
        source.write_bytes(gzip.compress(canonical(record), mtime=0))
        target = create_bundle(
            source, "actual", 7, self.context, self.root / "actual" / "capture.zip"
        )
        manifest, verified = read_bundle(target)
        self.assertEqual(verified["actual_points"]["1"], -1)
        self.assertIsNone(verified["actual_points"]["2"])
        self.assertEqual(manifest["stage"], "actual")
        record["actual_points"]["2"] = 0
        source.write_bytes(gzip.compress(canonical(record), mtime=0))
        with self.assertRaises(ValueError):
            create_bundle(source, "actual", 7, self.context, self.root / "corrupted.zip")

    def test_orchestration_continues_actuals_and_keepalive_after_forecast_failure(self):
        from ml.recording.run import run

        store = GitHubStore("example/repo", now=lambda: NOW)
        client = unittest.mock.Mock()
        client.forecast_snapshot.return_value = {**self.snapshot, "bootstrap": self.bootstrap(48)}
        with (
            patch.object(store, "releases", return_value=[]),
            patch.object(store, "release", return_value=None),
            patch.object(store, "heartbeat") as heartbeat,
            patch("ml.recording.run.context", return_value=self.context),
            patch(
                "ml.recording.run.capture_forecast", side_effect=RuntimeError("official API outage")
            ),
            patch("ml.recording.run.capture_actuals", return_value=[]) as actuals,
        ):
            result = run(store, client, self.root, now=lambda: NOW)
            self.assertEqual(result["state"], "failed")
            self.assertIn("official API outage", result["errors"])
            actuals.assert_called_once()
            heartbeat.assert_called_once()

    def test_orchestration_never_calls_model_after_target_deadline(self):
        from ml.recording.run import run

        store = GitHubStore("example/repo", now=lambda: NOW)
        client = unittest.mock.Mock()
        client.forecast_snapshot.return_value = {**self.snapshot, "bootstrap": self.bootstrap(-2)}
        with (
            patch.object(store, "releases", return_value=[]),
            patch.object(store, "heartbeat"),
            patch("ml.recording.run.context", return_value=self.context),
            patch("ml.recording.run.capture_forecast") as forecast,
            patch("ml.recording.run.capture_actuals", return_value=[]),
        ):
            result = run(store, client, self.root, now=lambda: NOW)
            forecast.assert_not_called()
            self.assertIsNone(result["due_slot"])
            self.assertEqual(len(result["gaps"]), 3)

    def test_successful_forecast_publish_is_not_mistaken_for_final_actuals(self):
        from ml.recording.run import run

        store = GitHubStore("example/repo", now=lambda: NOW)
        client = unittest.mock.Mock()
        client.forecast_snapshot.return_value = {**self.snapshot, "bootstrap": self.bootstrap(48)}
        with (
            patch.object(store, "releases", return_value=[]),
            patch.object(store, "release", return_value=None),
            patch.object(store, "heartbeat"),
            patch.object(store, "publish", return_value=self.release()),
            patch("ml.recording.run.context", return_value=self.context),
            patch("ml.recording.run.capture_forecast", return_value=self.bundle),
            patch("ml.recording.run.capture_actuals", return_value=[]),
        ):
            result = run(store, client, self.root, now=lambda: NOW)
            self.assertEqual(result["state"], "recorded")
            self.assertEqual(result["new_actual_gameweeks"], [])
            self.assertEqual(len(result["published"]), 1)

    def test_heartbeat_does_not_repeatedly_download_past_season_for_evaluation(self):
        from ml.recording.evaluate import from_releases

        store = GitHubStore("example/repo")
        with patch.object(store, "releases") as releases:
            self.assertEqual(from_releases(store, self.root, gameweeks=[])["gameweeks"], [])
            releases.assert_not_called()

    def test_missing_whole_gameweek_fails_until_explicitly_acknowledged_without_backfill(self):
        from ml.recording.run import run

        store = GitHubStore("example/repo", now=lambda: NOW)
        client = unittest.mock.Mock()
        client.forecast_snapshot.return_value = {**self.snapshot, "bootstrap": self.bootstrap(-2)}
        with (
            patch.object(store, "releases", return_value=[]),
            patch.object(store, "heartbeat"),
            patch("ml.recording.run.context", return_value=self.context),
            patch("ml.recording.run.capture_actuals", return_value=[]),
            patch.dict("os.environ", {"FPL_ACKNOWLEDGED_MISSING_GWS": ""}),
        ):
            self.assertEqual(run(store, client, self.root, now=lambda: NOW)["state"], "failed")
            with patch.dict("os.environ", {"FPL_ACKNOWLEDGED_MISSING_GWS": "7"}):
                result = run(store, client, self.root, now=lambda: NOW)
                self.assertEqual(result["state"], "recorded")
                self.assertEqual(result["acknowledged_missing_gameweeks"], [7])
                self.assertEqual(len(result["gaps"]), 3)
                self.assertEqual(result["published"], [])


if __name__ == "__main__":
    unittest.main()
