from __future__ import annotations

import random
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from fpl_forfeit.analysis import analyse_state, analysis_to_dict
from fpl_forfeit.api import FPLGameweekUnavailable
from fpl_forfeit.demo import demo_state
from fpl_forfeit.evaluation import evaluate, prediction_record
from fpl_forfeit.forecast import Condition, FixtureModel, ScenarioConflict, outlook
from fpl_forfeit.models import ElementScore, Fixture, Position
from fpl_forfeit.safety import conservative_score_bounds
from fpl_forfeit.scenarios import ScenarioVariable, football_consistent, outcome_catalog
from fpl_forfeit.scoring import event_points, final_manager_scores
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.solver import solve_candidate
from fpl_forfeit.state import current_standings
from fpl_forfeit.substitutions import effective_multipliers
from fpl_forfeit.web import create_app
from tests.helpers import league_state, manager, scores


def fixture_state(count: int = 5):
    state = league_state(
        (
            manager(1, captain=12),
            manager(2, captain=13),
            manager(3, captain=14, chip="3xc", transfer_cost=4),
        ),
        scores(0, 0),
    )
    for pid, p in state.players.items():
        state.players[pid] = replace(
            p,
            team_id=(pid - 1) % (2 * count) + 1,
            season_minutes=540,
            season_starts=6,
            season_team_matches=6,
            season_goals=3 if pid == 12 else 0,
            season_assists=1,
            season_defensive_contribution=40,
            availability_status="a",
        )
    state.fixtures = tuple(
        Fixture(i, 1, 2 * i - 1, 2 * i, f"Home {i}", f"Away {i}", False, False)
        for i in range(1, count + 1)
    )
    state.team_goal_rates = {i: (1.5, 1.3) for i in range(1, 2 * count + 1)}
    return state


class OfficialEventScoringTests(unittest.TestCase):
    def test_zero_minute_captain_card_bounds_allow_scored_vice_handover(self):
        state = league_state(
            (manager(1, captain=12, vice=7),),
            scores(1, 90),
            (Fixture(1, 1, 12, 99, "Captain team", "Opponent", False, False),),
        )
        state.live_scores[12] = ElementScore(-1, 0, carded=True)
        state.live_scores[7] = ElementScore(5, 90)
        current = {r.manager.entry_id: r.effective_score for r in current_standings(state)}
        bounds = conservative_score_bounds(
            state, current, min_remaining_player_contribution=0, max_remaining_player_contribution=0
        )
        final = final_manager_scores(state, state.live_scores)
        self.assertGreater(final[1], current[1])
        self.assertGreaterEqual(bounds[1].upper, final[1])

    def test_zero_minute_card_blocks_autosub_but_captain_uses_minutes(self):
        state = fixture_state(1)
        state.managers = (manager(1, captain=12, vice=7),)
        values = scores(2, 90)
        values[12] = ElementScore(-1, 0, carded=True)
        multipliers = effective_multipliers(
            state.managers[0], state.players, values, {1: True, 2: True}
        )
        self.assertEqual(multipliers[12], 1)
        self.assertEqual(multipliers[7], 2)
        self.assertEqual(multipliers[11], 0)

    def test_position_goals_and_appearance(self):
        for position, goal in (
            (Position.GOALKEEPER, 10),
            (Position.DEFENDER, 6),
            (Position.MIDFIELDER, 5),
            (Position.FORWARD, 4),
        ):
            self.assertEqual(event_points(position, {"minutes": 90, "goals_scored": 1}), 2 + goal)
            self.assertEqual(event_points(position, {"minutes": 59}), 1)
            self.assertEqual(event_points(position, {"minutes": 0}), 0)

    def test_all_other_scoring_categories(self):
        stats = {
            "minutes": 90,
            "clean_sheets": 1,
            "saves": 6,
            "penalties_saved": 1,
            "penalties_missed": 1,
            "yellow_cards": 1,
            "red_cards": 1,
            "own_goals": 1,
            "bonus": 3,
            "goals_conceded": 0,
        }
        self.assertEqual(event_points(Position.GOALKEEPER, stats), 8)
        self.assertEqual(event_points(Position.DEFENDER, {"minutes": 90, "goals_conceded": 3}), 1)
        self.assertEqual(event_points(Position.DEFENDER, {"minutes": 59, "clean_sheets": 1}), 1)

    def test_defensive_contribution_threshold_is_per_fixture_and_capped(self):
        for position, threshold in (
            (Position.DEFENDER, 10),
            (Position.MIDFIELDER, 12),
            (Position.FORWARD, 12),
        ):
            self.assertEqual(
                event_points(position, {"minutes": 90, "defensive_contribution": threshold - 1}), 2
            )
            self.assertEqual(
                event_points(position, {"minutes": 90, "defensive_contribution": threshold}), 4
            )
            self.assertEqual(
                event_points(position, {"minutes": 90, "defensive_contribution": 2 * threshold}), 4
            )
        self.assertEqual(
            event_points(Position.GOALKEEPER, {"minutes": 90, "defensive_contribution": 20}), 2
        )

    def test_goal_points_monotone(self):
        for position in Position:
            values = [event_points(position, {"minutes": 90, "goals_scored": g}) for g in range(8)]
            self.assertEqual(values, sorted(values))


