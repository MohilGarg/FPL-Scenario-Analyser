from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from urllib.error import HTTPError

from fpl_forfeit.assistant.external_forecasts import (
    SOURCE_URLS,
    ForecastHTTPClient,
    ForecastRow,
    combine_rows,
    fit_past_weights,
    gameweek_rows,
    load_available_forecasts,
    map_player,
    parse_fpl_cat,
    parse_manual_csv,
    parse_official_fpl,
    parse_sideline_quant,
    prune_external_cache,
    read_batch,
    save_batch,
    validate_batch,
)

NOW = datetime(2026, 11, 5, 10, tzinfo=UTC)
DEADLINE = datetime(2026, 11, 6, 18, 30, tzinfo=UTC)


def official() -> dict:
    return {
        "events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z"}, {"id": 10, "deadline_time": DEADLINE.isoformat(), "is_next": True}],
        "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}, {"id": 2, "name": "Chelsea", "short_name": "CHE"}],
        "elements": [
            {"id": 1, "team": 1, "first_name": "Alex", "second_name": "Smith", "ep_next": "5.2"},
            {"id": 2, "team": 2, "first_name": "Alex", "second_name": "Smith", "ep_next": None},
        ],
    }


def cat() -> dict:
    return {"gameweek": 10, "deadline": DEADLINE.isoformat(), "computed_at": (NOW - timedelta(hours=1)).isoformat(), "players": [
        {"id": 1, "team": "ARS", "fixtures": 1, "xp": 5.5, "expected_minutes": 80, "play_chance": 0.9, "p_haul": 0.2, "p_blank": 0.4},
    ]}


def sideline() -> dict:
    return {"schema_version": "1.0", "gameweek": 10, "deadline": DEADLINE.isoformat(), "generated_at": (NOW - timedelta(hours=1)).isoformat(), "rows": [
        {"player_id": 1, "fixture_id": 101, "gameweek": 10, "xP": 4.8, "e_mins": 72, "p_start": 0.8, "p_start60": 0.7, "p_cameo": 0.1, "p_unused": 0.1},
    ]}


def fixtures() -> list[dict]:
    return [{"id": 101, "event": 10, "team_h": 1, "team_a": 2}, {"id": 102, "event": 10, "team_h": 2, "team_a": 1}]


