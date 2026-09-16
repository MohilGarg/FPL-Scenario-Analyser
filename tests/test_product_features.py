from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from fpl_forfeit.api import FPLClient, FPLGameweekUnavailable, FPLNotFoundError, state_from_snapshot
from fpl_forfeit.comparison import compare_managers
from fpl_forfeit.demo import demo_state
from fpl_forfeit.models import ElementScore, Fixture, Player, Position
from fpl_forfeit.safety import conservative_score_bounds
from fpl_forfeit.scenarios import Outcome, outcome_catalog
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.settings import AnalysisSettings
from fpl_forfeit.solver import _diversity_signature, solve_candidate
from fpl_forfeit.state import current_standings
from fpl_forfeit.web import create_app
from tests.helpers import league_state, manager, players, scores


class ComparisonTests(unittest.TestCase):
    def test_shared_player_cancels_but_captain_difference_remains(self) -> None:
        a, b = manager(1, captain=12), manager(2, captain=13)
        state = league_state((a, b), scores(), (Fixture(1, 1, 12, 2, "A", "B", False, False),))
        result = compare_managers(state, a, b, {1: 30, 2: 26})
        self.assertEqual([p["player_id"] for p in result["shared_players"]], [2])
        self.assertEqual(result["differentials"][0]["a_multiplier"], 2)
        self.assertEqual(result["differentials"][0]["b_multiplier"], 1)
        self.assertEqual(result["required_relative_points"], 4)

    def test_strict_rule_requires_one_more_relative_point(self) -> None:
        state = demo_state("late")
        a, b = state.managers[:2]
        include = compare_managers(state, a, b, {a.entry_id: 20, b.entry_id: 20})
        strict = compare_managers(
            state, a, b, {a.entry_id: 20, b.entry_id: 20}, allow_tied_last=False
        )
        self.assertEqual(include["required_relative_points"], 0)
        self.assertEqual(strict["required_relative_points"], 1)
        self.assertTrue(include["conditional_exposure"])

    def test_safe_manager_can_be_compared_not_just_focused_matrix(self) -> None:
        state = demo_state("late")
        a, b = state.managers[0], state.managers[8]
        result = compare_managers(state, a, b, {a.entry_id: 45, b.entry_id: 94})
        self.assertTrue(result["differentials"])
        self.assertEqual(result["gap"], -49)


class RichScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(1, 1, 1, 99, "Team", "Opponent", False, False)

    def test_midfielder_assist_with_clean_sheet_has_six_points(self) -> None:
        player = Player(1, "Mid", 1, "Team", Position.MIDFIELDER)
        outcomes = outcome_catalog(player, self.fixture, ElementScore(0, 0))
        outcome = next(
            item for item in outcomes if item.label == "gets an assist and keeps a clean sheet"
        )
        self.assertEqual(outcome.points_delta, 6)
        self.assertEqual(outcome.opponent_max_goals, 0)
        self.assertLessEqual(len(outcomes), 30)

    def test_goalkeeper_goal_uses_current_ten_point_rule(self) -> None:
        player = Player(1, "Keeper", 1, "Team", Position.GOALKEEPER)
        outcomes = outcome_catalog(player, self.fixture, ElementScore(0, 0))
        self.assertEqual(
            next(
                item for item in outcomes if item.label == "scores but gets no clean sheet"
            ).points_delta,
            12,
        )
        self.assertTrue(any("saves a penalty and keeps" in item.label for item in outcomes))

    def test_yellow_own_goal_and_multi_return_are_available(self) -> None:
        player = Player(1, "Forward", 1, "Team", Position.FORWARD)
        outcomes = outcome_catalog(player, self.fixture, ElementScore(0, 0))
        for text in ("booked", "own goal", "twice and assists", "hat-trick"):
            self.assertTrue(any(text in item.label for item in outcomes))

    def test_live_attacking_return_can_coexist_with_clean_sheet_loss(self) -> None:
        player = Player(1, "Defender", 1, "Team", Position.DEFENDER)
        fixture = replace(self.fixture, started=True, home_score=0, away_score=0)
        outcomes = outcome_catalog(player, fixture, ElementScore(6, 70))
        outcome = next(
            item for item in outcomes if item.label == "scores but loses the clean sheet"
        )
        self.assertEqual(outcome.points_delta, 2)
        self.assertIsNone(outcome.opponent_max_goals)

    def test_brace_opens_a_path_beyond_old_single_goal_vocabulary(self) -> None:
        candidate, opponent = manager(1, captain=13), manager(2, captain=12, transfer_cost=10)
        state = league_state(
            (candidate, opponent), scores(0), (Fixture(1, 1, 12, 99, "A", "B", False, False),)
        )
        result = solve_candidate(state, candidate, limit=1, max_nodes=100)
        self.assertTrue(result.scenarios)
        self.assertTrue(any("scores twice" in item.label for item in result.scenarios[0].outcomes))

    def test_diversity_does_not_count_incidental_booking_as_new_route(self) -> None:
        goal = Outcome(1, 1, "scores", 5, 0, 1.8)
        booking = Outcome(2, 1, "is booked", -1, 0, 0.8)
        assist = replace(goal, label="gets an assist", points_delta=3)
        exposure = {1: {1: 0, 2: 1}, 2: {1: 1, 2: 0}}
        signature = lambda outcomes: _diversity_signature(outcomes, {1: 20, 2: 21}, 1, exposure)
        self.assertEqual(signature((goal,)), signature((goal, booking)))
        self.assertEqual(signature(()), signature((booking,)))
        self.assertNotEqual(signature((goal,)), signature((assist,)))

    def test_summary_skips_solver_and_manager_request_only_solves_one(self) -> None:
        state = demo_state("late")
        with patch("fpl_forfeit.analysis.solve_candidate", wraps=solve_candidate) as solve:
            analyse_state(state, include_scenarios=False)
            solve.assert_not_called()
            analyse_state(state, candidate_id=state.managers[0].entry_id)
            self.assertEqual(solve.call_count, 1)

    def test_pending_autosub_includes_already_scored_bench_points_in_safety(self) -> None:
        values = scores(0)
        values[2] = ElementScore(0, 0)
        values[5] = ElementScore(20, 90)
        state = league_state((manager(1),), values, (Fixture(1, 1, 2, 99, "A", "B", False, False),))
        bounds = conservative_score_bounds(state, {1: 0})[1]
        self.assertEqual(bounds.upper, 55)  # +35 remaining return, +20 existing bench points


class HistoricalClient(FPLClient):
    """Small protocol fixture, not a synthetic live-Gameweek simulation."""

    def _get(self, path: str) -> object:
        if path == "bootstrap-static/":
            return {
                "events": [{"id": 1, "finished": True}, {"id": 2, "is_current": True}, {"id": 3}],
                "teams": [{"id": p.id, "name": p.team_name} for p in players().values()],
                "elements": [
                    {
                        "id": p.id,
                        "web_name": p.name,
                        "team": p.id,
                        "element_type": {
                            Position.GOALKEEPER: 1,
                            Position.DEFENDER: 2,
                            Position.MIDFIELDER: 3,
                            Position.FORWARD: 4,
                        }[p.position],
                    }
                    for p in players().values()
                ],
            }
        if path.startswith("leagues-classic/"):
            return {"league": {"name": "Friends"}, "standings": {"results": [{"entry": 1}]}}
        if path.startswith("entry/"):
            return {
                "entry_history": {"points": 70, "event_transfers_cost": 4},
                "active_chip": "bboost",
                "picks": [
                    {
                        "element": p.player_id,
                        "position": p.squad_position,
                        "multiplier": 1,
                        "is_captain": p.is_captain,
                        "is_vice_captain": p.is_vice_captain,
                    }
                    for p in manager().picks
                ],
            }
        if path.startswith("fixtures/"):
            return [{"id": 1, "team_h": 1, "team_a": 2, "started": True, "finished": True}]
        return {
            "elements": [
                {"id": p.id, "stats": {"total_points": 1, "minutes": 90}}
                for p in players().values()
            ]
        }