class FixtureInvariantTests(unittest.TestCase):
    def test_zero_minute_red_card_cannot_acquire_future_minutes(self):
        state = fixture_state(1)
        state.fixtures = (
            replace(
                state.fixtures[0], started=True, elapsed_minutes=75, home_score=0, away_score=0
            ),
        )
        state.fixture_stats[(12, 1)] = {"minutes": 0, "red_cards": 1}
        state.live_scores[12] = ElementScore(-3, 0, carded=True)
        state.fixture_minutes[(12, 1)] = 0
        for seed in range(10):
            elements, _ = FixtureModel(state).draw(random.Random(seed))
            self.assertEqual(elements[12], ElementScore(-3, 0, carded=True))

    def test_unrelated_completed_players_and_fixture_remain_unchanged(self):
        state = fixture_state(5)
        finished = replace(
            state.fixtures[0], finished=True, started=True, home_score=2, away_score=1
        )
        state.fixtures = (finished,) + state.fixtures[1:]
        fixed = {pid for pid, p in state.players.items() if finished.involves(p.team_id)}
        for pid in fixed:
            state.live_scores[pid] = ElementScore(7, 90)
        model = FixtureModel(state, (Condition(2, "score", home_goals=3, away_goals=1),))
        for seed in range(10):
            elements, samples = model.draw(random.Random(seed))
            self.assertTrue(all(elements[pid] == ElementScore(7, 90) for pid in fixed))
            self.assertNotIn(finished.id, {s.fixture_id for s in samples})
            self.assertEqual((finished.home_score, finished.away_score), (2, 1))

    def test_events_have_shared_scores_minutes_and_distinct_assists(self):
        state = fixture_state()
        model = FixtureModel(state)
        for seed in range(35):
            elements, fixtures = model.draw(random.Random(seed))
            for sample in fixtures:
                fixture = model.by_fixture[sample.fixture_id]
                self.assertEqual(
                    sum(e[0] == fixture.home_team_id for e in sample.events), sample.home_goals
                )
                self.assertEqual(
                    sum(e[0] == fixture.away_team_id for e in sample.events), sample.away_goals
                )
                for team, scorer, assister, minute in sample.events:
                    self.assertFalse(scorer is not None and scorer == assister)
                    for pid in (scorer, assister):
                        if pid is not None:
                            self.assertEqual(state.players[pid].team_id, team)
                            a, b = sample.windows[pid]
                            self.assertLessEqual(a, minute)
                            self.assertLess(minute, b)
                for pid, stats in sample.stats.items():
                    if stats["minutes"] == 0:
                        self.assertEqual(event_points(state.players[pid].position, stats), 0)
                    if stats.get("clean_sheets"):
                        self.assertGreaterEqual(stats["minutes"], 60)
                        self.assertEqual(stats["goals_conceded"], 0)
                for team in (fixture.home_team_id, fixture.away_team_id):
                    for minute in range(90):
                        active = [
                            pid
                            for pid, (a, b) in sample.windows.items()
                            if state.players[pid].team_id == team and a <= minute < b
                        ]
                        self.assertLessEqual(len(active), 11)
                        self.assertLessEqual(
                            sum(
                                state.players[pid].position == Position.GOALKEEPER for pid in active
                            ),
                            1,
                        )
            final = final_manager_scores(state, elements)
            for m in state.managers:
                multipliers = effective_multipliers(
                    m, state.players, elements, {p.team_id: True for p in state.players.values()}
                )
                self.assertEqual(
                    final[m.entry_id],
                    sum(elements[pid].points * mult for pid, mult in multipliers.items())
                    - m.transfer_cost,
                )
                self.assertEqual(sum(v > 0 for v in multipliers.values()), 11)

    def test_completed_dgw_points_never_change(self):
        state = fixture_state(1)
        state.fixtures = (
            replace(state.fixtures[0], finished=True, started=True, home_score=2, away_score=1),
            replace(state.fixtures[0], id=2),
        )
        state.live_scores = {pid: ElementScore(7, 90) for pid in state.players}
        model = FixtureModel(state, (Condition(2, "dnp", player_id=12),))
        for seed in range(15):
            elements, fixtures = model.draw(random.Random(seed))
            self.assertEqual(elements[12], ElementScore(7, 90))
            self.assertEqual([f.fixture_id for f in fixtures], [2])

    def test_live_awarded_events_are_preserved_and_no_sixty_minute_cameo(self):
        state = fixture_state(1)
        state.fixtures = (
            replace(
                state.fixtures[0], started=True, elapsed_minutes=75, home_score=1, away_score=0
            ),
        )
        for pid in state.players:
            state.fixture_minutes[(pid, 1)] = 0
            state.fixture_stats[(pid, 1)] = {"minutes": 0}
        state.fixture_stats[(1, 1)] = {"minutes": 75, "goals_scored": 1, "clean_sheets": 1}
        state.fixture_minutes[(1, 1)] = 75
        state.live_scores[1] = ElementScore(16, 75)
        model = FixtureModel(state)
        for seed in range(20):
            _elements, samples = model.draw(random.Random(seed))
            sample = samples[0]
            self.assertGreaterEqual(sample.home_goals, 1)
            self.assertGreaterEqual(sample.stats[1]["goals_scored"], 1)
            for pid, stats in sample.stats.items():
                if pid != 1:
                    self.assertLessEqual(stats["minutes"], 15)
        with self.assertRaises(ScenarioConflict):
            FixtureModel(state, (Condition(1, "minutes", player_id=12, value=60),))

    def test_subbed_clean_sheet_survives_later_goal_and_red_does_not(self):
        state = fixture_state(1)
        pid = 2
        state.fixtures = (
            replace(
                state.fixtures[0], started=True, elapsed_minutes=75, home_score=0, away_score=0
            ),
        )
        state.fixture_stats[(pid, 1)] = {"minutes": 60, "clean_sheets": 1}
        state.fixture_minutes[(pid, 1)] = 60
        state.live_scores[pid] = ElementScore(6, 60)
        condition = Condition(1, "score", home_goals=1, away_goals=0)
        _elements, samples = FixtureModel(state, (condition,)).draw(random.Random(3))
        self.assertEqual(samples[0].stats[pid]["clean_sheets"], 1)
        state.fixture_stats[(pid, 1)]["red_cards"] = 1
        state.live_scores[pid] = ElementScore(3, 60)
        _elements, samples = FixtureModel(state, (condition,)).draw(random.Random(3))
        self.assertEqual(samples[0].stats[pid]["clean_sheets"], 0)
        self.assertEqual(samples[0].stats[pid]["goals_conceded"], 1)


