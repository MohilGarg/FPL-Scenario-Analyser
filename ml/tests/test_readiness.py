from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.dataset import (FEATURE_COLUMNS, FLOAT_STATS, INT_STATS, feature_values, gameweek_dataset,
                        historical_features, load_deadlines, timestamp)
from ml.deadlines import VERIFIED, extract_deadlines, write_deadlines
from ml.readiness import quality_metadata, readiness_report
from ml.prepare import write_parquet
from ml.sources import digest
from ml.tests.test_preparation import matches


def record(gw: int, *, fixture: int | None = None, season: str = "2024-25") -> dict:
    row = matches(season=season)[0]
    row.update(dict.fromkeys(INT_STATS, 0))
    row.update(dict.fromkeys(FLOAT_STATS, 0.0))
    row.update(gameweek=gw, fixture_id=fixture or 100 + gw, total_points=gw,
               expected_goals=gw / 10, expected_assists=gw / 20,
               minutes=90 if gw % 2 else 0, starts=1 if gw % 2 else 0,
               kickoff_utc=datetime(2024, 8, 1, 15, tzinfo=timezone.utc) + timedelta(days=3 * gw),
               team_gw_fixture_ids=[fixture or 100 + gw])
    return row


def archived_api(*, provisional: bool = False) -> tuple[dict, bytes]:
    start = datetime(2024, 8, 1, 11, tzinfo=timezone.utc)
    final_gw = 20 if provisional else 38
    captured = start + timedelta(days=7 * (final_gw - 1), hours=-2)
    events = []
    for gw in range(1, 39):
        deadline = start + timedelta(days=7 * (gw - 1))
        events.append({"id": gw, "deadline_time": deadline.isoformat(),
                       "deadline_time_epoch": int(deadline.timestamp()),
                       "finished": gw < final_gw, "is_next": gw == final_gw})
    # End-of-season player information is deliberately not parsed or used.
    body = json.dumps({"events": events, "elements": [{"total_points": 999999}]}).encode()
    wayback = captured.strftime("%Y%m%d%H%M%S")
    manifest = {"captured_at": captured.isoformat(), "event": final_gw,
                "event_deadline": events[final_gw - 1]["deadline_time"],
                "files": [{"name": "bootstrap-static.json.gz", "endpoint": "/bootstrap-static/",
                           "bytes": len(body), "sha256": digest(body)}],
                "backfill": {"season": "2024-25", "wayback_timestamp": wayback,
                             "source": f"https://web.archive.org/web/{wayback}id_/"
                                       "https://fantasy.premierleague.com/api/bootstrap-static/"}}
    return manifest, body


