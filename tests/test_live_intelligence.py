from __future__ import annotations

import copy
import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from fpl_forfeit.api import FPLClient, parse_appearance_history, state_from_snapshot
from fpl_forfeit.evaluation import prediction_record
from fpl_forfeit.forecast import Condition, FixtureModel, football_inputs, outlook
from fpl_forfeit.forecasting import project_players
from fpl_forfeit.forecasting.models import BENCHMARK_MODEL_VERSION
from fpl_forfeit.forecasting.service import ForecastEnricher
from fpl_forfeit.forecasting.statistics import benchmark_player_rates, player_rates
from fpl_forfeit.history import RiskHistoryStore
from fpl_forfeit.intelligence import fixture_groups
from fpl_forfeit.lineups import APIFootballLineups, configured_lineups, normalise_lineups
from fpl_forfeit.models import ElementScore, MatchAppearance
from fpl_forfeit.scoring import final_manager_scores, last_ids
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.web import create_app
from tests.test_forecast import fixture_state
from tests.validation_states import validation_state


class OfficialStatisticalInputTests(unittest.TestCase):
    def test_normalisation_preserves_zero_and_rejects_invalid_expected_stats(self):
        snapshot = {
            "league_id": 1,
            "gameweek": 1,
            "standings": [],
            "picks": {},
            "fixtures": [],
            "live": {"elements": []},
            "bootstrap": {
                "teams": [{"id": 1, "name": "Home"}],
                "elements": [
                    {
                        "id": 1,
                        "web_name": "Player",
                        "team": 1,
                        "element_type": 3,
                        "expected_goals": "0.00",
                        "expected_assists": "nan",
                        "expected_goal_involvements": "2.40",
                    }
                ],
            },
        }
        p = state_from_snapshot(snapshot).players[1]
        self.assertEqual(p.expected_goals, 0)
        self.assertIsNone(p.expected_assists)
        self.assertEqual(p.expected_goal_involvements, 2.4)

    def test_completed_histories_do_not_include_future_or_unfinished_matches(self):
        history = parse_appearance_history(
            {
                "history": [
                    {"fixture": 1, "round": 1, "minutes": 90, "starts": 1},
                    {"fixture": 2, "round": 2, "minutes": 19, "starts": 0},
                    {"fixture": 3, "round": 3, "minutes": 80, "starts": 1},
                ]
            },
            {1, 2},
        )
        self.assertEqual([(h.minutes, h.started) for h in history], [(90, True), (19, False)])

    def test_shrinkage_blends_correlated_counts_once_and_keeps_v3(self):
        p = replace(
            fixture_state().players[12],
            expected_goals=7.0,
            expected_assists=4.0,
            expected_goal_involvements=999,
        )
        rate = player_rates(p)
        self.assertAlmostEqual(rate.goal, (0.65 * 7 + 0.35 * 3 + 5 * 0.35) / (540 / 90 + 5))
        self.assertAlmostEqual(rate.assist, (0.65 * 4 + 0.35 * 1 + 5 * 0.1) / (540 / 90 + 5))
        self.assertEqual(
            player_rates(p, model_version=BENCHMARK_MODEL_VERSION), benchmark_player_rates(p)
        )
        self.assertEqual(rate, player_rates(replace(p, expected_goal_involvements=0)))

    def test_missing_expected_statistics_keep_observed_rates_and_sparse_priors(self):
        p = fixture_state().players[12]
        self.assertEqual(player_rates(p).goal, benchmark_player_rates(p).goal)
        sparse = replace(
            p, season_minutes=0, season_goals=None, season_assists=None, expected_goals=3
        )
        self.assertEqual(player_rates(sparse).goal, 0.35)
        self.assertTrue(0 < player_rates(sparse).start < 1)

    def test_regular_cameos_and_full_match_starters_have_different_distributions(self):
        p = fixture_state().players[12]
        sub = replace(
            p, appearance_history=tuple(MatchAppearance(i, i, 18, False) for i in range(8))
        )
        full = replace(
            p, appearance_history=tuple(MatchAppearance(i, i, 90, True) for i in range(8))
        )
        self.assertLess(player_rates(sub).start, player_rates(full).start)
        self.assertGreater(
            player_rates(sub).cameo_probability, player_rates(full).cameo_probability
        )
        self.assertGreater(
            sum(player_rates(full).starting_minutes) / len(player_rates(full).starting_minutes), 80
        )
        self.assertLess(player_rates(sub).start, 0.4)  # Four prior matches prevent abrupt zero.

    def test_availability_applies_once_to_appearance_rates(self):
        p = fixture_state().players[12]
        fit, doubtful = player_rates(p), player_rates(replace(p, chance_of_playing=50))
        self.assertAlmostEqual(doubtful.start, 0.5 * fit.start)
        self.assertAlmostEqual(doubtful.cameo_probability, 0.5 * fit.cameo_probability)

    def test_histories_are_bounded_cached_and_frozen_per_generation(self):
        state = fixture_state()
        state.provenance = "official_fpl"
        client = FPLClient()
        with patch.object(client, "player_history", return_value={"history": []}) as fetch:
            enrich = ForecastEnricher(client)
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(enrich.prime, [state] * 4))
                a = enrich.prepare(state)
                b = enrich.prepare(replace(state, league_id=999))
                self.assertLessEqual(fetch.call_count, 12)
                self.assertIs(enrich.prepare(state), a)
                self.assertEqual(a.players, b.players)
            finally:
                enrich.executor.shutdown(wait=True)

    def test_missing_history_and_missing_provider_fail_soft(self):
        state = fixture_state()
        state.provenance = "official_fpl"
        with (
            patch.object(FPLClient, "player_history", side_effect=OSError("unavailable")),
            patch.dict("os.environ", {}, clear=True),
        ):
            enrich = ForecastEnricher(FPLClient())
            try:
                self.assertEqual(enrich.prepare(state).players, state.players)
                self.assertIsNone(enrich.lineups)
            finally:
                enrich.executor.shutdown(wait=True)

    def test_client_history_requests_are_coalesced(self):
        client = FPLClient()
        with (
            patch.object(client, "_get", return_value={"history": []}) as fetch,
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            list(pool.map(client.player_history, [12] * 4))
        self.assertEqual(fetch.call_count, 1)


class EventAndFixtureIntelligenceTests(unittest.TestCase):
    def test_empty_league_has_no_fabricated_event_or_checkpoint(self):
        state = fixture_state(1)
        state.managers = ()
        result = outlook(state, samples=10)
        self.assertEqual(result["dangerous_events"], [])
        self.assertEqual(result["fixture_timeline"], [])
        self.assertEqual(result["managers"], [])

    def test_conditional_impacts_match_same_joint_samples(self):
        state = fixture_state(2)
        n, seed = 800, 19
        result = outlook(state, samples=n, seed=seed)
        self.assertTrue(result["dangerous_events"])
        event = next(
            (e for e in result["dangerous_events"] if e["condition"]["kind"] == "goal"),
            result["dangerous_events"][0],
        )
        c = event["condition"]
        rng, model = random.Random(seed), FixtureModel(state)
        matched, last = [], []
        for i in range(n):
            values, samples = model.draw(rng)
            sample = next(s for s in samples if s.fixture_id == c["fixture_id"])
            if c["kind"] == "goal":
                flag = sample.stats[c["player_id"]].get("goals_scored", 0) > 0
            elif c["kind"] == "blank":
                s = sample.stats[c["player_id"]]
                flag = not s.get("goals_scored") and not s.get("assists")
            elif c["kind"] == "clean_sheet":
                f = model.by_fixture[c["fixture_id"]]
                flag = (
                    sample.away_goals if c["team_id"] == f.home_team_id else sample.home_goals
                ) == 0
            else:
                flag = (
                    sample.stats[c["player_id"]]["minutes"] > 0
                    and sample.windows[c["player_id"]][0] > 0
                )
            if flag:
                matched.append(i)
            last.append(last_ids(final_manager_scores(state, values)))
        self.assertEqual(event["matching_samples"], len(matched))
        self.assertEqual(event["occurrence_probability"], round(len(matched) / n, 4))
        for row in event["managers"]:
            expected = sum(row["entry_id"] in last[i] for i in matched) / len(matched)
            self.assertEqual(row["event_risk"], round(expected, 4))
            self.assertTrue(row["sampling_band"][0] > 0 or row["sampling_band"][1] < 0)
        self.assertEqual(event["interpretation"], "sample_conditional")
        forced = outlook(
            state,
            samples=80,
            conditions=(
                Condition(
                    c["fixture_id"], c["kind"], player_id=c["player_id"], team_id=c["team_id"]
                ),
            ),
        )
        self.assertEqual(forced["interpretation"], "forced_intervention")
        self.assertEqual(forced["dangerous_events"], [])

    def test_small_samples_do_not_create_noisy_event_cards(self):
        result = outlook(fixture_state(), samples=39)
        self.assertEqual(result["dangerous_events"], [])

    def test_summary_skips_expensive_intelligence(self):
        result = outlook(fixture_state(), samples=12, include_risk=False)
        self.assertEqual(result["dangerous_events"], [])
        self.assertEqual(result["fixture_timeline"], [])

    def test_simultaneous_overlapping_unknown_and_later_kickoffs(self):
        fixtures = fixture_state(3).fixtures
        now = datetime.now(UTC)
        a, b, c = [
            replace(f, kickoff_time=now + timedelta(hours=i)) for i, f in enumerate(fixtures)
        ]
        self.assertEqual(len(fixture_groups((c, a, b))), 1)
        c = replace(c, kickoff_time=now + timedelta(hours=4))
        self.assertEqual([[f.id for f in g] for g in fixture_groups((c, a, b))], [[1, 2], [3]])
        self.assertEqual(len(fixture_groups((replace(a, kickoff_time=None), c))), 1)

    def test_checkpoint_scores_reconcile_with_final_and_keep_doubles(self):
        state = fixture_state(2)
        now = datetime.now(UTC)
        first = replace(state.fixtures[0], kickoff_time=now)
        state.fixtures = (
            first,
            replace(first, id=90, kickoff_time=now + timedelta(days=1)),
            replace(state.fixtures[1], kickoff_time=now + timedelta(days=2)),
        )
        result = outlook(state, samples=100, seed=17)
        self.assertEqual(len(result["fixture_forecasts"]), 3)
        self.assertEqual(len(result["fixture_timeline"]), 3)
        final = {r["entry_id"]: r for r in result["managers"]}
        for row in result["fixture_timeline"][-1]["managers"]:
            self.assertEqual(
                row["projected_checkpoint_score"], final[row["entry_id"]]["projected_score"]
            )
            self.assertEqual(
                row["checkpoint_bottom_probability"], final[row["entry_id"]]["last_probability"]
            )
            self.assertEqual(row["remaining_contribution_interval_80"], [0, 0])
        self.assertEqual(result["fixture_timeline"][-1]["remaining_uncertainty_sd"], 0)

    def test_50_manager_payload_has_bounded_cards_and_one_checkpoint_prefix(self):
        result = outlook(validation_state(5, 50), samples=40)
        self.assertEqual(len(result["managers"]), 50)
        self.assertLessEqual(len(result["dangerous_events"]), 5)
        self.assertLessEqual(result["event_screening"]["candidate_count"], 32)
        self.assertTrue(all(g["samples"] == 40 for g in result["fixture_timeline"]))
        self.assertTrue(all(len(f["managers"]) <= 50 for f in result["fixture_forecasts"]))


def provider_fixture():
    state = fixture_state(1)
    state.fixtures = (replace(state.fixtures[0], kickoff_time=datetime.now(UTC)),)
    f = state.fixtures[0]
    metadata = {
        "fixture": {"id": 1001, "date": f.kickoff_time.isoformat()},
        "league": {"id": 39},
        "teams": {"home": {"id": 101}, "away": {"id": 102}},
    }
    mapping = {
        "fixtures": {"1001": 1},
        "teams": {"101": 1, "102": 2},
        "players": {"101": 1, "103": 3, "105": 5, "107": 7},
    }
    lineups = [
        {
            "team": {"id": 101},
            "startXI": [{"player": {"id": pid}} for pid in (101, 103, 105, *range(9001, 9009))],
            "substitutes": [{"player": {"id": 107}}],
        }
    ]
    return state, metadata, lineups, mapping


class ExternalLineupTests(unittest.TestCase):
    def test_explicit_ids_and_confirmed_status_without_name_matching(self):
        state, meta, lineups, mapping = provider_fixture()
        records = normalise_lineups(state, meta, lineups, [], mapping, datetime.now(UTC))
        self.assertEqual(len(records), 4)
        self.assertEqual(records[(1, 1)].status, "starter")
        self.assertEqual(records[(7, 1)].status, "bench")
        self.assertEqual(records[(1, 1)].provider_fixture_id, 1001)
        state.confirmed_appearances = records
        projections = {
            p.player_id: p
            for p in project_players(football_inputs(state), player_ids=(1, 7), samples=250)
        }
        self.assertEqual(projections[1].starting_probability, 1)
        self.assertEqual(projections[7].starting_probability, 0)
        self.assertTrue(0 < projections[7].appearance_probability < 1)
        self.assertGreater(projections[1].expected_minutes, projections[7].expected_minutes)

    def test_bad_fixture_team_player_or_duplicate_mapping_rejected(self):
        state, meta, lineups, mapping = provider_fixture()
        bad_maps = [copy.deepcopy(mapping) for _ in range(3)]
        bad_maps[0]["teams"]["101"] = 2
        bad_maps[1]["players"]["103"] = 2
        bad_maps[2]["players"]["103"] = 1
        for bad in bad_maps:
            with self.assertRaises(ValueError):
                normalise_lineups(state, meta, lineups, [], bad, datetime.now(UTC))
        meta["fixture"]["date"] = (state.fetched_at + timedelta(days=2)).isoformat()
        with self.assertRaises(ValueError):
            normalise_lineups(state, meta, lineups, [], mapping, datetime.now(UTC))

    def test_substitutions_resolve_active_ids_and_improve_live_minutes(self):
        state, meta, lineups, mapping = provider_fixture()
        events = [
            {"type": "subst", "time": {"elapsed": 60}, "player": {"id": 107}, "assist": {"id": 103}}
        ]
        records = normalise_lineups(state, meta, lineups, events, mapping, datetime.now(UTC))
        self.assertEqual(records[(3, 1)].off_minute, 60)
        self.assertEqual(records[(7, 1)].on_minute, 60)
        state.confirmed_appearances = records
        state.fixtures = (
            replace(
                state.fixtures[0], started=True, elapsed_minutes=70, home_score=0, away_score=0
            ),
        )
        state.fixture_stats = {
            (pid, 1): {"minutes": 60 if pid == 3 else 10 if pid == 7 else 70}
            for pid in state.players
        }
        state.live_scores = {
            pid: ElementScore(1, s["minutes"]) for (pid, _), s in state.fixture_stats.items()
        }
        model = FixtureModel(state)
        sample = model.sample_fixture(state.fixtures[0], random.Random(1))
        self.assertEqual(sample.stats[3]["minutes"], 60)
        self.assertGreater(sample.stats[7]["minutes"], 10)
        self.assertLessEqual(sample.stats[7]["minutes"], 30)
        with self.assertRaisesRegex(ValueError, "already left"):
            FixtureModel(state, (Condition(1, "goal", player_id=3),))

    def test_benchmark_ignores_confirmed_override_and_missing_live_stats_remain_unknown(self):
        state, meta, lineups, mapping = provider_fixture()
        state.confirmed_appearances = normalise_lineups(
            state, meta, lineups, [], mapping, datetime.now(UTC)
        )
        state.players[1] = replace(state.players[1], chance_of_playing=0)
        inputs = football_inputs(state)
        old = project_players(
            replace(inputs, model_version=BENCHMARK_MODEL_VERSION), player_ids=(1,), samples=40
        )[0]
        current = project_players(inputs, player_ids=(1,), samples=40)[0]
        self.assertEqual(old.starting_probability, 0)
        self.assertEqual(current.starting_probability, 1)
        state.fixtures = (replace(state.fixtures[0], started=True, elapsed_minutes=20),)
        missing = project_players(football_inputs(state), player_ids=(1,), samples=40)[0]
        self.assertIsNone(missing.expected_points)
        self.assertEqual(missing.coverage, "missing_live_breakdown")

    def test_saved_normalised_lineups_replay_with_confirmation_provenance(self):
        snapshot = {
            "league_id": 1,
            "gameweek": 1,
            "standings": [],
            "picks": {},
            "fixtures": [
                {"id": 1, "team_h": 1, "team_a": 2, "kickoff_time": datetime.now(UTC).isoformat()}
            ],
            "live": {"elements": []},
            "bootstrap": {
                "teams": [{"id": 1, "name": "Home"}, {"id": 2, "name": "Away"}],
                "elements": [{"id": 1, "web_name": "Player", "team": 1, "element_type": 1}],
            },
            "confirmed_appearances": [
                {
                    "player_id": 1,
                    "fixture_id": 1,
                    "provider_player_id": 10,
                    "provider_fixture_id": 20,
                    "status": "starter",
                    "confirmed_at": datetime.now(UTC).isoformat(),
                    "source": "test-licensed-source",
                    "on_minute": 0,
                }
            ],
        }
        state = state_from_snapshot(snapshot)
        self.assertEqual(state.confirmed_appearances[(1, 1)].provider_player_id, 10)
        self.assertIn("test-licensed-source", {s.name for s in football_inputs(state).provenance})
        record = prediction_record(state, samples=40)
        self.assertEqual(record["lineup_provenance"][0]["source"], "test-licensed-source")
        snapshot["confirmed_appearances"][0]["fixture_id"] = 99
        with self.assertRaises(ValueError):
            state_from_snapshot(snapshot)

    def test_unlicensed_or_missing_credentials_and_provider_errors_fall_back(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(configured_lineups())
        state, _meta, _lineups, mapping = provider_fixture()
        adapter = APIFootballLineups("test-only", mapping)
        with patch.object(adapter, "_get", side_effect=OSError("provider down")):
            records, coverage = adapter.enrich(state)
        self.assertEqual(records, {})
        self.assertFalse(coverage["lineup_feed_verified"])

    def test_provider_caching_and_request_budget(self):
        state, meta, lineups, mapping = provider_fixture()
        adapter = APIFootballLineups("test-only", mapping, daily_limit=1)
        with patch.object(
            adapter, "_get", side_effect=[{"response": [meta]}, {"response": lineups}]
        ) as fetch:
            a = adapter.enrich(state)[0]
            b = adapter.enrich(replace(state, league_id=999))[0]
            self.assertEqual(a, b)
            self.assertEqual(fetch.call_count, 2)
        adapter._day = datetime.now(UTC).date().isoformat()
        adapter._calls = adapter.limit
        with self.assertRaisesRegex(ValueError, "budget"):
            adapter._get("fixtures?id=1001")


class DurableRiskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "history.sqlite3"
        self.state = fixture_state(1)
        self.state.provenance = "official_fpl"
        self.record = prediction_record(self.state, samples=60)

    def test_restart_duplicate_and_league_isolation(self):
        store = RiskHistoryStore(self.path)
        self.assertTrue(store.append(self.record))
        self.assertFalse(store.append(self.record))
        reopened = RiskHistoryStore(self.path)
        self.assertEqual(len(reopened.read(123, 1)["series"][0]["observations"]), 1)
        self.assertEqual(reopened.read(999, 1)["series"], [])

    def test_versions_settings_and_membership_have_separate_series(self):
        store = RiskHistoryStore(self.path)
        store.append(self.record)
        for change in (
            {"model_version": BENCHMARK_MODEL_VERSION},
            {"seed": 99},
            {"tie_rule": "strict"},
            {"managers": self.record["managers"][:2]},
        ):
            store.append({**self.record, **change})
        history = store.read(123, 1)
        self.assertEqual(len(history["series"]), 5)
        self.assertTrue(
            all(
                g["observations"][0]["managers"][0]["risk_change"] is None
                for g in history["series"]
            )
        )

    def test_real_changes_context_and_source_cutoff(self):
        store = RiskHistoryStore(self.path)
        store.append(self.record)
        second = copy.deepcopy(self.record)
        source = datetime.fromisoformat(second["source"]) + timedelta(minutes=1)
        second.update(
            source=source.isoformat(), recorded_at=(source + timedelta(seconds=1)).isoformat()
        )
        second["managers"][0]["last_probability"] += 0.1
        second["player_observations"][0]["points"] += 5
        second["fixture_status"][0]["home_score"] = 1
        store.append(second)
        history = store.read(123, 1)
        last = history["series"][0]["observations"][-1]
        self.assertEqual(last["managers"][0]["risk_change"], 0.1)
        self.assertEqual(last["player_changes"][0]["points_change"], 5)
        self.assertTrue(last["annotations"])
        self.assertEqual(
            len(store.read(123, 1, source=self.record["source"])["series"][0]["observations"]), 1
        )

    def test_replays_synthetic_and_stale_records_rejected(self):
        store = RiskHistoryStore(self.path)
        for change in (
            {"provenance": "replay"},
            {"data_provenance": "synthetic"},
            {"recorded_at": (self.state.fetched_at + timedelta(hours=1)).isoformat()},
        ):
            with self.assertRaises(ValueError):
                store.append({**self.record, **change})
        self.assertEqual(store.read(123, 1)["series"], [])

    def test_disabled_and_unavailable_storage_are_explicit(self):
        self.assertFalse(RiskHistoryStore().read(123, 1)["recording_enabled"])
        self.path.write_text("not a sqlite database")
        self.assertEqual(RiskHistoryStore(self.path).read(123, 1)["status"], "storage_unavailable")

    def test_public_history_endpoint_is_read_only_and_does_not_fetch_fpl(self):
        store = RiskHistoryStore(self.path)
        store.append(self.record)
        client = FPLClient()
        service = AnalysisService(client, history=store)
        with (
            TestClient(create_app(service)) as api,
            patch.object(client, "fetch_snapshot") as fetch,
        ):
            response = api.get("/api/league/123/risk-history?gameweek=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ready")
            self.assertEqual(api.get("/api/league/123/risk-history?gameweek=99").status_code, 422)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