class WhatIfTests(unittest.TestCase):
    def test_goal_changes_relative_scores_and_forfeit_risk(self):
        state = fixture_state(5)
        fixture = next(f.id for f in state.fixtures if f.involves(state.players[12].team_id))
        minutes = Condition(fixture, "minutes", player_id=12, value=90)
        goal = outlook(
            state, samples=400, conditions=(minutes, Condition(fixture, "goal", player_id=12))
        )
        blank = outlook(
            state, samples=400, conditions=(minutes, Condition(fixture, "blank", player_id=12))
        )
        a = {r["entry_id"]: r for r in goal["managers"]}
        b = {r["entry_id"]: r for r in blank["managers"]}
        self.assertGreater(
            a[1]["projected_score"] - a[2]["projected_score"],
            b[1]["projected_score"] - b[2]["projected_score"] + 3,
        )
        self.assertLess(a[1]["last_probability"], b[1]["last_probability"])

    def test_substitute_window_is_late_and_prevents_sixty_minute_points(self):
        state = fixture_state(1)
        model = FixtureModel(state, (Condition(1, "substitute", player_id=12, value=20),))
        _, fixtures = model.draw(random.Random(2))
        self.assertEqual(fixtures[0].windows[12], (70, 90))
        self.assertEqual(fixtures[0].stats[12]["minutes"], 20)

    def test_goal_assist_blank_clean_sheet_and_score_all_apply(self):
        state = fixture_state(1)
        for condition in (
            Condition(1, "goal", player_id=12),
            Condition(1, "assist", player_id=12),
            Condition(1, "blank", player_id=12),
            Condition(1, "clean_sheet", team_id=1),
            Condition(1, "score", home_goals=2, away_goals=1),
        ):
            model = FixtureModel(state, (condition,))
            for seed in range(8):
                _, fixtures = model.draw(random.Random(seed))
                f = fixtures[0]
                if condition.kind == "goal":
                    self.assertEqual(f.stats[12]["goals_scored"], 1)
                if condition.kind == "assist":
                    self.assertEqual(f.stats[12]["assists"], 1)
                if condition.kind == "blank":
                    self.assertEqual(f.stats[12]["goals_scored"], 0)
                    self.assertEqual(f.stats[12]["assists"], 0)
                if condition.kind == "clean_sheet":
                    self.assertEqual(f.away_goals, 0)
                if condition.kind == "score":
                    self.assertEqual((f.home_goals, f.away_goals), (2, 1))

    def test_contradictory_events_are_rejected(self):
        state = fixture_state(1)
        for conditions in (
            (Condition(1, "blank", player_id=12), Condition(1, "goal", player_id=12)),
            (Condition(1, "dnp", player_id=12), Condition(1, "assist", player_id=12)),
            (Condition(1, "clean_sheet", team_id=1), Condition(1, "goal", player_id=12)),
            (Condition(1, "score", home_goals=0, away_goals=0), Condition(1, "goal", player_id=12)),
            (Condition(1, "clean_sheet", team_id=1), Condition(1, "lose_clean_sheet", team_id=1)),
            (Condition(1, "goal", player_id=12), Condition(1, "goal", player_id=12)),
        ):
            with self.subTest(conditions=conditions), self.assertRaises(ScenarioConflict):
                FixtureModel(state, conditions)
        state.fixtures = (replace(state.fixtures[0], finished=True),)
        with self.assertRaises(ScenarioConflict):
            FixtureModel(state, (Condition(1, "goal", player_id=12),))

    def test_goal_and_assist_from_same_player_need_two_team_goals(self):
        state = fixture_state(1)
        conditions = (Condition(1, "goal", player_id=12), Condition(1, "assist", player_id=12))
        _, fixtures = FixtureModel(state, conditions).draw(random.Random(1))
        self.assertGreaterEqual(fixtures[0].away_goals, 2)
        with self.assertRaises(ScenarioConflict):
            FixtureModel(
                state, conditions + (Condition(1, "score", home_goals=0, away_goals=1),)
            ).draw(random.Random(1))

    def test_absence_recalculates_vice_triple_captain_and_autosub(self):
        state = fixture_state(1)
        state.managers = (manager(1, captain=12, vice=7, chip="3xc"),)
        model = FixtureModel(
            state,
            (
                Condition(1, "dnp", player_id=12),
                Condition(1, "minutes", player_id=7, value=90),
                Condition(1, "minutes", player_id=11, value=90),
            ),
        )
        elements, _ = model.draw(random.Random(1))
        multipliers = effective_multipliers(
            state.managers[0], state.players, elements, {1: True, 2: True}
        )
        self.assertEqual(multipliers[12], 0)
        self.assertEqual(multipliers[7], 3)
        self.assertEqual(multipliers[11], 1)

    def test_bench_boost_counts_every_appearing_pick(self):
        state = fixture_state(5)
        state.managers = (manager(1, chip="bboost"),)
        elements, _ = FixtureModel(state).draw(random.Random(1))
        multipliers = effective_multipliers(
            state.managers[0], state.players, elements, {i: True for i in range(1, 11)}
        )
        self.assertTrue(all(mult >= 1 for mult in multipliers.values()))