class ExternalForecastTests(unittest.TestCase):
    def test_cat_does_not_double_multiply_unconditional_points(self):
        row = parse_fpl_cat(cat(), official(), NOW).rows[0]
        self.assertEqual(row.expected_points, 5.5)
        self.assertEqual(row.appearance_probability, 0.9)

    def test_cat_dgw_summed_appearance_never_becomes_probability(self):
        payload = cat()
        payload["players"][0].update(fixtures=2, play_chance=1.8, expected_minutes=160, xp=11)
        row = parse_fpl_cat(payload, official(), NOW).rows[0]
        self.assertEqual(row.expected_points, 11)
        self.assertIsNone(row.appearance_probability)
        self.assertIsNone(row.no_show_probability)
        self.assertIsNone(row.haul_probability)
        self.assertIsNone(row.blank_probability)

    def test_cat_mapping_and_invalid_schema_fail_visibly(self):
        for update in ({"id": 999}, {"team": "CHE"}, {"xp": float("nan")}, {"play_chance": 1.1}, {"expected_minutes": 100}):
            with self.subTest(update=update), self.assertRaises((ValueError, KeyError)):
                payload = cat()
                payload["players"][0].update(update)
                parse_fpl_cat(payload, official(), NOW)

    def test_deadline_and_late_source_rejected(self):
        for key, value in (("computed_at", (NOW + timedelta(seconds=1)).isoformat()), ("deadline", (DEADLINE + timedelta(hours=1)).isoformat()), ("gameweek", 9)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                payload = cat()
                payload[key] = value
                parse_fpl_cat(payload, official(), NOW)
        with self.assertRaises(ValueError):
            parse_fpl_cat(cat(), official(), DEADLINE)

    def test_sideline_preserves_fixture_grain_and_start60_definition(self):
        row = parse_sideline_quant(sideline(), official(), NOW, fixtures()).rows[0]
        self.assertEqual(row.fixture_id, 101)
        self.assertEqual(row.expected_points, 4.8)
        self.assertAlmostEqual(row.appearance_probability, 0.9)
        self.assertEqual(row.start_probability, 0.8)
        self.assertIsNone(row.sixty_probability)
        self.assertIn("P(start and 60+ minutes)", row.notes[0])

    def test_partial_double_excluded_and_complete_double_sums_means_only(self):
        batch = parse_sideline_quant(sideline(), official(), NOW, fixtures())
        self.assertEqual(gameweek_rows(batch, 10, fixtures(), official()), {})
        payload = sideline()
        payload["rows"].append({**payload["rows"][0], "fixture_id": 102, "xP": 3.2})
        row = gameweek_rows(parse_sideline_quant(payload, official(), NOW, fixtures()), 10, fixtures(), official())[1]
        self.assertEqual(row.expected_points, 8)
        self.assertEqual(row.expected_minutes, 144)
        self.assertIsNone(row.appearance_probability)
        self.assertIsNone(row.sixty_probability)

    def test_wrong_fixture_and_inconsistent_minutes_states_rejected(self):
        for update in ({"fixture_id": 999}, {"gameweek": 9}, {"p_start": 0.99}, {"p_start60": 0.9}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                payload = sideline()
                payload["rows"][0].update(update)
                parse_sideline_quant(payload, official(), NOW, fixtures())

    def test_unknown_version_and_duplicate_rows_rejected(self):
        payload = sideline()
        payload["schema_version"] = "2.0"
        with self.assertRaises(ValueError):
            parse_sideline_quant(payload, official(), NOW)
        payload = sideline()
        payload["rows"] *= 2
        with self.assertRaises(ValueError):
            parse_sideline_quant(payload, official(), NOW)

    def test_official_missing_is_missing_and_no_computation_time_invented(self):
        batch = parse_official_fpl(official(), NOW)
        self.assertEqual(len(batch.rows), 1)
        self.assertEqual(batch.rows[0].expected_points, 5.2)
        self.assertIsNone(batch.computed_at)
        self.assertFalse(batch.redistribution_allowed)

    def test_snapshot_immutable_roundtrip_and_no_backdating(self):
        batch = parse_fpl_cat(cat(), official(), NOW)
        self.assertFalse(batch.available_at(NOW - timedelta(seconds=1)))
        self.assertTrue(batch.available_at(NOW))
        self.assertFalse(batch.available_at(DEADLINE))
        self.assertFalse(batch.available_at(NOW + timedelta(hours=10), max_age_hours=2))
        with tempfile.TemporaryDirectory() as temporary:
            path = save_batch(batch, Path(temporary))
            original = path.read_bytes()
            self.assertEqual(save_batch(batch, Path(temporary)), path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(read_batch(path), batch)
            validate_batch(read_batch(path), official())

    def test_stale_missing_corrupt_captures_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_batch(parse_fpl_cat(cat(), official(), NOW), root)
            (root / "bad.json.gz").write_bytes(b"corrupt")
            (root / "index-2026-27-GW10-bad.json").write_text(json.dumps({"filename": "bad.json.gz", "sha256": "0" * 64, "retrieved_at": NOW.isoformat()}))
            loaded = load_available_forecasts(root, "2026-27", 10, NOW, bootstrap=official())
            self.assertIn("fpl_cat", loaded["sources"])
            self.assertTrue(loaded["errors"])
            stale = load_available_forecasts(root, "2026-27", 10, NOW + timedelta(hours=10), max_age_hours=1)
            self.assertEqual(stale["sources"], {})
            self.assertIn("internal model retained", stale["fallback"])
            self.assertFalse(load_available_forecasts(root, "2025-26", 10, NOW)["sources"])

    def test_manual_mapping_requires_season_and_full_name_plus_club(self):
        self.assertEqual(map_player({"name": "Alex Smith", "team": "ARS"}, official(), "2026-27"), (1, "exact_full_name_and_club"))
        with self.assertRaises(ValueError):
            map_player({"name": "Smith", "team": "ARS"}, official(), "2026-27")
        with self.assertRaises(ValueError):
            map_player({"name": "Alex Smith"}, official(), "2026-27")
        with self.assertRaises(ValueError):
            map_player({"player_id": 1}, official(), "2025-26")
        self.assertEqual(map_player({"name": "Alias", "team": "ARS"}, official(), "2026-27", {"2026-27:ARS:Alias": 1})[0], 1)

    def test_manual_conditional_fixture_points_normalised_once_and_private(self):
        metadata = {"source": "fpl_form", "season": "2026-27", "deadline": DEADLINE.isoformat(), "computed_at": None, "points_semantics": "conditional_appearance", "granularity": "fixture"}
        body = "player_id,gameweek,fixture_id,expected_points,appearance_probability\n1,10,101,8,0.5\n"
        batch = parse_manual_csv(body, metadata, official(), NOW, fixtures())
        self.assertEqual(batch.rows[0].expected_points, 4)
        self.assertEqual(batch.rows[0].points_semantics, "unconditional")
        self.assertFalse(batch.redistribution_allowed)
        metadata["points_semantics"] = "unconditional"
        self.assertEqual(parse_manual_csv(body, metadata, official(), NOW, fixtures()).rows[0].expected_points, 8)
        metadata.update(granularity="gameweek", points_semantics="conditional_appearance")
        with self.assertRaises(ValueError):
            parse_manual_csv(body, metadata, official(), NOW)

    def test_mean_median_and_common_coverage_never_zero_fill(self):
        sources = {"a": {1: ForecastRow(1, 10, 6), 2: ForecastRow(2, 10, 8)}, "b": {1: ForecastRow(1, 10, 2)}}
        combined = combine_rows(sources)
        self.assertEqual(combined[1]["expected_points"], 4)
        self.assertEqual(combined[2]["expected_points"], 8)
        self.assertEqual(combined[2]["coverage"], 0.5)
        self.assertFalse(combined[1]["distribution_available"])
        self.assertEqual(set(combine_rows(sources, require_all=True)), {1})
        self.assertEqual(combine_rows(sources, method="median")[1]["expected_points"], 4)
        self.assertEqual(combine_rows(sources, method="weighted", weights={"a": 0.75, "b": 0.25})[1]["expected_points"], 5)
        with self.assertRaises(ValueError):
            combine_rows(sources, method="weighted", weights={"a": -1, "b": 2})

    def test_learned_weights_are_past_only_and_shrink_to_equal(self):
        self.assertEqual(fit_past_weights([], ("a", "b"), NOW), {"a": 0.5, "b": 0.5})
        row = {"deadline": "2026-10-01T00:00:00Z", "captured_at": "2026-09-30T23:00:00Z", "outcome_available_at": "2026-10-04T00:00:00Z", "actual": 4, "predictions": {"a": 4, "b": 6}}
        weights = fit_past_weights([row], ("a", "b"), NOW)
        self.assertEqual(weights,{"a":.5,"b":.5})
        self.assertEqual(fit_past_weights([row]*670,("a","b"),NOW),weights)
        past=[]
        for index in range(8):
            deadline=NOW-timedelta(days=10+7*index)
            past.append({**row,"deadline":deadline.isoformat(),"captured_at":(deadline-timedelta(hours=1)).isoformat(),"outcome_available_at":(deadline+timedelta(days=3)).isoformat()})
        weights=fit_past_weights(past,("a","b"),NOW)
        self.assertGreater(weights["a"], weights["b"])
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertLess(weights["a"], 0.7)
        row["outcome_available_at"] = (NOW + timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):
            fit_past_weights([row], ("a", "b"), NOW)

    def test_http_cache_prevents_polling_and_retries_only_transient_errors(self):
        calls, sleeps = [], []
        class Response:
            headers: ClassVar[dict[str, str]] = {"ETag": '"v1"'}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(cat()).encode()
        def opener(request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise HTTPError(request.full_url, 503, "Unavailable", {}, None)
            return Response()
        with tempfile.TemporaryDirectory() as temporary:
            client = ForecastHTTPClient(Path(temporary), opener=opener, sleeper=sleeps.append, now=lambda: NOW)
            value, retrieved = client.get_json(SOURCE_URLS["fpl_cat"])
            self.assertEqual(value, cat())
            self.assertEqual(retrieved, NOW)
            self.assertEqual(sleeps, [1])
            client.get_json(SOURCE_URLS["fpl_cat"])
            self.assertEqual(len(calls), 2)
            with self.assertRaises(ValueError):
                client.get_json("https://untrusted.example/feed")

    def test_indexed_lookup_skips_unrelated_gameweeks_without_inflation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_batch(parse_fpl_cat(cat(), official(), NOW), root)
            with patch("fpl_forfeit.assistant.external_forecasts.read_batch", wraps=read_batch) as read:
                loaded = load_available_forecasts(root, "2026-27", 11, NOW)
                self.assertEqual(loaded["sources"], {})
                read.assert_not_called()

    def test_indexed_historical_cutoff_uses_only_available_older_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = parse_fpl_cat(cat(), official(), NOW)
            save_batch(batch, root)
            newer = replace(batch, retrieved_at=(NOW + timedelta(minutes=10)).isoformat(), rows=(replace(batch.rows[0], expected_points=9),))
            save_batch(newer, root)
            loaded = load_available_forecasts(root, "2026-27", 10, NOW)
            self.assertEqual(loaded["sources"]["fpl_cat"][1].expected_points, 5.5)

    def test_retention_bounds_automatic_copies_and_never_prunes_manuals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = parse_fpl_cat(cat(), official(), NOW)
            auto = []
            for day in range(10):
                older = replace(batch, retrieved_at=(NOW - timedelta(days=day)).isoformat(), computed_at=(NOW - timedelta(days=day, hours=1)).isoformat())
                auto.append(save_batch(older, root))
            manual = replace(batch, source="fpl_form", source_version="personal-export", redistribution_allowed=False, retrieved_at=(NOW - timedelta(days=30)).isoformat())
            manual_path = save_batch(manual, root)
            report = prune_external_cache(root, now=NOW, max_files=6)
            self.assertEqual(report["remaining_automatic_files"], 6)
            self.assertTrue(manual_path.exists())
            self.assertTrue(auto[0].exists())
            self.assertTrue(auto[1].exists())
            self.assertFalse(auto[-1].exists())
            self.assertFalse(report["personal_imports_pruned"])
            loaded = load_available_forecasts(root, "2026-27", 10, NOW)
            self.assertIn("fpl_cat", loaded["sources"])

    def test_storage_and_index_names_reject_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = parse_fpl_cat(cat(), official(), NOW)
            with self.assertRaises(ValueError):
                save_batch(replace(batch, source="../escape"), root)
            (root / "index-2026-27-GW10-bad.json").write_text(json.dumps({"filename": "../escape.json.gz", "sha256": "0" * 64, "retrieved_at": NOW.isoformat()}))
            self.assertTrue(load_available_forecasts(root, "2026-27", 10, NOW)["errors"])

    def test_etag_304_preserves_earliest_retrieval_evidence(self):
        now = [NOW]
        class Response:
            headers: ClassVar[dict[str, str]] = {"ETag": '"v1"'}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(cat()).encode()
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if len(calls) > 1:
                self.assertEqual(request.get_header("If-none-match"), '"v1"')
                raise HTTPError(request.full_url, 304, "Unchanged", {}, None)
            return Response()
        with tempfile.TemporaryDirectory() as temporary:
            client = ForecastHTTPClient(Path(temporary), opener=opener, now=lambda: now[0])
            client.get_json(SOURCE_URLS["fpl_cat"])
            now[0] += timedelta(minutes=10)
            _, retrieved = client.get_json(SOURCE_URLS["fpl_cat"])
            self.assertEqual(retrieved, NOW)


if __name__ == "__main__":
    unittest.main()
