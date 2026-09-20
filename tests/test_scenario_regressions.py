from __future__ import annotations

import itertools
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from fpl_forfeit.api import FPLAPIError, state_from_snapshot
from fpl_forfeit.availability import availability_payload, minutes_cost
from fpl_forfeit.demo import demo_state
from fpl_forfeit.models import ElementScore, Fixture
from fpl_forfeit.ranking import scenario_cost
from fpl_forfeit.scenarios import football_consistent, outcome_catalog, route_key
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.settings import AnalysisSettings
from fpl_forfeit.solver import build_variables, projected_scores, solve_candidate
from fpl_forfeit.web import create_app
from tests.helpers import league_state, manager, players, scores
from tests.test_product_features import HistoricalClient


def narrow_tie_state():
    """Captain/non-captain vs a completed opponent: exactly two MID points are needed."""
    first = manager(1, captain=7, vice=13)
    second = manager(2, captain=13)
    third = replace(
        manager(3, captain=13),
        picks=tuple(
            replace(
                pick,
                player_id=11
                if pick.player_id == 7
                else 7
                if pick.player_id == 11
                else pick.player_id,
            )
            for pick in manager(3, captain=13).picks
        ),
    )
    values = scores(0)
    values.update({1: ElementScore(29, 90), 13: ElementScore(2, 90), 11: ElementScore(2, 90)})
    fixture = Fixture(77, 1, 7, 99, "Home", "Away", False, False)
    state = league_state((first, second, third), values, (fixture,))
    for player_id in (7, 5, 6, 15):
        state.players[player_id] = replace(
            state.players[player_id],
            team_id=7,
            season_starts=4,
            season_minutes=360,
            season_team_matches=4,
            availability_status="a",
        )
        state.live_scores[player_id] = ElementScore(0, 0)
    return state


