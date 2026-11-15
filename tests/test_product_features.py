from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from fpl_forfeit.api import FPLClient, FPLGameweekUnavailable, FPLNotFoundError, state_from_snapshot
from fpl_forfeit.models import ElementScore, Fixture, Position
from fpl_forfeit.safety import conservative_score_bounds
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.settings import AnalysisSettings
from fpl_forfeit.state import current_standings
from fpl_forfeit.web import create_app
from tests.helpers import league_state, manager, players, scores


class PendingAutosubSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(1, 1, 1, 99, "Team", "Opponent", False, False)

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
    def test_final_results_use_official_score_less_hits(self) -> None:
        snapshot = HistoricalClient().fetch_snapshot(123, gameweek=1)
        state = state_from_snapshot(snapshot)
        self.assertEqual(current_standings(state)[0].effective_score, 66)
        result = analysis_to_dict(analyse_state(state))
        self.assertTrue(result["league"]["historical"])
        self.assertEqual(result["league"]["current_gameweek"], 2)
        self.assertEqual(result["league"]["status"], "complete")
        self.assertIn(result["managers"][0]["status"], {"last", "clear"})
        self.assertEqual(result["differentials"], [])
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
        current = service.analyse_league(123)
        historical = service.analyse_league(123, gameweek=1)
        self.assertEqual(current["league"]["gameweek"], 2)
        self.assertEqual(historical["league"]["gameweek"], 1)
        self.assertTrue(service.analyse_league(123, gameweek=1)["cache"]["hit"])

    def test_derived_cache_cannot_return_an_older_snapshot_generation(self) -> None:
        service = AnalysisService(HistoricalClient())
        first = service.analyse_league(123)
        service._snapshots.clear()
        refreshed = service.analyse_league(123)
        self.assertNotEqual(first["league"]["fetched_at"], refreshed["league"]["fetched_at"])
        self.assertFalse(refreshed["cache"]["hit"])

    def test_strict_tie_completed_result_matches_safety(self) -> None:
        state = league_state((manager(1), manager(2)), scores(2, 90))
        state.raw["historical"] = True
        state.raw["bootstrap"] = {"events": [{"id": state.gameweek, "finished": True}]}
        result = analysis_to_dict(analyse_state(state, AnalysisSettings(allow_tied_last=False)))
        self.assertEqual(result["league"]["status"], "complete")
        self.assertEqual(result["summary"]["current_last_entry_ids"], [])
        self.assertTrue(all(m["status"] == "clear" for m in result["managers"]))
        tied = analysis_to_dict(analyse_state(state, AnalysisSettings(allow_tied_last=True)))
        self.assertEqual(sorted(tied["summary"]["current_last_entry_ids"]), [1, 2])
        self.assertTrue(all(m["status"] == "last" for m in tied["managers"]))


class ProductAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(AnalysisService(HistoricalClient())))

    def test_history_validation_and_unavailable_data(self) -> None:
        self.assertEqual(self.client.get("/api/league/123?gameweek=0").status_code, 422)
        self.assertEqual(self.client.get("/api/league/123?gameweek=3").status_code, 409)
        response = self.client.get("/api/league/123?gameweek=1&detail=summary")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["league"]["historical"])


if __name__ == "__main__":
    unittest.main()
