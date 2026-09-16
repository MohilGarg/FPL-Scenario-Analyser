from __future__ import annotations

import copy
import json
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from fpl_forfeit.analytics.calculations import (
    current_squads,
    enrich_season,
    head_to_head,
    scoring_detail,
    season_summary,
)
from fpl_forfeit.analytics.service import AnalyticsService
from fpl_forfeit.api import FPLClient, FPLNotFoundError
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.web import create_app


def season_fixture() -> tuple[list[dict], dict[int, dict]]:
    members = [{"entry_id": i, "name": f"Manager {i}", "team": f"Team {i}"} for i in range(1, 5)]
    scores = {1: [40, 60, 20], 2: [40, 50, 40], 3: [50, 40, 60], 4: [60, 70, 80]}
    histories = {
        entry: {
            "current": [
                {
                    "event": gw,
                    "points": score + (4 if entry == 1 else 0),
                    "event_transfers_cost": 4 if entry == 1 else 0,
                    "event_transfers": 2,
                    "points_on_bench": 99,
                }
                for gw, score in enumerate(values, 1)
            ],
            "chips": [],
        }
        for entry, values in scores.items()
    }
    histories[1]["chips"] = [{"name": "3xc", "event": 1}, {"name": "3xc", "event": 3}]
    return members, histories


def squad_fixture(chip: str | None = None, *, autosub: bool = False) -> tuple[dict, dict, dict]:
    players = {
        i: {
            "id": i,
            "web_name": f"Player {i}",
            "element_type": 1
            if i in (1, 12)
            else 2
            if i in (2, 3, 4, 5, 13)
            else 3
            if i in (6, 7, 8, 9, 14)
            else 4,
        }
        for i in range(1, 16)
    }
    selection = [
        {
            "element": i,
            "position": i,
            "element_type": players[i]["element_type"],
            "multiplier": (3 if chip == "3xc" else 2)
            if i == 6
            else 1
            if i <= 11 or chip == "bboost"
            else 0,
            "is_captain": i == 6,
            "is_vice_captain": i == 7,
        }
        for i in players
    ]
    points = {i: 8 if i == 6 else 2 for i in players}
    if autosub:
        selection[1]["multiplier"] = 0
        selection[12]["multiplier"] = 1
        points[2] = 0
    picks = {
        "picks": selection,
        "active_chip": chip,
        "entry_history": {"points": sum(points[p["element"]] * p["multiplier"] for p in selection)},
        "automatic_subs": [{"element_in": 13, "element_out": 2}] if autosub else [],
    }
    live = {"elements": [{"id": i, "stats": {"total_points": points[i]}} for i in players]}
    return picks, live, players


class AnalyticsCalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.members, self.histories = season_fixture()
        self.summary = season_summary(self.members, self.histories, [1, 2, 3])

    def test_weekly_ranks_ties_last_and_bottom_three(self) -> None:
        week = self.summary["gameweeks"][0]
        self.assertEqual([r["rank"] for r in week["rows"]], [1, 2, 3, 3])
        self.assertEqual(week["last_names"], ["Manager 1", "Manager 2"])
        self.assertEqual(week["margin"], 0)
        self.assertEqual([m["times_last"] for m in self.summary["managers"]], [2, 1, 1, 0])
        self.assertEqual([m["bottom_three"] for m in self.summary["managers"]], [3, 3, 3, 0])

    def test_cutoff_ties_include_more_than_three(self) -> None:
        self.histories[4]["current"][0]["points"] = 50
        summary = season_summary(self.members, self.histories, [1])
        self.assertTrue(all(r["bottom_three"] for r in summary["gameweeks"][0]["rows"]))

    def test_scores_average_median_and_population_deviation(self) -> None:
        manager = self.summary["managers"][0]
        self.assertEqual(manager["average"], 40)
        self.assertEqual(manager["median"], 40)
        self.assertEqual(manager["standard_deviation"], 16.33)
        self.assertEqual(manager["total_points"], 120)
        self.assertEqual(manager["history"][-1]["cumulative"], 120)

    def test_hits_subtracted_once_and_transfer_breakdown(self) -> None:
        m = self.summary["managers"][0]
        self.assertEqual(m["history"][0]["raw"], 44)
        self.assertEqual(m["history"][0]["score"], 40)
        self.assertEqual(m["hits"]["total"], 12)
        self.assertEqual(m["hits"]["four"], 3)
        self.assertEqual(m["hits"]["transfers"], 6)

    def test_missing_history_never_creates_rank_or_false_last(self) -> None:
        self.histories[1]["current"].pop()
        summary = season_summary(self.members, self.histories, [1, 2, 3])
        self.assertEqual(summary["coverage"]["scores"], 11)
        self.assertEqual(summary["coverage"]["ranked_gameweeks"], 2)
        self.assertIsNone(summary["gameweeks"][-1]["bottom"])
        self.assertTrue(all(r["last"] is None for r in summary["gameweeks"][-1]["rows"]))

    def test_missing_transfer_cost_is_not_assumed_zero(self) -> None:
        del self.histories[1]["current"][0]["event_transfers_cost"]
        summary = season_summary(self.members, self.histories, [1])
        self.assertEqual(summary["coverage"]["scores"], 3)

    def test_no_completed_weeks_have_null_not_fabricated_scores(self) -> None:
        summary = season_summary(self.members, self.histories, [])
        self.assertIsNone(summary["totals"]["average"])
        self.assertIsNone(summary["managers"][0]["times_last"])
        self.assertIsNone(summary["managers"][0]["total_points"])

    def test_repeated_chip_history_is_preserved(self) -> None:
        self.assertEqual([c["event"] for c in self.summary["managers"][0]["chips"]], [1, 3])

    def test_unused_bench_excludes_autosubbed_player(self) -> None:
        result = scoring_detail(*squad_fixture(autosub=True))
        self.assertEqual(result["unused_bench"], 6)
        self.assertEqual(result["players"][12]["points"], 2)
        self.assertEqual(result["players"][1]["points"], 0)

    def test_bench_boost_counts_bench_and_never_unused(self) -> None:
        result = scoring_detail(*squad_fixture("bboost"))
        self.assertEqual(result["unused_bench"], 0)
        self.assertEqual(result["bench_boost"], 8)

    def test_uncounted_starting_slot_is_not_assumed_to_be_bench(self) -> None:
        picks, live, players = squad_fixture()
        picks["picks"][1]["multiplier"] = 0
        picks["entry_history"]["points"] -= 2
        live["elements"][1]["stats"]["total_points"] = -3
        result = scoring_detail(picks, live, players)
        self.assertEqual(result["unused_bench"], 8)

    def test_captain_total_and_additional_are_distinct(self) -> None:
        result = scoring_detail(*squad_fixture())
        self.assertEqual(result["captain_points"], 16)
        self.assertEqual(result["captain_additional"], 8)

    def test_triple_captain_counts_two_extra_multipliers(self) -> None:
        result = scoring_detail(*squad_fixture("3xc"))
        self.assertEqual(result["captain_points"], 24)
        self.assertEqual(result["captain_additional"], 16)
        self.assertEqual(result["triple_captain_additional"], 16)

    def test_vice_takeover_credits_effective_captain(self) -> None:
        picks, live, players = squad_fixture()
        picks["picks"][5]["multiplier"] = 0
        picks["picks"][6]["multiplier"] = 2
        live["elements"][5]["stats"]["total_points"] = 0
        picks["entry_history"]["points"] -= 14
        result = scoring_detail(picks, live, players)
        self.assertEqual(result["captain_chosen"], 6)
        self.assertEqual(result["effective_captain"], 7)
        self.assertEqual(result["captain_points"], 4)
        self.assertEqual(result["captain_additional"], 2)

    def test_positional_points_reconcile_and_dgw_total_counted_once(self) -> None:
        result = scoring_detail(*squad_fixture("3xc"))
        self.assertEqual(result["positions"]["MID"], 30)
        self.assertEqual(sum(result["positions"].values()), result["raw"])
        self.assertEqual(sum(p["points"] for p in result["players"]), result["raw"])

    def test_unreconciled_or_missing_points_cannot_produce_details(self) -> None:
        picks, live, players = squad_fixture()
        picks["entry_history"]["points"] += 1
        with self.assertRaisesRegex(ValueError, "reconcile"):
            scoring_detail(picks, live, players)
        live["elements"].pop()
        with self.assertRaisesRegex(ValueError, "missing"):
            scoring_detail(picks, live, players)

    def test_enrichment_propagates_to_managers_and_league_contribution(self) -> None:
        detail = scoring_detail(*squad_fixture("3xc"))
        result = enrich_season(self.summary, {(1, 1): detail, (2, 1): detail})
        self.assertEqual(result["managers"][0]["captaincy"]["additional"], 16)
        self.assertEqual(result["managers"][0]["detail_coverage"], 1)
        self.assertEqual(result["managers"][0]["chips"][0]["contribution"], 16)
        player = next(p for p in result["contributions"] if p["id"] == 6)
        self.assertEqual(player["points"], 48)
        self.assertEqual(player["scoring_appearances"], 2)
        self.assertEqual(player["additional"], 32)
        self.assertIsNone(result["managers"][2]["bench"]["total"])

    def test_current_ownership_effective_and_similarity(self) -> None:
        picks, _, players = squad_fixture("3xc")
        result = current_squads(self.members[:2], {1: picks, 2: picks}, players)
        captain = next(p for p in result["players"] if p["id"] == 6)
        self.assertEqual(captain["owned"], 2)
        self.assertEqual(captain["effective_pct"], 300)
        self.assertEqual(result["pairs"][0]["shared_count"], 15)
        self.assertEqual(result["pairs"][0]["only_a"], [])
        self.assertEqual(result["pairs"][0]["percentage"], 100)

    def test_head_to_head_uses_common_weeks(self) -> None:
        result = head_to_head(self.summary, 1, 2)
        self.assertEqual((result["a_wins"], result["b_wins"], result["ties"]), (1, 1, 1))
        self.assertEqual(result["largest_a_win"], 10)
        self.assertEqual(result["largest_b_win"], 20)
        self.assertEqual(result["a_common_points"], 120)
        self.assertEqual(result["average_difference"], -3.33)

    def test_ownership_and_captain_trends_keep_partial_coverage(self) -> None:
        detail = scoring_detail(*squad_fixture())
        result = enrich_season(self.summary, {(1, 1): detail, (2, 1): detail})
        self.assertEqual(result["ownership_history"][0]["coverage"], 2)
        self.assertEqual(result["ownership_history"][0]["most_owned_count"], 2)
        self.assertEqual(result["captaincy"]["players"][0]["chosen"], 2)
        self.assertEqual(result["ownership_history"][1]["coverage"], 0)
        self.assertIsNone(result["captaincy"]["gameweeks"][1]["unique_choices"])

    def test_missing_captain_metadata_cannot_be_assumed(self) -> None:
        picks, live, players = squad_fixture()
        picks["picks"][5]["is_captain"] = False
        with self.assertRaisesRegex(ValueError, "captain"):
            scoring_detail(picks, live, players)