class ScenarioSearchRegressionTests(unittest.TestCase):
    def test_narrow_tie_still_has_three_routes_after_kickoff(self):
        state = narrow_tie_state()
        state.fixtures = (replace(state.fixtures[0], started=True, home_score=0, away_score=0),)
        state.live_scores[7] = ElementScore(1, 8)
        result = solve_candidate(state, state.managers[1])
        self.assertEqual(len(result.scenarios), 3)
        for scenario in result.scenarios:
            self.assertEqual(scenario.final_scores[2], min(scenario.final_scores.values()))
            deciding = next(o for o in scenario.outcomes if o.player_id == 7)
            self.assertEqual(deciding.points_delta, 1)
            self.assertNotIn("does not", deciding.label)

    def test_narrow_tie_produces_three_distinct_verified_routes_quickly(self):
        state = narrow_tie_state()
        result = solve_candidate(state, state.managers[1], max_nodes=2_000)
        self.assertEqual(len(result.scenarios), 3)
        self.assertLess(result.nodes_checked, 1_000)
        variables, _ = build_variables(state, include_baselines=True)
        routes = set()
        for example in result.scenarios:
            self.assertTrue(football_consistent(example.outcomes, variables))
            self.assertEqual(example.final_scores, projected_scores(state, example.outcomes))
            self.assertEqual(example.final_scores[2], min(example.final_scores.values()))
            outcome = next(o for o in example.outcomes if o.player_id == 7)
            self.assertEqual(outcome.points_delta, 2)
            self.assertGreater(outcome.minutes_delta, 0)
            routes.add(route_key(outcome))
        self.assertEqual(len(routes), 3)

    def test_impossible_candidate_pruned_without_enumerating_cartesian_product(self):
        state = narrow_tie_state()
        candidate = replace(state.managers[1], entry_id=4)
        state.managers = tuple(replace(m, transfer_cost=100) for m in state.managers) + (candidate,)
        result = solve_candidate(state, candidate, max_nodes=2_000)
        self.assertFalse(result.scenarios)
        self.assertTrue(result.exhausted)
        self.assertLess(result.nodes_checked, 10)

    def test_omitted_dimensions_keep_explicit_baselines_not_silent_absences(self):
        state = narrow_tie_state()
        variables, omitted = build_variables(state, max_relevant=1, include_baselines=True)
        self.assertEqual(len(variables), 4)
        self.assertEqual(omitted, 3)
        for variable in variables[1:]:
            self.assertEqual(len(variable.outcomes), 1)
            self.assertTrue(variable.outcomes[0].baseline)
            self.assertEqual(variable.outcomes[0].minutes_delta, 60)

    def test_broad_summary_is_cheap_but_explicit_candidate_can_be_searched(self):
        state = demo_state("early")
        self.assertFalse(analyse_state(state).searches)
        selected = analyse_state(state, candidate_id=state.managers[0].entry_id)
        self.assertEqual(set(selected.searches), {state.managers[0].entry_id})
        payload = analysis_to_dict(selected)
        manager_payload = next(
            m for m in payload["managers"] if m["entry_id"] == state.managers[0].entry_id
        )
        self.assertTrue(manager_payload["search"]["loaded"])
        self.assertIsNotNone(manager_payload["search"]["assumptions_note"])

    def test_strict_tie_search_matches_exhaustive_authoritative_scorer(self):
        state = narrow_tie_state()
        # One free differential plus a double fixture checks presence across the whole GW.
        state.fixtures += (Fixture(78, 1, 7, 98, "Home", "Other", False, False),)
        for player_id in (5, 6, 15):
            state.players[player_id] = replace(state.players[player_id], team_id=player_id)
            state.live_scores[player_id] = ElementScore(0, 90)
        variables, _ = build_variables(state)
        for tied in (True, False):
            for candidate in state.managers:
                possible = False
                for outcomes in itertools.product(*(v.outcomes for v in variables)):
                    if not football_consistent(outcomes, variables):
                        continue
                    final = projected_scores(state, outcomes)
                    other = min(
                        value for entry, value in final.items() if entry != candidate.entry_id
                    )
                    if (
                        final[candidate.entry_id] <= other
                        if tied
                        else final[candidate.entry_id] < other
                    ):
                        possible = True
                        break
                result = solve_candidate(state, candidate, allow_tied_last=tied)
                self.assertEqual(bool(result.scenarios), possible)

    def test_fewer_than_three_routes_are_reported_honestly(self):
        values = scores(0)
        values[7] = ElementScore(0, 0)
        state = league_state(
            (manager(1), manager(2)), values, (Fixture(1, 1, 7, 99, "A", "B", False, False),)
        )
        payload = analysis_to_dict(analyse_state(state))
        for item in payload["managers"]:
            self.assertEqual(len(item["scenarios"]), 1)
            self.assertEqual(item["search"]["found_count"], 1)
            self.assertIn("fewer than", item["search"]["shortfall_note"])