class OutlookTests(unittest.TestCase):
    def test_full_five_three_and_one_fixture_outlooks_are_useful(self):
        for count in (10, 5, 3, 1):
            with self.subTest(fixtures=count):
                state = fixture_state(count)
                result = outlook(state, samples=120)
                self.assertEqual(result["simulations"], 120)
                self.assertEqual(len(result["managers"]), 3)
                self.assertTrue(result["projected_last_entry_ids"])
                self.assertTrue(result["decisive_players"])
                self.assertTrue(result["important_fixtures"])
                self.assertTrue(all(r["last_probability"] is not None for r in result["managers"]))
                self.assertTrue(any(r["expected_remaining"] > 0 for r in result["managers"]))

    def test_reproducibility_and_tie_convention(self):
        state = fixture_state(3)
        result = outlook(state, samples=140, seed=33)
        self.assertEqual(result, outlook(state, samples=140, seed=33))
        for row in result["managers"]:
            self.assertAlmostEqual(
                row["last_probability"],
                row["outright_last_probability"] + row["tied_last_probability"],
                places=3,
            )
            self.assertLessEqual(row["score_interval_80"][0], row["median_score"])
            self.assertGreaterEqual(row["score_interval_80"][1], row["median_score"])
        strict = outlook(state, samples=140, seed=33, allow_tied_last=False)
        self.assertLessEqual(sum(r["last_probability"] for r in strict["managers"]), 1.0002)
        for row in strict["managers"]:
            self.assertEqual(row["last_probability"], row["outright_last_probability"])

    def test_identical_exposure_cancels_and_tied_chances_are_not_split(self):
        state = fixture_state(1)
        state.managers = (manager(1), manager(2))
        result = outlook(state, samples=80)
        self.assertEqual([r["last_probability"] for r in result["managers"]], [1, 1])
        self.assertFalse(result["decisive_players"])
        strict = outlook(state, samples=80, allow_tied_last=False)
        self.assertEqual([r["last_probability"] for r in strict["managers"]], [0, 0])
        self.assertEqual(strict["projected_last_entry_ids"], [])
        self.assertEqual(set(strict["projected_bottom_entry_ids"]), {1, 2})

    def test_completed_results_are_official_and_no_hypotheticals_available(self):
        state = fixture_state()
        state.event_final = True
        state.managers = tuple(replace(m, official_points=40 + m.entry_id) for m in state.managers)
        result = outlook(state, samples=120)
        self.assertEqual(result["simulations"], 1)
        self.assertEqual(result["catalog"], [])
        self.assertFalse(result["estimated"])
        for row in result["managers"]:
            self.assertEqual(row["projected_score"], row["current_score"])
            self.assertEqual(row["expected_remaining"], 0)

    def test_zero_sampled_risk_is_not_safe_or_a_search_proof(self):
        state = fixture_state(5)
        state.managers = (manager(1), replace(manager(2), transfer_cost=-100))
        payload = analysis_to_dict(analyse_state(state, include_scenarios=False))
        status = {r["entry_id"]: r["status"] for r in payload["managers"]}
        result = outlook(state, samples=100)
        row = next(r for r in result["managers"] if r["entry_id"] == 2)
        self.assertEqual(row["last_probability"], 0)
        self.assertEqual(status[2], "unresolved")
        self.assertIsNone(payload["outlook"]["managers"][0]["last_probability"])


