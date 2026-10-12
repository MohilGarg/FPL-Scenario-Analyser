from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from ml.dataset import (
    FEATURE_COLUMNS, CsvData, feature_values, gameweek_dataset, load_deadlines,
    normalise_matches, number, read_csv, timestamp,
)
from ml.prepare import write_parquet
from ml.sources import digest, verify_cache


def synthetic_tables(rows: list[dict[str, str]] | None = None) -> dict[str, CsvData]:
    raw = {"element": "1", "GW": "1", "round": "1", "fixture": "101", "name": "Player",
           "team": "Home", "position": "MID", "was_home": "True", "opponent_team": "2",
           "total_points": "5", "minutes": "90", "starts": "1", "expected_goals": "0.25",
           "expected_assists": "", "kickoff_time": "2024-08-01T15:00:00Z", "xP": "999"}
    rows = rows if rows is not None else [raw]
    players = [{"id": "1", "code": "900", "element_type": "3", "team": "2",
                "first_name": "A", "second_name": "Player", "total_points": "99999"},
               {"id": "2", "code": "901", "element_type": "5", "team": "1"}]
    fixtures = [{"id": str(100 + g), "event": str(g), "team_h": "1", "team_a": "2",
                 "finished": "True"} for g in (1, 2, 9, 10)]
    teams = [{"id": "1", "name": "Home", "code": "10"},
             {"id": "2", "name": "Away", "code": "20"}]
    def table(r):
        return CsvData(list(r[0]), r)
    return {"history": table(rows), "players": table(players),
            "fixtures": table(fixtures), "teams": table(teams)}


def matches(rows=None, season="2024-25"):
    return normalise_matches(season, **synthetic_tables(rows))[0]


def history_rows() -> list[dict[str, str]]:
    base = synthetic_tables()["history"].rows[0]
    rows = []
    for gw in (10, 2, 1, 9):
        rows.append({**base, "GW": str(gw), "round": str(gw), "fixture": str(100 + gw),
                     "kickoff_time": f"2024-08-{gw:02d}T15:00:00Z", "total_points": str(gw)})
    return rows


class IngestionTests(unittest.TestCase):
    def test_csv_preserves_blank_zero_unicode_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.csv"
            path.write_text('name,minutes,new_field\n"José, Smith",0,\n', encoding="utf-8")
            data = read_csv(path)
        self.assertEqual(data.columns, ["name", "minutes", "new_field"])
        self.assertEqual(data.rows[0], {"name": "José, Smith", "minutes": "0", "new_field": ""})

    def test_missing_is_not_zero_and_bad_numeric_data_fails(self):
        for value in ("", "None", "NaN", "NULL", None):
            self.assertIsNone(number(value))
        self.assertEqual(number("0", integer=True), 0)
        for value in ("inf", "1.5", "bad"):
            with self.assertRaises(ValueError):
                number(value, integer=True)

    def test_identical_duplicates_removed_but_conflicts_fail(self):
        raw = synthetic_tables()["history"].rows[0]
        cleaned, diagnostics = normalise_matches("2024-25", **synthetic_tables([raw, dict(raw)]))
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(diagnostics["exact_duplicate_rows"], 1)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            matches([raw, {**raw, "total_points": "6"}])

    def test_assistant_manager_excluded_and_fixture_mapping_beats_final_team(self):
        raw = synthetic_tables()["history"].rows[0]
        cleaned, diagnostics = normalise_matches(
            "2024-25", **synthetic_tables([raw, {**raw, "element": "2", "position": "AM"}])
        )
        self.assertEqual(diagnostics["assistant_manager_rows"], 1)
        self.assertEqual(cleaned[0]["team_id"], 1)  # final player metadata says team 2
        self.assertEqual(cleaned[0]["player_code"], 900)

    def test_missing_gameweek_never_backfilled(self):
        data = gameweek_dataset(matches(history_rows()))
        self.assertEqual([r["gameweek"] for r in data], [1, 2, 9, 10])
        self.assertEqual(len(data), 4)

    def test_round_mismatch_and_bad_opponent_fail(self):
        raw = synthetic_tables()["history"].rows[0]
        for changed in ({"round": "2"}, {"opponent_team": "3"}, {"GW": "0", "round": "0"}):
            with self.assertRaises(ValueError):
                matches([{**raw, **changed}])

    def test_double_gameweek_sum_and_missing_target(self):
        first = matches()[0]
        first["team_gw_fixture_ids"] = [101, 102]
        second = {**first, "fixture_id": 102, "total_points": 7, "minutes": 60,
                  "kickoff_utc": timestamp("2024-08-04T15:00:00Z")}
        row = gameweek_dataset([second, first])[0]
        self.assertEqual(row["fixture_ids"], [101, 102])
        self.assertEqual(row["target_points"], 12)
        self.assertEqual(row["observed_minutes"], 150)
        self.assertIsNone(row["observed_expected_assists"])
        second["total_points"] = None
        self.assertIsNone(gameweek_dataset([first, second])[0]["target_points"])
        self.assertIsNone(gameweek_dataset([first])[0]["target_points"])

    def test_unfinished_fixture_has_no_final_label(self):
        tables = synthetic_tables()
        tables["fixtures"].rows[0]["finished"] = "False"
        data, _ = normalise_matches("2024-25", **tables)
        self.assertIsNone(gameweek_dataset(data)[0]["target_points"])

    def test_corrupted_cache_fails_before_processing(self):
        lock = {"revision": "a" * 40, "repository": "test/repo", "seasons": ["2024-25"],
                "files": ["data.csv"]}
        with tempfile.TemporaryDirectory() as folder, patch("ml.sources.source_lock", return_value=lock):
            root = Path(folder)
            (root / "2024-25").mkdir()
            (root / "2024-25/data.csv").write_bytes(b"corrupt")
            manifest = {**lock, "files": {"2024-25/data.csv": {"sha256": digest(b"expected"),
                                                               "bytes": 8}}}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_cache(root, ["2024-25"])