class AvailabilityRankingTests(unittest.TestCase):
    def test_shared_clean_sheet_is_not_ranked_as_independent_coincidences(self):
        mids = [replace(players()[7], id=index, team_id=7) for index in (7, 8, 9)]
        fixture = Fixture(1, 1, 7, 99, "A", "B", False, False)
        clean = tuple(
            next(
                o
                for o in outcome_catalog(p, fixture, ElementScore(0, 0))
                if o.opponent_max_goals == 0 and o.points_delta == 3
            )
            for p in mids
        )
        self.assertEqual(scenario_cost(clean), scenario_cost(clean[:1]))
        booked = next(
            o
            for o in outcome_catalog(mids[0], fixture, ElementScore(0, 0))
            if o.label == "keeps a clean sheet but is booked"
        )
        self.assertGreater(scenario_cost((booked,) + clean[1:]), scenario_cost(clean))

    def setUp(self):
        self.player = replace(
            players()[7],
            season_starts=4,
            season_minutes=360,
            season_team_matches=4,
            availability_status="a",
            price_tenths=120,
        )
        self.fixture = Fixture(1, 1, 7, 99, "A", "B", False, False)

    def test_regular_starter_absences_rank_below_normal_football_events(self):
        catalog = outcome_catalog(self.player, self.fixture, ElementScore(0, 0))
        absence = next(o for o in catalog if o.label == "does not play")
        goal = next(o for o in catalog if o.label == "scores")
        self.assertGreater(absence.plausibility_cost, goal.plausibility_cost)
        self.assertEqual(availability_payload(self.player)["label"], "Regular starter")

    def test_midfielder_negative_totals_include_explicit_no_clean_sheet_assumption(self):
        catalog = outcome_catalog(self.player, self.fixture, ElementScore(0, 0))
        for item in catalog:
            if item.label in {
                "plays 60+ minutes and is sent off",
                "plays 60+ minutes and misses a penalty",
            }:
                self.assertEqual(item.opponent_min_goals, 1)
        booked_clean = next(o for o in catalog if o.label == "keeps a clean sheet but is booked")
        self.assertEqual((booked_clean.points_delta, booked_clean.minutes_delta), (2, 60))
        self.assertEqual(booked_clean.opponent_max_goals, 0)

    def test_injury_flag_changes_ranking_not_valid_outcomes_or_points(self):
        injured = replace(self.player, availability_status="i", chance_of_playing=0)
        healthy = outcome_catalog(self.player, self.fixture, ElementScore(0, 0))
        flagged = outcome_catalog(injured, self.fixture, ElementScore(0, 0))
        self.assertEqual(flagged[0].label, "does not play")
        self.assertEqual(
            {(o.label, o.points_delta, o.minutes_delta) for o in healthy},
            {(o.label, o.points_delta, o.minutes_delta) for o in flagged},
        )

    def test_price_is_weak_and_cannot_override_starting_history(self):
        cheap_regular = replace(self.player, price_tenths=40)
        expensive_bench = replace(self.player, season_starts=0, season_minutes=30, price_tenths=140)
        self.assertGreater(minutes_cost(cheap_regular, 0), minutes_cost(expensive_bench, 0))
        self.assertLessEqual(minutes_cost(self.player, 0) - minutes_cost(cheap_regular, 0), 0.3)

    def test_missing_history_stays_unknown_and_price_alone_is_not_evidence(self):
        unknown = replace(players()[7], price_tenths=140)
        self.assertEqual(minutes_cost(unknown, 0), 0)
        self.assertIsNone(availability_payload(unknown)["season_starts"])

    def test_live_minutes_override_pregame_injury_ranking(self):
        live = replace(self.fixture, started=True)
        flagged = replace(self.player, availability_status="i", chance_of_playing=0)
        healthy = outcome_catalog(self.player, live, ElementScore(2, 70))
        injured = outcome_catalog(flagged, live, ElementScore(2, 70))
        self.assertEqual(healthy, injured)
        self.assertFalse(any(o.label == "does not play" for o in injured))

    def test_availability_is_serialised_for_squads_differentials_and_scenarios(self):
        state = narrow_tie_state()
        payload = analysis_to_dict(analyse_state(state))
        differential = next(p for p in payload["differentials"] if p["player_id"] == 7)
        self.assertEqual(differential["availability"]["season_starts"], 4)
        player = next(p for p in payload["managers"][0]["squad"] if p["player_id"] == 7)
        self.assertEqual(player["availability"]["season_minutes"], 360)
        event = payload["managers"][0]["scenarios"][0]["events"][0]
        self.assertIn("fpl_chance_of_playing", event["availability"])

    def test_api_parses_public_metadata_and_counts_fixtures_not_gameweeks(self):
        snapshot = HistoricalClient().fetch_snapshot(123)
        snapshot["bootstrap"]["elements"][6].update(
            {
                "starts": 3,
                "minutes": 250,
                "now_cost": 95,
                "status": "d",
                "news": "Knock",
                "chance_of_playing_this_round": 75,
            }
        )
        snapshot["season_fixtures"] = [
            {"team_h": 7, "team_a": 99, "started": True, "event": 1},
            {"team_h": 7, "team_a": 98, "started": True, "event": 1},
            {"team_h": 7, "team_a": 97, "started": False, "event": 2},
        ]
        player = state_from_snapshot(snapshot).players[7]
        self.assertEqual(
            (player.season_starts, player.season_minutes, player.price_tenths), (3, 250, 95)
        )
        self.assertEqual(player.season_team_matches, 2)
        self.assertEqual(player.chance_of_playing, 75)
        self.assertEqual(player.availability_news, "Knock")