class HistoricalTests(unittest.TestCase):
    def test_final_results_use_official_score_less_hits_and_no_scenarios(self) -> None:
        snapshot = HistoricalClient().fetch_snapshot(123, gameweek=1)
        state = state_from_snapshot(snapshot)
        self.assertEqual(current_standings(state)[0].effective_score, 66)
        result = analysis_to_dict(analyse_state(state))
        self.assertTrue(result["league"]["historical"])
        self.assertEqual(result["league"]["current_gameweek"], 2)
        self.assertEqual(result["managers"][0]["scenarios"], [])
        self.assertEqual(result["managers"][0]["squad"][0]["scoring_multiplier"], 1)
        self.assertEqual([gw["id"] for gw in result["league"]["available_gameweeks"]], [1, 2])

    def test_future_gameweek_cannot_be_reconstructed(self) -> None:
        with self.assertRaises(FPLGameweekUnavailable):
            HistoricalClient().fetch_snapshot(123, gameweek=3)

    def test_missing_historical_squad_is_not_silently_dropped(self) -> None:
        client = HistoricalClient()
        original = client._get

        def missing(path: str) -> object:
            if path.startswith("entry/"):
                raise FPLNotFoundError("Not found")
            return original(path)

        with (
            patch.object(client, "_get", side_effect=missing),
            self.assertRaises(FPLGameweekUnavailable),
        ):
            client.fetch_snapshot(123, gameweek=1)

    def test_cache_separates_current_and_historical_results(self) -> None:
        service = AnalysisService(HistoricalClient())
        current = service.analyse_league(123, include_scenarios=False)
        historical = service.analyse_league(123, gameweek=1, include_scenarios=False)
        self.assertEqual(current["league"]["gameweek"], 2)
        self.assertEqual(historical["league"]["gameweek"], 1)
        self.assertTrue(
            service.analyse_league(123, gameweek=1, include_scenarios=False)["cache"]["hit"]
        )

    def test_derived_cache_cannot_return_an_older_snapshot_generation(self) -> None:
        service = AnalysisService(HistoricalClient())
        first = service.analyse_league(123, include_scenarios=False)
        service._snapshots.clear()
        refreshed = service.analyse_league(123, include_scenarios=False)
        self.assertNotEqual(first["league"]["fetched_at"], refreshed["league"]["fetched_at"])
        self.assertFalse(refreshed["cache"]["hit"])

    def test_strict_tie_completed_result_matches_safety(self) -> None:
        state = demo_state("complete")
        state.raw["historical"] = True
        state.managers = tuple(
            replace(m, official_points=60, transfer_cost=0) for m in state.managers
        )
        result = analysis_to_dict(analyse_state(state, AnalysisSettings(allow_tied_last=False)))
        self.assertEqual(result["summary"]["current_last_entry_ids"], [])
        self.assertTrue(all(m["status"] == "safe" for m in result["managers"]))


class ProductAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(AnalysisService(HistoricalClient())))

    def test_history_validation_and_unavailable_data(self) -> None:
        self.assertEqual(self.client.get("/api/league/123?gameweek=0").status_code, 422)
        self.assertEqual(self.client.get("/api/league/123?gameweek=3").status_code, 409)
        response = self.client.get("/api/league/123?gameweek=1&detail=summary")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["league"]["historical"])

    def test_manager_only_scenario_response_and_pairwise_comparison(self) -> None:
        response = self.client.get("/api/league/900001/managers/1/scenarios?demo=late&scenarios=6")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manager"]["entry_id"], 1)
        self.assertNotIn("managers", response.json())
        compared = self.client.get("/api/league/900001/compare?demo=late&a=1&b=2&ties=strict")
        self.assertEqual(compared.status_code, 200)
        self.assertFalse(compared.json()["ties_count_as_last"])
        self.assertEqual(self.client.get("/api/league/123/compare?a=1&b=1").status_code, 400)


if __name__ == "__main__":
    unittest.main()