class LeakageTests(unittest.TestCase):
    def test_without_deadline_all_features_null(self):
        row = gameweek_dataset(matches())[0]
        self.assertEqual(row["feature_status"], "no_deadline")
        self.assertTrue(all(v is None for v in feature_values(row).values()))

    def test_numeric_order_and_strict_prior_gameweeks(self):
        cutoff = timestamp("2024-08-10T10:00:00Z")
        # GW9 is too close to the cutoff for the 24h readiness gate; GW1+2 enter.
        rows = gameweek_dataset(matches(history_rows()), {("2024-25", 10): cutoff})
        self.assertEqual([r["gameweek"] for r in rows], [1, 2, 9, 10])
        row = rows[-1]
        self.assertEqual(row["pre_gw_total_points_sum"], 3)
        self.assertEqual(row["pre_gw_history_fixtures"], 2)
        self.assertEqual(row["pre_gw_points_mean_last3_observed_gws"], 1.5)
        self.assertIsNone(row["pre_gw_expected_assists_sum"])

    def test_current_and_future_outcomes_cannot_change_features(self):
        original = matches(history_rows())
        deadlines = {("2024-25", 2): timestamp("2024-08-02T10:00:00Z")}
        # Move the earlier kickoff far enough back to pass the readiness gate.
        original[0]["kickoff_utc"] = timestamp("2024-07-28T15:00:00Z")
        before = feature_values(gameweek_dataset(original, deadlines)[1])
        mutated = deepcopy(original)
        for match in mutated:
            if match["gameweek"] >= 2:
                match["total_points"] = 999
                match["minutes"] = 999
                match["expected_goals"] = 999
        after = feature_values(gameweek_dataset(mutated, deadlines)[1])
        self.assertEqual(before, after)
        self.assertEqual(before["pre_gw_total_points_sum"], 1)

    def test_second_double_gameweek_leg_never_enters_same_gw_features(self):
        data = matches(history_rows())
        first = next(m for m in data if m["gameweek"] == 2)
        first["team_gw_fixture_ids"] = [102, 202]
        data.append({**first, "fixture_id": 202, "kickoff_utc": timestamp("2024-08-06T15:00:00Z"),
                     "total_points": 1000})
        cutoff = timestamp("2024-08-02T10:00:00Z")
        rows = gameweek_dataset(data, {("2024-25", 2): cutoff})
        row = next(r for r in rows if r["gameweek"] == 2)
        self.assertEqual(row["target_points"], 1002)
        self.assertIsNone(row["pre_gw_total_points_sum"])

    def test_season_reset_and_missing_kickoff(self):
        current = matches(history_rows())
        current[0]["kickoff_utc"] = None
        previous = matches(season="2023-24")
        previous[0]["total_points"] = 5000
        cutoff = timestamp("2024-08-09T10:00:00Z")
        row = next(r for r in gameweek_dataset(current + previous, {("2024-25", 9): cutoff})
                   if r["season"] == "2024-25" and r["gameweek"] == 9)
        self.assertEqual(row["pre_gw_total_points_sum"], 2)

    def test_end_season_fields_and_xp_never_enter_whitelist(self):
        allowed = set(FEATURE_COLUMNS)
        self.assertFalse(allowed & {"xP", "form", "now_cost", "total_points", "team_a_score",
                                   "status", "fixture_records", "observed_points", "player_code"})
        raw = history_rows()
        deadlines = {("2024-25", 9): timestamp("2024-08-09T10:00:00Z")}
        before = feature_values(gameweek_dataset(matches(raw), deadlines)[2])
        self.assertEqual(before["pre_gw_total_points_sum"], 3)
        tables = synthetic_tables([{**r, "xP": "-9999"} for r in raw])
        tables["players"].rows[0]["total_points"] = "-9999"
        normalised, _ = normalise_matches("2024-25", **tables)
        self.assertEqual(before, feature_values(gameweek_dataset(normalised, deadlines)[2]))

    def test_deadline_requires_offset_provenance_and_order(self):
        with self.assertRaises(ValueError):
            timestamp("2024-08-01T12:00:00")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "deadlines.csv"
            path.write_text("season,gameweek,deadline_utc,source_url\n"
                            "2024-25,1,2024-08-10T10:00:00Z,https://example.test/a\n"
                            "2024-25,2,2024-08-01T10:00:00Z,https://example.test/b\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "increase"):
                load_deadlines(path)
        with self.assertRaisesRegex(ValueError, "after"):
            gameweek_dataset(matches(), {("2024-25", 1): timestamp("2024-08-01T16:00:00Z")})


@unittest.skipUnless(importlib.util.find_spec("pyarrow"), "Install ml/requirements.txt for Parquet check")
class ParquetTests(unittest.TestCase):
    def test_source_tokens_and_null_features_round_trip(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = synthetic_tables()["history"]
            path = root / "source.parquet"
            write_parquet(path, source.rows, columns=source.columns)
            self.assertEqual(pq.read_table(path).to_pylist(), source.rows)
            path = root / "gameweeks.parquet"
            rows = gameweek_dataset(matches())
            write_parquet(path, rows)
            recovered = pq.read_table(path).to_pylist()
            self.assertEqual(rows, recovered)
            self.assertTrue(all(recovered[0][c] is None for c in FEATURE_COLUMNS))
            write_parquet(path, matches())
            self.assertEqual(pq.read_table(path).to_pylist()[0]["kickoff_utc"],
                             timestamp("2024-08-01T15:00:00Z"))


if __name__ == "__main__":
    unittest.main()