class DeadlineTests(unittest.TestCase):
    def test_completed_and_imminent_deadlines_have_payload_proof(self):
        manifest, body = archived_api()
        rows = extract_deadlines("2024-25", manifest, body, "https://example.test/source.gz")
        self.assertEqual(len(rows), 38)
        self.assertTrue(all(r["verification_status"] == VERIFIED for r in rows))
        self.assertEqual(rows[0]["evidence"], "finished_event_in_archived_api")
        self.assertEqual(rows[-1]["evidence"], "is_next_event_in_predeadline_api")
        self.assertEqual(rows[0]["deadline_utc"], "2024-08-01T11:00:00+00:00")

    def test_provisional_deadlines_stay_empty_and_cannot_be_loaded(self):
        manifest, body = archived_api(provisional=True)
        rows = extract_deadlines("2024-25", manifest, body, "https://example.test/source.gz")
        self.assertEqual(sum(r["verification_status"] == VERIFIED for r in rows), 20)
        self.assertEqual(rows[20]["deadline_utc"], "")
        self.assertTrue(rows[20]["reported_deadline_utc"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "deadlines.csv"
            write_deadlines(path, rows)
            self.assertEqual(len(load_deadlines(path)), 20)
            rows[20]["deadline_utc"] = rows[20]["reported_deadline_utc"]
            write_deadlines(path, rows)
            with self.assertRaisesRegex(ValueError, "Unverified"):
                load_deadlines(path)

    def test_bad_bytes_season_and_capture_timestamp_fail(self):
        manifest, body = archived_api()
        with self.assertRaisesRegex(ValueError, "body"):
            extract_deadlines("2024-25", manifest, body + b" ", "https://example.test/source.gz")
        with self.assertRaisesRegex(ValueError, "season"):
            extract_deadlines("2023-24", manifest, body, "https://example.test/source.gz")
        manifest["captured_at"] = "2024-09-01T10:00:00Z"
        with self.assertRaisesRegex(ValueError, "timestamp"):
            extract_deadlines("2024-25", manifest, body, "https://example.test/source.gz")

    def test_body_deadline_epoch_must_agree(self):
        manifest, body = archived_api()
        data = json.loads(body)
        data["events"][0]["deadline_time_epoch"] += 3600
        body = json.dumps(data).encode()
        manifest["files"][0].update(bytes=len(body), sha256=digest(body))
        with self.assertRaisesRegex(ValueError, "epoch"):
            extract_deadlines("2024-25", manifest, body, "https://example.test/source.gz")


class RollingTests(unittest.TestCase):
    def test_three_five_ten_match_and_gw_windows(self):
        data = [record(g) for g in range(1, 14)]
        cutoff = data[-1]["kickoff_utc"] - timedelta(hours=2)
        row = gameweek_dataset(data, {("2024-25", 13): cutoff})[-1]
        for n, points in ((3, 33), (5, 50), (10, 75)):
            for unit in ("matches", "gws"):
                prefix = f"pre_gw_last{n}_{unit}"
                self.assertEqual(row[f"{prefix}_count"], n)
                self.assertFalse(row[f"{prefix}_short_history"])
                self.assertEqual(row[f"{prefix}_total_points_sum"], points)
                self.assertAlmostEqual(row[f"{prefix}_expected_goals_sum"], points / 10)
                self.assertAlmostEqual(row[f"{prefix}_expected_assists_mean"], points / 20 / n)
        self.assertEqual(row["pre_gw_total_points_sum"], 78)
        self.assertEqual(row["pre_gw_minutes_sum"], 540)
        self.assertEqual(row["pre_gw_appearance_rate"], 0.5)

    def test_double_separates_match_and_gw_denominators(self):
        data = [record(g) for g in range(1, 14)]
        first = data[-2]
        first["team_gw_fixture_ids"] = [112, 212]
        extra = {**first, "fixture_id": 212, "total_points": 50,
                 "kickoff_utc": first["kickoff_utc"] + timedelta(days=1)}
        cutoff = data[-1]["kickoff_utc"] - timedelta(hours=2)
        row = gameweek_dataset(data + [extra], {("2024-25", 13): cutoff})[-1]
        self.assertEqual(row["pre_gw_last3_matches_total_points_sum"], 73)
        self.assertEqual(row["pre_gw_last3_gws_total_points_sum"], 83)
        self.assertEqual(row["pre_gw_last3_gws_fixture_count"], 4)
        self.assertEqual(row["pre_gw_last3_gws_count"], 3)

    def test_future_outcomes_and_target_double_legs_cannot_change_features(self):
        data = [record(g) for g in range(1, 15)]
        first = data[11]
        first["team_gw_fixture_ids"] = [112, 212]
        data.append({**first, "fixture_id": 212, "kickoff_utc": first["kickoff_utc"] + timedelta(days=1)})
        cutoff = first["kickoff_utc"] - timedelta(hours=2)
        deadlines = {("2024-25", 12): cutoff}
        before = next(r for r in gameweek_dataset(data, deadlines) if r["gameweek"] == 12)
        changed = deepcopy(data)
        for m in changed:
            if m["gameweek"] >= 12:
                for s in (*INT_STATS, *FLOAT_STATS):
                    m[s] = 999
        after = next(r for r in gameweek_dataset(changed, deadlines) if r["gameweek"] == 12)
        self.assertEqual(feature_values(before), feature_values(after))
        self.assertNotEqual(before["target_points"], after["target_points"])

    def test_late_prior_gw_leg_is_excluded_by_time_not_just_gw(self):
        data = [record(g) for g in range(1, 14)]
        first = data[11]
        first["team_gw_fixture_ids"] = [112, 212]
        late = {**first, "fixture_id": 212, "kickoff_utc": data[-1]["kickoff_utc"] + timedelta(days=1)}
        deadlines = {("2024-25", 13): data[-1]["kickoff_utc"] - timedelta(hours=2)}
        before = feature_values(gameweek_dataset(data + [late], deadlines)[-1])
        late["total_points"] = 9999
        late["expected_goals"] = 9999
        after = feature_values(gameweek_dataset(data + [late], deadlines)[-1])
        self.assertEqual(before, after)
        self.assertEqual(before["pre_gw_last3_matches_total_points_sum"], 33)

    def test_season_boundaries_do_not_share_history_or_use_final_snapshots(self):
        old = record(38, season="2023-24")
        old["kickoff_utc"] = timestamp("2024-05-19T15:00:00Z")
        current = record(1)
        deadlines = {("2024-25", 1): current["kickoff_utc"] - timedelta(hours=2)}
        before = next(r for r in gameweek_dataset([old, current], deadlines) if r["season"] == "2024-25")
        old["total_points"] = -999
        old["expected_goals"] = 999
        after = next(r for r in gameweek_dataset([old, current], deadlines) if r["season"] == "2024-25")
        self.assertEqual(feature_values(before), feature_values(after))
        self.assertTrue(before["pre_gw_no_history"])
        self.assertIsNone(before["pre_gw_last10_matches_expected_goals_mean"])

    def test_missing_counts_short_windows_and_zero_minute_rates(self):
        data = [record(g) for g in (1, 2, 3)]
        data[2].update(minutes=45, starts=0, expected_goals=None)
        values = historical_features(data)
        self.assertAlmostEqual(values["pre_gw_last3_matches_appearance_rate"], 2 / 3)
        self.assertAlmostEqual(values["pre_gw_last3_matches_start_rate"], 1 / 3)
        self.assertIsNone(values["pre_gw_last3_matches_expected_goals_sum"])
        self.assertEqual(values["pre_gw_last3_matches_expected_goals_missing_count"], 1)
        self.assertEqual(values["pre_gw_last5_matches_count"], 3)
        self.assertTrue(values["pre_gw_last5_matches_short_history"])
        self.assertIsNotNone(values["pre_gw_last3_matches_expected_assists_mean"])

    def test_actual_kickoff_orders_match_window_not_fixture_id(self):
        data = [record(g) for g in (1, 2, 3, 4)]
        data[0]["kickoff_utc"] = data[-1]["kickoff_utc"] + timedelta(days=1)
        values = historical_features(data)
        self.assertEqual(values["pre_gw_last3_matches_total_points_sum"], 8)  # GW3+4+1


class QualityReadinessTests(unittest.TestCase):
    def test_ferguson_quarantine_keeps_values_and_is_outside_features(self):
        data = [record(g) for g in (1, 2)]
        for m in data:
            m.update(element=123, player_key="2024-25:123", name="Evan Ferguson")
        rows = gameweek_dataset(data)
        original = deepcopy(rows)
        profiles = [{"season": "2024-25", "season_total_mismatches": [
            {"element": 123, "history_points": 27, "snapshot_points": 28}]}]
        quality = quality_metadata(rows, profiles)
        self.assertEqual(rows, original)
        self.assertTrue(all(not q["evaluation_eligible"] for q in quality))
        self.assertEqual(quality[0]["season_history_points"], 27)
        self.assertEqual(quality[0]["season_snapshot_points"], 28)
        self.assertNotIn("evaluation_eligible", FEATURE_COLUMNS)
        self.assertNotIn("season_snapshot_points", FEATURE_COLUMNS)

    def test_readiness_distinguishes_partial_windows_and_unverified_calendar(self):
        data = [record(g) for g in range(1, 5)]
        deadlines = {("2024-25", m["gameweek"]): m["kickoff_utc"] - timedelta(hours=2) for m in data}
        rows = gameweek_dataset(data, deadlines)
        quality = quality_metadata(rows, [{"season": "2024-25", "season_total_mismatches": []}])
        records = [{"season": "2024-25", "gameweek": g, "deadline_utc": d.isoformat(),
                    "verification_status": VERIFIED} for (_, g), d in deadlines.items()]
        report = readiness_report(rows, quality, records, verified_archive=True)
        season = report["seasons"][0]
        self.assertEqual(season["rows_with_valid_features"], 3)
        self.assertEqual(season["cold_start_rows"], 1)
        xg = season["rolling_coverage"]["last3_matches"]["expected_goals"]
        self.assertEqual(xg, {"non_null": 3, "full_window": 1})
        self.assertEqual(report["readiness"], "not_ready")  # only 4 verified deadlines
        unverified = readiness_report(rows, quality, records, verified_archive=False)
        self.assertEqual(unverified["seasons"][0]["rows_with_valid_features"], 0)


@unittest.skipUnless(importlib.util.find_spec("pyarrow"), "Install ml/requirements.txt for Parquet check")
class RollingParquetTests(unittest.TestCase):
    def test_expected_assists_stay_fractional_and_unknown_flags_nullable(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        data = [record(g) for g in range(1, 5)]
        deadlines = {("2024-25", 4): data[-1]["kickoff_utc"] - timedelta(hours=2)}
        rows = gameweek_dataset(data, deadlines)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rows.parquet"
            write_parquet(path, rows)
            table = pq.read_table(path)
            recovered = table.to_pylist()
            self.assertEqual(recovered, rows)
            self.assertAlmostEqual(recovered[-1]["pre_gw_last3_matches_expected_assists_sum"], 0.3)
            self.assertEqual(table.schema.field("pre_gw_last3_matches_short_history").type, pa.bool_())
            self.assertEqual(table.schema.field("pre_gw_last3_matches_count").type, pa.int64())
            self.assertIsNone(recovered[0]["pre_gw_last3_matches_short_history"])


if __name__ == "__main__":
    unittest.main()
