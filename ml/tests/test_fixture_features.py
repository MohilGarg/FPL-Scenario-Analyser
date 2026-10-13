from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from ml.dataset import FEATURE_COLUMNS, timestamp
from ml.fixture_features import (PLAYER_TEAM_FEATURE_COLUMNS, TEAM_FEATURE_COLUMNS, club_proxy_audit,
    normalise_fixtures, player_club_features, retrospective_context, schema_document,
    team_deadline_features, team_index, validate_config, validate_match_links, xg_coverage_audit)
from ml.sources import ROOT


CONFIG = json.loads((ROOT / "fixture-config.json").read_text(encoding="utf-8"))
START = timestamp("2024-08-01T15:00:00Z")


def fixture(gw=1, *, identity=None, home=1, away=2, h=2, a=0, season="2024-25", kickoff=None):
    raw = {"id": str(identity or gw), "event": str(gw), "team_h": str(home), "team_a": str(away),
           "team_h_score": str(h), "team_a_score": str(a), "finished": "True",
           "kickoff_time": (kickoff or START + timedelta(days=7 * (gw - 1))).isoformat(),
           "team_h_difficulty": "99999"}
    return normalise_fixtures(season, [raw], roster(season))[0]


def roster(season="2024-25"):
    return team_index(season, [{"id": str(i), "code": str(i * 10), "name": f"Team {i}",
                               "strength_attack_home": "99999"} for i in (1, 2, 3)])


def team_row(fixtures, gw=3, team=1, cutoff=None, season="2024-25"):
    cutoff = cutoff or START + timedelta(days=7 * (gw - 1), hours=-2)
    rows = team_deadline_features(fixtures, {season: roster(season)}, {(season, gw): cutoff}, CONFIG)
    return next(r for r in rows if r["team_id"] == team and r["gameweek"] == gw)


def player_match(gw=1, team=1, season="2024-25"):
    return {"season": season, "player_key": f"{season}:1", "element": 1, "gameweek": gw,
            "fixture_id": gw, "team_id": team, "finished": True,
            "kickoff_utc": START + timedelta(days=7 * (gw - 1)), "starts": 1, "minutes": 90, "expected_goals": .25}