class HistoryClient(FPLClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter = Counter()
        self.members, self.histories = season_fixture()
        self.picks, self.live, self.players = squad_fixture()
        # Make the score source agree with the independently reconciled picks fixture.
        for history in self.histories.values():
            for row in history["current"]:
                row["points"] = self.picks["entry_history"]["points"]

    def _get(self, path: str) -> dict:
        self.calls[path] += 1
        if path == "bootstrap-static/":
            return {
                "events": [
                    {
                        "id": gw,
                        "finished": True,
                        "is_current": gw == 3,
                        "deadline_time": "2026-08-01T10:00:00Z",
                    }
                    for gw in (1, 2, 3)
                ],
                "elements": list(self.players.values()),
            }
        if path.startswith("leagues-classic/404/"):
            raise FPLNotFoundError("League not found")
        if path.startswith("leagues-classic/"):
            return {
                "league": {"name": "Test league"},
                "standings": {
                    "has_next": False,
                    "results": [
                        {"entry": m["entry_id"], "player_name": m["name"], "entry_name": m["team"]}
                        for m in self.members
                    ],
                },
            }
        if path.endswith("/history/"):
            return copy.deepcopy(self.histories[int(path.split("/")[1])])
        if path.endswith("/picks/"):
            return copy.deepcopy(self.picks)
        if path.endswith("/live/"):
            return copy.deepcopy(self.live)
        raise AssertionError(path)


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = HistoryClient()
        self.service = AnalyticsService(AnalysisService(client=self.client))
        self.web = TestClient(create_app(analytics=self.service))

    def test_summary_cache_and_frontend_contract(self) -> None:
        response = self.web.get("/api/league/7/analytics/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["league"]["manager_count"], 4)
        self.assertEqual(payload["coverage"]["scores"], 12)
        self.assertEqual(payload["totals"]["chips"], 2)
        self.assertNotIn("history", payload["managers"][0])
        self.assertNotIn("rows", payload["gameweeks"][0])
        self.assertFalse(any("/picks/" in key for key in self.client.calls))
        self.web.get("/api/league/7/analytics/gameweeks?gameweek=2")
        self.assertTrue(all(count == 1 for count in self.client.calls.values()))
        json.dumps(payload, allow_nan=False)

    def test_concurrent_requests_share_upstream_history(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.service.summary, [7] * 4))
        self.assertEqual(len(results), 4)
        self.assertTrue(all(count == 1 for count in self.client.calls.values()))

    def test_invalid_leagues_and_managers(self) -> None:
        self.assertEqual(self.web.get("/api/league/0/analytics/summary").status_code, 400)
        self.assertEqual(self.web.get("/api/league/no/analytics/summary").status_code, 422)
        self.assertEqual(self.web.get("/api/league/404/analytics/summary").status_code, 404)
        self.assertEqual(self.web.get("/api/league/7/analytics/manager/999").status_code, 404)
        self.assertEqual(
            self.web.get("/api/league/7/analytics/head-to-head?a=1&b=1").status_code, 400
        )

    def test_historical_serialisation_and_partial_coverage(self) -> None:
        self.client.histories[1]["current"].pop()
        response = self.web.get("/api/league/7/analytics/gameweeks?gameweek=3")
        week = response.json()["gameweeks"][0]
        self.assertFalse(week["complete"])
        self.assertIsNone(week["bottom"])
        self.assertIsNone(week["rows"][0]["detail"])
        response = self.web.get("/api/league/7/analytics/head-to-head?a=1&b=2")
        self.assertEqual(response.json()["common_gameweeks"], 2)

    def test_background_job_progress_and_resource_reuse(self) -> None:
        self.service.detail_status(7)
        finished = Event()
        # Synchronise on the worker's completion via its bounded cache, not fixed sleeps.
        for _ in range(100):
            status = self.service.detail_status(7)
            if status["status"] == "ready":
                break
            finished.wait(0.01)
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["coverage"]["available"], 12)
        response = self.web.get("/api/league/7/analytics/manager/1")
        self.assertEqual(response.json()["managers"][0]["detail_coverage"], 3)
        self.assertTrue(all(count == 1 for count in self.client.calls.values()))
        json.dumps(response.json(), allow_nan=False)
        timeline = self.web.get("/api/league/7/analytics/player/6")
        self.assertEqual(timeline.status_code, 200)
        self.assertEqual(timeline.json()["history"][0]["owners"], 4)
        self.assertEqual(timeline.json()["history"][0]["captains"], 4)

    def test_cached_enrichment_does_not_freeze_current_gameweek(self) -> None:
        summary = self.service.summary(7)
        key = self.service._key(summary)
        self.service._results.set(key, enrich_season(copy.deepcopy(summary), {}))
        summary["league"]["current_gameweek"] = 4
        summary["league"]["current_status"] = "In progress"
        self.service._summaries.set(7, summary)
        self.assertEqual(self.service.section(7, "summary")["league"]["current_gameweek"], 4)


if __name__ == "__main__":
    unittest.main()
