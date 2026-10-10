from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fpl_forfeit.evaluation import evaluate, main, prediction_record, timeline
from tests.test_forecast import fixture_state


class RecordingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state(3)
        self.record = prediction_record(self.state, samples=30)
        self.final = replace(
            self.state,
            event_final=True,
            fetched_at=self.state.fetched_at + timedelta(days=1),
            managers=tuple(
                replace(m, official_points=50 + m.entry_id) for m in self.state.managers
            ),
        )

    def test_record_contains_reproduction_and_recording_metadata(self):
        self.assertEqual(self.record["schema_version"], 2)
        self.assertEqual(self.record["provenance"], "recorded")
        self.assertTrue(self.record["recorded_at"])
        self.assertEqual(self.record["source"], self.state.fetched_at.isoformat())
        self.assertIn("assumptions", self.record)
        self.assertIn("coverage", self.record)
        result = evaluate([self.record], self.final)
        self.assertIn("mean_binary_brier", result["snapshots"][0])

    def test_replay_is_not_accepted_as_a_forecast_recorded_live(self):
        replay = prediction_record(self.state, samples=30, provenance="replay")
        with self.assertRaisesRegex(ValueError, "Replays"):
            evaluate([replay], self.final)

    def test_legacy_record_is_not_assumed_to_be_recorded_before_outcome(self):
        legacy = copy.deepcopy(self.record)
        legacy.pop("recorded_at")
        with self.assertRaisesRegex(ValueError, "legacy"):
            evaluate([legacy], self.final)

    def test_old_input_relabelled_recorded_and_after_final_creation_are_rejected(self):
        for at in (
            self.state.fetched_at + timedelta(hours=1),
            self.final.fetched_at + timedelta(hours=1),
        ):
            invalid = {**self.record, "recorded_at": at.isoformat()}
            with self.assertRaisesRegex(ValueError, "before"):
                evaluate([invalid], self.final)

    def test_probability_timeline_sorts_sources_and_exposes_provenance(self):
        second = copy.deepcopy(self.record)
        second["source"] = (self.state.fetched_at + timedelta(minutes=5)).isoformat()
        second["managers"][0]["last_probability"] += 0.1
        second["provenance"] = "replay"
        result = timeline([second, self.record])
        self.assertEqual(result["snapshots"][0]["source"], self.record["source"])
        self.assertEqual(result["snapshots"][1]["provenance"], "replay")
        self.assertAlmostEqual(result["snapshots"][1]["managers"][0]["risk_change"], 0.1)

    def test_timeline_rejects_mixed_ties_versions_and_missing_members(self):
        for change in (
            {"tie_rule": "strict"},
            {"model_version": "different"},
            {"managers": self.record["managers"][:2]},
        ):
            with self.assertRaises(ValueError):
                timeline([self.record, {**self.record, **change}])

    def test_evaluation_rejects_duplicate_member_rows(self):
        invalid = {
            **self.record,
            "managers": self.record["managers"] + [self.record["managers"][0]],
        }
        with self.assertRaisesRegex(ValueError, "Membership"):
            evaluate([invalid], self.final)

    def test_cli_records_inputs_and_marks_replay_output(self):
        raw = {
            "schema_version": 1,
            "league_id": 123,
            "fetched_at": self.state.fetched_at.isoformat(),
        }
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch("fpl_forfeit.evaluation.FPLClient.fetch_snapshot", return_value=raw),
                patch("fpl_forfeit.evaluation.state_from_snapshot", return_value=self.state),
                patch(
                    "sys.argv",
                    ["evaluation", "record", "123", "--directory", folder, "--samples", "10"],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
            inputs = list(Path(folder).glob("*-input.json"))
            forecasts = list(Path(folder).glob("*-forecast.json"))
            self.assertEqual(len(inputs), 1)
            self.assertEqual(json.loads(inputs[0].read_text()), raw)
            replay = Path(folder) / "replay.json"
            with (
                patch("fpl_forfeit.evaluation.state_from_snapshot", return_value=self.state),
                patch(
                    "sys.argv",
                    [
                        "evaluation",
                        "replay",
                        str(inputs[0]),
                        "--samples",
                        "10",
                        "--output",
                        str(replay),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
            original, replayed = (
                json.loads(forecasts[0].read_text()),
                json.loads(replay.read_text()),
            )
            self.assertEqual(original["managers"], replayed["managers"])
            self.assertEqual(replayed["provenance"], "replay")

    def test_cli_final_preserves_all_members_and_rejects_provisional_result(self):
        with tempfile.TemporaryDirectory() as folder:
            args = ["evaluation", "final", "123", "--gameweek", "1", "--directory", folder]
            with (
                patch(
                    "fpl_forfeit.evaluation.FPLClient.fetch_snapshot",
                    return_value={"schema_version": 1},
                ),
                patch("fpl_forfeit.evaluation.state_from_snapshot", return_value=self.state),
                patch("sys.argv", args),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                main()
            self.assertFalse(list(Path(folder).glob("*.json")))
            with (
                patch(
                    "fpl_forfeit.evaluation.FPLClient.fetch_snapshot",
                    return_value={"schema_version": 1},
                ) as fetch,
                patch("fpl_forfeit.evaluation.state_from_snapshot", return_value=self.final),
                patch("sys.argv", args),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
            fetch.assert_called_once_with(123, gameweek=1, max_entries=50)
            self.assertEqual(len(list(Path(folder).glob("*-final-input.json"))), 1)