class FixtureFeatureTests(unittest.TestCase):
    def test_schema_preserves_frozen_baselines_and_excludes_retrospective_inputs(self):
        doc = schema_document()
        self.assertEqual(doc["frozen_player_v1"], list(FEATURE_COLUMNS))
        self.assertEqual(doc["upcoming_fixture_inputs"], [])
        self.assertEqual(doc["team_xg_inputs"], [])
        self.assertEqual(len(set(doc["approved_player_v2_inputs"])), len(FEATURE_COLUMNS) + len(PLAYER_TEAM_FEATURE_COLUMNS))
        for c in doc["approved_player_v2_inputs"]:
            self.assertTrue(c.startswith("pre_gw_"))
        validate_config(CONFIG)
        with self.assertRaises(ValueError):
            validate_config({**CONFIG, "season_carryover": True})

    def test_future_and_target_results_never_change_team_features_or_league_priors(self):
        data = [fixture(g) for g in range(1, 6)]
        before = team_row(data)
        changed = deepcopy(data)
        for f in changed:
            if f["gameweek"] >= 3:
                f["home_score"], f["away_score"] = 99, 66
        after = team_row(changed)
        self.assertEqual(before, after)
        self.assertEqual(before["pre_team_overall_season_match_count"], 2)

    def test_strict_buffer_and_same_gameweek_exclusion(self):
        cutoff = START + timedelta(days=14)
        data = [fixture(1, kickoff=cutoff - timedelta(hours=24)), fixture(2, kickoff=cutoff - timedelta(hours=24, seconds=1)),
                fixture(3, kickoff=cutoff - timedelta(days=3))]
        row = team_row(data, cutoff=cutoff)
        self.assertEqual(row["pre_team_overall_season_match_count"], 1)
        self.assertEqual(row["pre_team_league_history_matches"], 1)

    def test_rearranged_old_round_with_late_kickoff_does_not_enter(self):
        late = fixture(1, kickoff=START + timedelta(days=22))
        normal = fixture(2)
        cutoff = START + timedelta(days=14)
        before = team_row([late, normal], cutoff=cutoff)
        late["home_score"] = 999
        self.assertEqual(before, team_row([late, normal], cutoff=cutoff))
        self.assertEqual(before["pre_team_overall_season_match_count"], 1)

    def test_historical_merseyside_rearrangement_is_not_a_december_schedule_snapshot(self):
        # Pinned archive fixture 144, final GW24; official notices are in fixture-source-review.json.
        f = fixture(24, identity=144, kickoff=timestamp("2025-02-12T19:30:00Z"), h=2, a=2)
        december = team_row([f], gw=15, cutoff=timestamp("2024-12-07T11:00:00Z"))
        february = team_row([f], gw=25, cutoff=timestamp("2025-02-14T18:30:00Z"))
        self.assertEqual(december["pre_team_overall_season_match_count"], 0)
        self.assertEqual(february["pre_team_overall_season_match_count"], 1)
        self.assertEqual(schema_document()["upcoming_fixture_inputs"], [])

    def test_final_upcoming_assignment_changes_only_research_context(self):
        data = [fixture(1), fixture(3)]
        before = team_row(data)
        data[1]["away_id"] = 3
        data[1]["gameweek"] = 4
        data[1]["kickoff_utc"] += timedelta(days=4)
        self.assertEqual(before, team_row(data))
        self.assertNotIn("retro_opponent_id", TEAM_FEATURE_COLUMNS)

    def test_congestion_and_match_age_use_only_buffered_past_pl_results(self):
        cutoff = START + timedelta(days=14)
        data = [fixture(1, kickoff=cutoff - timedelta(days=13)), fixture(2, kickoff=cutoff - timedelta(days=5)),
                fixture(3, kickoff=cutoff + timedelta(days=2))]
        row = team_row(data, cutoff=cutoff)
        self.assertEqual(row["pre_team_matches_past7_days"], 1)
        self.assertEqual(row["pre_team_matches_past14_days"], 2)
        self.assertEqual(row["pre_team_last_match_age_days"], 5.)

    def test_prior_dgw_eligible_leg_only_and_target_double_kept_in_research(self):
        prior = fixture(1)
        late_leg = fixture(1, identity=11, kickoff=START + timedelta(days=16))
        target = [fixture(3, identity=30), fixture(3, identity=31, home=2, away=1, kickoff=START + timedelta(days=17))]
        cutoff = START + timedelta(days=14, hours=-2)
        data = [prior, late_leg, *target]
        rows = team_deadline_features(data, {"2024-25": roster()}, {("2024-25", 3): cutoff}, CONFIG)
        own = next(r for r in rows if r["team_id"] == 1 and r["gameweek"] == 3)
        self.assertEqual(own["pre_team_overall_season_match_count"], 1)
        research, gws = retrospective_context(data, rows, {("2024-25", 3): cutoff}, {"2024-25": roster()})
        legs = [r for r in research if r["team_id"] == 1 and r["gameweek"] == 3]
        self.assertEqual(len(legs), 2)
        self.assertEqual([r["retro_was_home"] for r in legs], [True, False])
        self.assertAlmostEqual(legs[1]["retro_pl_rest_days"], 3.)
        self.assertTrue(next(r for r in gws if r["team_id"] == 1 and r["gameweek"] == 3)["retro_double"])
        self.assertTrue(all(r["schedule_status"] == "research_only_final_assignment" for r in legs))

    def test_blank_does_not_zero_team_form_or_create_player_target(self):
        data = [fixture(1), fixture(3, home=2, away=3)]
        rows = team_deadline_features(data, {"2024-25": roster()}, {("2024-25", 3): START + timedelta(days=14, hours=-2)}, CONFIG)
        own = next(r for r in rows if r["team_id"] == 1 and r["gameweek"] == 3)
        self.assertEqual(own["pre_team_overall_season_goals_scored_mean"], 2.)
        _, gws = retrospective_context(data, rows, {("2024-25", 3): own["feature_cutoff_utc"]}, {"2024-25": roster()})
        self.assertTrue(next(r for r in gws if r["team_id"] == 1 and r["gameweek"] == 3)["retro_blank"])
        self.assertNotIn("pre_gw_fixture_count", schema_document()["approved_player_v2_inputs"])

    def test_promoted_and_opening_teams_use_explicit_neutral_prior_without_carryover(self):
        old = fixture(38, season="2023-24", h=99)
        row = team_row([old], gw=1)
        self.assertTrue(row["pre_team_no_history"])
        self.assertIsNone(row["pre_team_overall_season_goals_scored_mean"])
        self.assertEqual(row["pre_team_overall_season_goals_scored_smoothed"], 1.35)
        self.assertEqual(row["pre_team_overall_season_attack_ratio"], 1.)
        self.assertEqual(row["pre_team_overall_season_clean_sheet_frequency_smoothed"], .25)

    def test_short_history_smoothing_uses_only_prior_league_and_correct_venue(self):
        data = [fixture(1, h=3, a=1), fixture(2, home=2, away=1, h=1, a=2)]
        row = team_row(data)
        self.assertEqual(row["pre_team_home_season_match_count"], 1)
        self.assertEqual(row["pre_team_away_season_match_count"], 1)
        self.assertEqual(row["pre_team_home_season_goals_scored_smoothed"], (3 + 5 * 2) / 6)
        self.assertEqual(row["pre_team_away_season_goals_conceded_smoothed"], (1 + 5 * 2) / 6)
        self.assertEqual(row["pre_team_overall_last3_match_count"], 2)
        self.assertTrue(row["pre_team_overall_last3_short_history"])

    def test_rolling_windows_follow_actual_kickoff_and_skip_incomplete_results(self):
        data = [fixture(g, h=g, a=0) for g in range(1, 6)]
        data[0]["kickoff_utc"] = START + timedelta(days=25)
        row = team_row(data, gw=6)
        self.assertEqual(row["pre_team_overall_last3_goals_scored_sum"], 10)  # GW4+GW1+GW5
        incomplete = fixture(1)
        incomplete["result_complete"] = False
        empty = team_row([incomplete])
        self.assertEqual(empty["pre_team_overall_season_match_count"], 0)
        self.assertIsNone(empty["pre_team_overall_season_goals_conceded_sum"])

    def test_missing_source_fields_remain_unknown_and_ambiguous_blank_not_asserted(self):
        raw = {"id": "9", "team_h": "1", "team_a": "999", "team_h_score": "", "team_a_score": "0", "event": "", "kickoff_time": "", "finished": ""}
        data = normalise_fixtures("2024-25", [raw], roster())
        self.assertFalse(data[0]["result_complete"])
        self.assertIsNone(data[0]["kickoff_utc"])
        row = team_row(data)
        self.assertEqual(row["pre_team_overall_season_match_count"], 0)
        rows = team_deadline_features(data, {"2024-25": roster()}, {}, CONFIG)
        _, gws = retrospective_context(data, rows, {}, {"2024-25": roster()})
        self.assertTrue(all(r["retro_blank"] is None for r in gws))

    def test_missing_deadline_never_activates_even_smoothing_features(self):
        rows = team_deadline_features([fixture(1)], {"2024-25": roster()}, {}, CONFIG)
        self.assertTrue(all(r[c] is None for r in rows for c in TEAM_FEATURE_COLUMNS))

    def test_team_ids_are_season_scoped_even_when_codes_change(self):
        current = roster()
        other = team_index("2023-24", [{"id": "1", "code": "555", "name": "Different club"},
                                      {"id": "2", "code": "666", "name": "Other club"}])
        rosters = {"2024-25": current, "2023-24": other}
        old = fixture(1, season="2023-24", h=10)
        rows = team_deadline_features([old], rosters, {("2024-25", 3): START + timedelta(days=14)}, CONFIG)
        row = next(r for r in rows if r["season"] == "2024-25" and r["team_id"] == 1 and r["gameweek"] == 3)
        self.assertEqual(row["team_code"], 10)
        self.assertEqual(row["pre_team_overall_season_match_count"], 0)

    def test_player_club_proxy_never_uses_target_or_final_team(self):
        deadlines = {("2024-25", 3): START + timedelta(days=14, hours=-2)}
        teams = team_deadline_features([fixture(1), fixture(2)], {"2024-25": roster()}, deadlines, CONFIG)
        player = {"season": "2024-25", "player_key": "2024-25:1", "gameweek": 3, "team_id": 3, "final_team": 3}
        history = [player_match(1), player_match(3, team=3)]
        before = list(player_club_features([player], history, teams, deadlines))[0]
        self.assertEqual(before["last_observed_team_id"], 1)
        history[1]["team_id"] = 2
        player["final_team"] = 2
        self.assertEqual(before, list(player_club_features([player], history, teams, deadlines))[0])
        new = list(player_club_features([player], [], teams, deadlines))[0]
        self.assertTrue(new["pre_gw_last_observed_club_missing"])
        self.assertTrue(all(new[c] is None for c in PLAYER_TEAM_FEATURE_COLUMNS if not c.endswith("_missing")))

    def test_conflicting_duplicate_ids_and_invalid_scores_fail(self):
        raw = {"id": "1", "team_h": "1", "team_a": "2", "event": "1"}
        with self.assertRaises(ValueError):
            normalise_fixtures("2024-25", [raw, raw], roster())
        with self.assertRaises(ValueError):
            normalise_fixtures("2024-25", [{**raw, "team_h_score": "-1"}], roster())
        with self.assertRaises(ValueError):
            team_index("2024-25", [{"id": "1", "code": "10", "name": "A"}, {"id": "2", "code": "10", "name": "B"}])

    def test_prepared_match_cannot_use_a_contradictory_earlier_kickoff(self):
        f, m = fixture(1), player_match(1)
        validate_match_links([f], [m])
        m["kickoff_utc"] -= timedelta(days=7)
        with self.assertRaisesRegex(ValueError, "contradicts"):
            validate_match_links([f], [m])
        with self.assertRaisesRegex(ValueError, "missing fixture"):
            validate_match_links([], [m])

    def test_club_proxy_disagreement_is_quality_metadata_not_a_predictor(self):
        ids = [{"season": "2024-25", "player_key": "2024-25:1", "gameweek": 3, "last_observed_team_id": 1}]
        audit = club_proxy_audit(ids, [player_match(3, team=2)])[0]
        self.assertFalse(audit["quality_proxy_matches_target_club"])
        self.assertNotIn("quality_proxy_matches_target_club", schema_document()["approved_player_v2_inputs"])

    def test_team_xg_player_sums_are_audit_only_even_with_known_starters(self):
        rows = [player_match(1) for _ in range(11)]
        for i, r in enumerate(rows):
            r["element"] = i
        audit = xg_coverage_audit([fixture(1)], rows)[0]
        self.assertEqual(audit["quality_starts_sum"], 11)
        self.assertEqual(audit["research_player_xg_sum"], 2.75)
        self.assertFalse(audit["quality_substitute_coverage_verified"])
        rows[0]["expected_goals"] = None
        self.assertIsNone(xg_coverage_audit([fixture(1)], rows)[0]["research_player_xg_sum"])