class ForecastServiceTests(unittest.TestCase):
    def test_concurrent_visitors_share_one_simulation(self):
        service = AnalysisService()
        source = demo_state("early").fetched_at.isoformat()
        with patch("fpl_forfeit.service.outlook", wraps=outlook) as run:
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(
                    executor.map(
                        lambda _: service.forecast(900000, source=source, demo="early", samples=80),
                        range(4),
                    )
                )
            self.assertEqual(run.call_count, 1)
        self.assertTrue(all(r["managers"] == results[0]["managers"] for r in results))

    def test_cache_is_coalesced_immutable_and_comparisons_use_baseline(self):
        service = AnalysisService()
        source = demo_state("late").fetched_at.isoformat()
        first = service.forecast(900001, source=source, demo="late", samples=80)
        first["managers"][0]["projected_score"] = -999
        second = service.forecast(900001, source=source, demo="late", samples=80)
        self.assertTrue(second["cache"]["hit"])
        self.assertNotEqual(second["managers"][0]["projected_score"], -999)
        modified = service.forecast(
            900001,
            source=source,
            demo="late",
            samples=80,
            conditions=(Condition(701, "goal", player_id=9001),),
        )
        base = {r["entry_id"]: r for r in second["managers"]}
        for row in modified["managers"]:
            self.assertAlmostEqual(
                row["score_change"],
                row["projected_score"] - base[row["entry_id"]]["projected_score"],
                places=2,
            )
        with self.assertRaises(FPLGameweekUnavailable):
            service.forecast(1, source="expired")

    def test_http_generation_conditions_validation_and_cors(self):
        client = TestClient(create_app(AnalysisService()))
        source = demo_state("late").fetched_at.isoformat()
        url = "/api/league/900001/what-if?demo=late"
        response = client.post(
            url,
            json={
                "source": source,
                "conditions": [{"fixture_id": 701, "kind": "goal", "player_id": 9001}],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], source)
        self.assertTrue(all("risk_change" in r for r in response.json()["managers"]))
        conflict = client.post(
            url,
            json={
                "source": source,
                "conditions": [
                    {"fixture_id": 701, "kind": "blank", "player_id": 9001},
                    {"fixture_id": 701, "kind": "goal", "player_id": 9001},
                ],
            },
        )
        self.assertEqual(conflict.status_code, 422)
        self.assertIn("blank", conflict.json()["detail"])
        expired = client.get("/api/league/12/outlook?source=expired")
        self.assertEqual(expired.status_code, 409)
        missing = client.get("/api/league/12/outlook")
        self.assertEqual(missing.status_code, 422)
        preflight = client.options(
            url,
            headers={"Origin": "http://127.0.0.1:8080", "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertIn("POST", preflight.headers["access-control-allow-methods"])


class ExactConsistencyRegressionTests(unittest.TestCase):
    def test_late_entrant_can_score_without_unreachable_sixty_minutes(self):
        state = fixture_state(1)
        f = replace(state.fixtures[0], started=True, elapsed_minutes=80, home_score=0, away_score=0)
        outcomes = outcome_catalog(
            state.players[12], f, ElementScore(1, 20), stats={"minutes": 20}, remaining_minutes=10
        )
        self.assertTrue(any(o.goals == 1 for o in outcomes))
        self.assertTrue(all(o.minutes_delta <= 10 for o in outcomes))
        goal = next(o for o in outcomes if o.goals == 1 and o.assists == 0)
        self.assertEqual(goal.points_delta, 4)
        self.assertEqual(next(o for o in outcomes if o.baseline).points_delta, 0)

    def test_live_penalty_save_crossing_three_saves_uses_shared_scorer(self):
        state = fixture_state(1)
        fixture = replace(state.fixtures[0], started=True, home_score=0, away_score=0)
        outcomes = outcome_catalog(
            state.players[1],
            fixture,
            ElementScore(6, 70),
            stats={"minutes": 70, "clean_sheets": 1, "saves": 2},
        )
        penalty = next(o for o in outcomes if "saves a penalty" in o.label)
        self.assertEqual(penalty.points_delta, 6)

    def test_live_second_concession_deduction_applies_to_own_goal(self):
        state = fixture_state(1)
        fixture = replace(state.fixtures[0], started=True, home_score=1, away_score=0)
        outcomes = outcome_catalog(state.players[2], fixture, ElementScore(2, 70))
        own = next(o for o in outcomes if o.label == "scores an own goal")
        self.assertEqual(own.points_delta, -3)

    def test_defender_concedes_points_cannot_ignore_multiple_attackers(self):
        state = fixture_state(1)
        fixture = state.fixtures[0]
        ids = (7, 9, 2)  # two home midfielders and an away defender
        variables = tuple(
            ScenarioVariable(
                state.players[pid],
                fixture,
                outcome_catalog(state.players[pid], fixture, ElementScore(0, 0)),
            )
            for pid in ids
        )
        selected = (
            next(o for o in variables[0].outcomes if o.label == "scores"),
            next(o for o in variables[1].outcomes if o.label == "scores"),
            next(o for o in variables[2].outcomes if o.baseline),
        )
        self.assertFalse(football_consistent(selected, variables))
        two = next(o for o in variables[2].outcomes if "concedes twice" in o.label)
        self.assertTrue(football_consistent(selected[:2] + (two,), variables))
        self.assertEqual(two.points_delta, 1)

    def test_complete_and_incomplete_searches_stay_distinct(self):
        state = demo_state("late")
        limited = solve_candidate(state, state.managers[0], max_nodes=1)
        self.assertFalse(limited.exhausted)
        complete = solve_candidate(league_state((manager(1), manager(2)), scores()), manager(1))
        self.assertTrue(complete.exhausted)
        self.assertTrue(complete.scenarios)


class EvaluationTests(unittest.TestCase):
    def test_brier_and_reliability_use_real_recorded_prediction_targets(self):
        state = fixture_state(3)
        prediction = prediction_record(state, samples=50)
        final = replace(
            state,
            event_final=True,
            fetched_at=state.fetched_at + timedelta(days=1),
            managers=tuple(replace(m, official_points=40 + m.entry_id * 4) for m in state.managers),
        )
        result = evaluate([prediction], final)
        self.assertEqual(result["manager_predictions"], 3)
        self.assertTrue(0 <= result["mean_binary_brier"] <= 1)
        self.assertEqual(sum(b["count"] for b in result["calibration"]), 3)
        with self.assertRaises(ValueError):
            evaluate([prediction], state)
        with self.assertRaises(ValueError):
            evaluate([prediction], replace(final, managers=final.managers[:2]))

    def test_replay_records_are_reproducible(self):
        state = fixture_state(5)
        self.assertEqual(prediction_record(state, samples=30), prediction_record(state, samples=30))
