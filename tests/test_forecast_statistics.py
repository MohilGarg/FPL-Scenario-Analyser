from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

from fpl_forfeit.api import FPLClient, parse_appearance_history, state_from_snapshot
from fpl_forfeit.forecasting.models import BENCHMARK_MODEL_VERSION
from fpl_forfeit.forecasting.statistics import benchmark_player_rates, player_rates
from fpl_forfeit.models import MatchAppearance
from tests.simulator_helpers import fixture_state


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

    def test_client_history_requests_are_coalesced(self):
        client = FPLClient()
        with (
            patch.object(client, "_get", return_value={"history": []}) as fetch,
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            list(pool.map(client.player_history, [12] * 4))
        self.assertEqual(fetch.call_count, 1)


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


if __name__ == "__main__":
    unittest.main()