@unittest.skipUnless(importlib.util.find_spec("pyarrow"), "Install ML Parquet dependency")
class FixtureParquetTests(unittest.TestCase):
    def test_sidecar_types_missing_club_and_explicit_predictive_join(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from ml.prepare_fixtures import approved_player_inputs, write_player_sidecar
        deadline = START + timedelta(days=14)
        keys = {"season": "2024-25", "player_key": "2024-25:1", "gameweek": 3, "feature_cutoff_utc": deadline}
        teams = team_deadline_features([fixture(1)], {"2024-25": roster()}, {("2024-25", 3): deadline}, CONFIG)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sidecar.parquet"
            write_player_sidecar(path, player_club_features([keys], [], teams, {("2024-25", 3): deadline}))
            sidecar = pq.read_table(path)
        self.assertEqual(sidecar.schema.field("pre_gw_last_observed_club_overall_last5_match_count").type, pa.int64())
        self.assertEqual(sidecar.schema.field("pre_gw_last_observed_club_missing").type, pa.bool_())
        base = pa.Table.from_pylist([{**keys, **dict.fromkeys(FEATURE_COLUMNS, 0), "target_points": 999, "xP": 999}])
        # Match the existing fixed-offset cutoff representation.
        base = base.set_column(base.schema.get_field_index("feature_cutoff_utc"), "feature_cutoff_utc",
                               base["feature_cutoff_utc"].cast(pa.timestamp("us", tz="+00:00")))
        joined = approved_player_inputs(base, sidecar)
        self.assertNotIn("target_points", joined.column_names)
        self.assertNotIn("last_observed_team_id", joined.column_names)
        self.assertNotIn("xP", joined.column_names)
        self.assertEqual(joined.num_columns, 4 + len(FEATURE_COLUMNS) + len(PLAYER_TEAM_FEATURE_COLUMNS))
        bad = sidecar.set_column(sidecar.schema.get_field_index("gameweek"), "gameweek", pa.array([4], type=pa.int64()))
        with self.assertRaises(ValueError):
            approved_player_inputs(base, bad)


if __name__ == "__main__":
    unittest.main()