class ScenarioSourceTests(unittest.TestCase):
    def test_live_fixtures_do_not_use_the_stale_season_feed(self):
        client = HistoricalClient()
        original = client._get

        def feed(path):
            if path == "fixtures/":
                return [
                    {
                        "id": 99,
                        "event": 2,
                        "team_h": 7,
                        "team_a": 8,
                        "started": False,
                        "team_h_score": None,
                        "team_a_score": None,
                    }
                ]
            if path.startswith("fixtures/?event="):
                return [
                    {
                        "id": 99,
                        "event": 2,
                        "team_h": 7,
                        "team_a": 8,
                        "started": True,
                        "team_h_score": 1,
                        "team_a_score": 0,
                    }
                ]
            return original(path)

        with patch.object(client, "_get", side_effect=feed) as get:
            first = client.fetch_snapshot(123)
            client.fetch_snapshot(123)
        self.assertEqual(first["fixtures"][0]["team_h_score"], 1)
        self.assertTrue(first["season_fixtures"][0]["started"])
        self.assertEqual(sum(call.args[0] == "fixtures/" for call in get.call_args_list), 1)

    def test_optional_season_feed_failure_does_not_block_analysis(self):
        client = HistoricalClient()
        original = client._get

        def unavailable(path):
            if path == "fixtures/":
                raise FPLAPIError("Season context unavailable")
            return original(path)

        with patch.object(client, "_get", side_effect=unavailable):
            snapshot = client.fetch_snapshot(123)
        self.assertNotIn("season_fixtures", snapshot)
        self.assertIsNone(state_from_snapshot(snapshot).players[7].season_team_matches)

    def test_detail_uses_summary_generation_after_live_cache_expires(self):
        service = AnalysisService(HistoricalClient())
        with patch("fpl_forfeit.cache.monotonic", return_value=1):
            summary = service.analyse_league(123, include_scenarios=False)
        with (
            patch("fpl_forfeit.cache.monotonic", return_value=95),
            patch.object(
                service.client, "fetch_snapshot", side_effect=AssertionError("Must not refetch")
            ),
        ):
            result = service.analyse_league(
                123, candidate_id=1, source=summary["league"]["fetched_at"]
            )
        self.assertEqual(result["league"]["fetched_at"], summary["league"]["fetched_at"])

    def test_expired_or_wrong_league_source_returns_refresh_instruction(self):
        service = AnalysisService(HistoricalClient())
        with TestClient(create_app(service)) as client:
            response = client.get("/api/league/123/managers/1/scenarios?source=missing")
            self.assertEqual(response.status_code, 409)
            self.assertIn("Refresh", response.json()["detail"])
            self.assertEqual(
                client.get("/api/league/123/managers/1/scenarios?source=" + "x" * 81).status_code,
                422,
            )

    def test_lineup_budget_exhaustion_never_proves_no_modelled_path(self):
        state = narrow_tie_state()
        with patch("fpl_forfeit.solver.MAX_SCENARIO_LINEUPS", 0):
            result = analyse_state(state, AnalysisSettings())
        self.assertTrue(all(not search.exhausted for search in result.searches.values()))
        self.assertTrue(
            all(m["status"] == "unresolved" for m in analysis_to_dict(result)["managers"])
        )


if __name__ == "__main__":
    unittest.main()
