from __future__ import annotations

import random
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from fpl_forfeit.analysis import analysis_to_dict
from fpl_forfeit.forecast import Condition, FixtureModel, ScenarioConflict, catalog, outlook
from fpl_forfeit.models import ElementScore, Position
from fpl_forfeit.scenarios import outcome_catalog
from fpl_forfeit.scoring import event_points, final_manager_scores
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.substitutions import effective_multipliers
from fpl_forfeit.web import create_app
from tests.test_forecast import fixture_state
from tests.test_service import _CountingClient
from tests.validation_states import validation_state


class ConditionalRegressionTests(unittest.TestCase):
    def test_maximum_pairing_is_valid_and_independent_of_condition_order(self):
        state = fixture_state(1)
        conditions = (
            Condition(1, "goal", player_id=7),
            Condition(1, "goal", player_id=9),
            Condition(1, "assist", player_id=13),
            Condition(1, "assist", player_id=9),
            Condition(1, "score", home_goals=2, away_goals=0),
        )
        for seed in range(200):
            sample = FixtureModel(state, conditions).draw(random.Random(seed))[1][0]
            self.assertEqual(sample.home_goals, 2)
            for pid, key in (
                (7, "goals_scored"),
                (9, "goals_scored"),
                (9, "assists"),
                (13, "assists"),
            ):
                self.assertEqual(sample.stats[pid][key], 1)
        expected = outlook(state, samples=60, conditions=conditions)
        self.assertEqual(expected, outlook(state, samples=60, conditions=conditions[::-1]))

    def test_goal_and_late_assist_never_fail_randomly(self):
        state = fixture_state(1)
        conditions = (
            Condition(1, "goal", player_id=7),
            Condition(1, "assist", player_id=13),
            Condition(1, "substitute", player_id=13, value=20),
            Condition(1, "score", home_goals=1, away_goals=0),
        )
        model = FixtureModel(state, conditions)
        for seed in range(250):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertEqual(sample.home_goals, 1)
            self.assertEqual(sample.stats[13]["minutes"], 20)
            self.assertEqual(sample.stats[13]["assists"], 1)
            self.assertGreater(sample.windows[7][1], sample.windows[13][0])

    def test_disjoint_forced_windows_are_rejected_before_sampling(self):
        with self.assertRaisesRegex(ScenarioConflict, "overlapping"):
            FixtureModel(
                fixture_state(1),
                (
                    Condition(1, "goal", player_id=7),
                    Condition(1, "minutes", player_id=7, value=55),
                    Condition(1, "assist", player_id=13),
                    Condition(1, "substitute", player_id=13, value=20),
                    Condition(1, "score", home_goals=1, away_goals=0),
                ),
            )

    def test_live_active_substitute_can_have_explicit_future_minutes(self):
        state = fixture_state(1)
        state.fixtures = (
            replace(
                state.fixtures[0], started=True, elapsed_minutes=75, home_score=0, away_score=0
            ),
        )
        state.fixture_minutes[(12, 1)] = 20
        state.fixture_stats[(12, 1)] = {"minutes": 20}
        state.live_scores[12] = ElementScore(1, 20)
        row = next(p for p in catalog(state)[0]["players"] if p["player_id"] == 12)
        self.assertEqual(row["maximum_final_minutes"], 35)
        self.assertEqual(row["maximum_substitute_minutes"], 0)
        self.assertEqual(catalog(state)[0]["remaining_minutes"], 15)
        model = FixtureModel(
            state,
            (Condition(1, "minutes", player_id=12, value=35), Condition(1, "goal", player_id=12)),
        )
        for seed in range(30):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertEqual(sample.stats[12]["minutes"], 35)
            self.assertEqual(sample.stats[12]["goals_scored"], 1)
            self.assertEqual(sample.stats[12]["clean_sheets"], 0)

    def test_zero_minute_disciplinary_appearance_cannot_be_erased_by_dnp(self):
        state = fixture_state(1)
        state.fixture_stats[(12, 1)] = {"minutes": 0, "red_cards": 1}
        with self.assertRaisesRegex(ScenarioConflict, "participate"):
            FixtureModel(state, (Condition(1, "dnp", player_id=12),))

    def test_substitution_limit_applies_separately_to_each_team(self):
        conditions = tuple(
            Condition(1, "substitute", player_id=pid, value=20) for pid in (3, 5, 7, 2, 4, 6)
        )
        model = FixtureModel(fixture_state(1), conditions)
        sample = model.draw(random.Random(9))[1][0]
        self.assertTrue(all(sample.stats[pid]["minutes"] == 20 for pid in (3, 5, 7, 2, 4, 6)))

    def test_keeper_substitutions_have_nonoverlapping_windows(self):
        model = FixtureModel(
            fixture_state(1),
            (
                Condition(1, "minutes", player_id=1, value=70),
                Condition(1, "substitute", player_id=15, value=20),
            ),
        )
        for seed in range(40):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertEqual(sample.windows[1], (0, 70))
            self.assertEqual(sample.windows[15], (70, 90))

    def test_fully_owned_on_field_team_has_no_anonymous_goalscorer(self):
        state = fixture_state(1)
        for pid in range(1, 12):
            state.players[pid] = replace(state.players[pid], team_id=1)
        conditions = tuple(Condition(1, "minutes", player_id=pid, value=90) for pid in range(1, 12))
        model = FixtureModel(
            state, conditions + (Condition(1, "score", home_goals=2, away_goals=0),)
        )
        for seed in range(50):
            sample = model.draw(random.Random(seed))[1][0]
            self.assertTrue(all(e[1] is not None for e in sample.events))
            self.assertEqual(sum(s["goals_scored"] for s in sample.stats.values()), 2)

    def test_saves_awarded_only_to_goalkeepers(self):
        self.assertEqual(event_points(Position.GOALKEEPER, {"minutes": 90, "saves": 6}), 4)
        for position in (Position.DEFENDER, Position.MIDFIELDER, Position.FORWARD):
            self.assertEqual(event_points(position, {"minutes": 90, "saves": 6}), 2)

    def test_exact_substituted_defender_retains_points_after_later_team_concession(self):
        state = fixture_state(1)
        fixture = replace(
            state.fixtures[0], started=True, elapsed_minutes=80, home_score=1, away_score=0
        )
        outcomes = outcome_catalog(
            state.players[2],
            fixture,
            ElementScore(6, 65),
            stats={"minutes": 65, "clean_sheets": 1},
            remaining_minutes=10,
            off_field=True,
        )
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].baseline)
        self.assertEqual(outcomes[0].points_delta, 0)
        self.assertEqual(outcomes[0].minutes_delta, 0)
        self.assertIsNone(outcomes[0].opponent_max_goals)


class BroaderFixtureProperties(unittest.TestCase):
    def test_decisive_beneficiary_groups_are_disjoint_and_use_entry_ids(self):
        state = fixture_state(1)
        state.managers = tuple(replace(m, manager_name="Same name") for m in state.managers)
        result = outlook(state, samples=180)
        self.assertTrue(result["decisive_players"])
        for player in result["decisive_players"]:
            helps = set(player["strong_performance_helps_entry_ids"])
            blanks = set(player["blank_helps_entry_ids"])
            self.assertTrue(helps)
            self.assertTrue(blanks)
            self.assertFalse(helps & blanks)
            self.assertEqual(len(helps), len(player["strong_performance_helps"]))

    def test_dgw_fixture_leverage_does_not_repeat_the_other_fixtures_range(self):
        state = fixture_state(1)
        state.fixtures = (
            replace(
                state.fixtures[0], started=True, elapsed_minutes=90, home_score=0, away_score=0
            ),
            replace(state.fixtures[0], id=2),
        )
        state.fixture_stats = {(pid, 1): {"minutes": 0} for pid in state.players}
        state.fixture_minutes = {(pid, 1): 0 for pid in state.players}
        result = outlook(state, samples=120)
        leverage = {f["fixture_id"]: f["relative_leverage"] for f in result["important_fixtures"]}
        self.assertEqual(leverage[1], 0)
        self.assertGreater(leverage[2], 0)

    def test_realistic_sizes_and_gameweek_phases_preserve_all_scoring_invariants(self):
        for remaining, count in ((10, 12), (5, 12), (3, 12), (1, 12), (5, 50)):
            state = validation_state(remaining, count)
            model = FixtureModel(state)
            rng = random.Random(918)
            for _ in range(30):
                scores, fixtures = model.draw(rng)
                for sample in fixtures:
                    fixture = model.by_fixture[sample.fixture_id]
                    for team, total in (
                        (fixture.home_team_id, sample.home_goals),
                        (fixture.away_team_id, sample.away_goals),
                    ):
                        self.assertEqual(total, sum(e[0] == team for e in sample.events))
                        for minute in {a for a, _ in sample.windows.values()}:
                            active = [
                                pid
                                for pid, (a, b) in sample.windows.items()
                                if state.players[pid].team_id == team and a <= minute < b
                            ]
                            self.assertLessEqual(len(active), 11)
                            self.assertLessEqual(
                                sum(
                                    state.players[pid].position == Position.GOALKEEPER
                                    for pid in active
                                ),
                                1,
                            )
                    for team, goal, assist, minute in sample.events:
                        self.assertFalse(goal is not None and goal == assist)
                        for pid in (goal, assist):
                            if pid is not None:
                                self.assertEqual(state.players[pid].team_id, team)
                                a, b = sample.windows[pid]
                                self.assertTrue(a <= minute < b)
                    self.assertLessEqual(sum(s["bonus"] for s in sample.stats.values()), 6)
                final = final_manager_scores(state, scores)
                complete = {p.team_id: True for p in state.players.values()}
                for m in state.managers:
                    weights = effective_multipliers(m, state.players, scores, complete)
                    self.assertEqual(
                        final[m.entry_id],
                        sum(scores[pid].points * w for pid, w in weights.items()) - m.transfer_cost,
                    )
                    self.assertLessEqual(
                        sum(w > 0 for w in weights.values()),
                        15 if m.active_chip == "bboost" else 11,
                    )

    def test_common_hit_shift_and_manager_order_leave_risk_unchanged(self):
        state = validation_state(3)
        before = outlook(state, samples=180)
        shifted = replace(
            state,
            managers=tuple(
                replace(m, transfer_cost=m.transfer_cost + 4) for m in reversed(state.managers)
            ),
        )
        after = outlook(shifted, samples=180)
        for a, b in zip(before["managers"], after["managers"], strict=True):
            self.assertEqual(a["entry_id"], b["entry_id"])
            self.assertEqual(a["last_probability"], b["last_probability"])
            self.assertAlmostEqual(a["projected_score"] - 4, b["projected_score"])

    def test_cached_presence_scoring_matches_uncached_reference(self):
        state = validation_state(3)
        seed, n = 13, 120
        rng, model = random.Random(seed), FixtureModel(state)
        reference = {m.entry_id: [] for m in state.managers}
        for _ in range(n):
            final = final_manager_scores(state, model.draw(rng)[0])
            for entry, score in final.items():
                reference[entry].append(score)
        for row in outlook(state, seed=seed, samples=n)["managers"]:
            self.assertEqual(row["projected_score"], round(sum(reference[row["entry_id"]]) / n, 2))

    def test_final_scores_do_not_change_with_seeds_or_sample_count(self):
        state = validation_state(0)
        a, b = outlook(state, seed=1, samples=12), outlook(state, seed=7, samples=4000)
        self.assertEqual(a["managers"], b["managers"])
        self.assertEqual(a["simulations"], 1)


class ExplorerAPIMatrixTests(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state(1)

        class FastService(AnalysisService):
            def forecast(self, league_id, **kwargs):
                kwargs["samples"] = 80
                return super().forecast(league_id, **kwargs)

        self.service = FastService()
        self.source = self.state.fetched_at.isoformat()
        self.service._generations.set(
            (123, None, self.source), {"league_id": 123, "fetched_at": self.source}
        )
        self.service._states.set((123, None, self.source), self.state)
        self.client = TestClient(create_app(self.service))

    def test_compatible_condition_matrix(self):
        cases = [
            [Condition(1, "goal", player_id=7)],
            [Condition(1, "goal", player_id=7, value=2)],
            [Condition(1, "goal", player_id=7), Condition(1, "goal", player_id=9)],
            [Condition(1, "goal", player_id=7), Condition(1, "assist", player_id=7)],
            [Condition(1, "clean_sheet", team_id=1)],
            [Condition(1, "dnp", player_id=12)],
            [Condition(1, "substitute", player_id=12, value=10)],
            [Condition(1, "score", home_goals=2, away_goals=1)],
            [
                Condition(1, "goal", player_id=7),
                Condition(1, "assist", player_id=13),
                Condition(1, "substitute", player_id=13, value=20),
                Condition(1, "score", home_goals=1, away_goals=0),
            ],
        ]
        from dataclasses import asdict

        for conditions in cases:
            with self.subTest(conditions=conditions):
                response = self.client.post(
                    "/api/league/123/what-if",
                    json={"source": self.source, "conditions": [asdict(c) for c in conditions]},
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(len(payload["managers"]), 3)
                self.assertTrue(all(0 <= m["last_probability"] <= 1 for m in payload["managers"]))
                self.assertTrue(all("score_change" in m for m in payload["managers"]))

    def test_invalid_condition_skips_baseline_computation(self):
        with patch(
            "fpl_forfeit.service.outlook", side_effect=AssertionError("Unnecessary simulation")
        ):
            response = self.client.post(
                "/api/league/123/what-if",
                json={
                    "source": self.source,
                    "conditions": [
                        {"fixture_id": 1, "kind": "goal", "player_id": 7},
                        {"fixture_id": 1, "kind": "blank", "player_id": 7},
                    ],
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("blank", response.json()["detail"])

    def test_equivalent_condition_order_reuses_cached_forecast(self):
        conditions = [
            {"fixture_id": 1, "kind": "goal", "player_id": 7},
            {"fixture_id": 1, "kind": "assist", "player_id": 13},
        ]
        a = self.client.post(
            "/api/league/123/what-if", json={"source": self.source, "conditions": conditions}
        ).json()
        b = self.client.post(
            "/api/league/123/what-if", json={"source": self.source, "conditions": conditions[::-1]}
        ).json()
        self.assertTrue(b["cache"]["hit"])
        self.assertEqual(a["managers"], b["managers"])


class SummaryConcurrencyTests(unittest.TestCase):
    def test_concurrent_summary_coalesces_fetch_parse_and_projection(self):
        client = _CountingClient()
        service = AnalysisService(client)
        state = validation_state(5)
        with patch("fpl_forfeit.service.state_from_snapshot", return_value=state) as parse:
            with (
                patch("fpl_forfeit.service.analysis_to_dict", wraps=analysis_to_dict) as compute,
                ThreadPoolExecutor(max_workers=4) as pool,
            ):
                results = list(
                    pool.map(
                        lambda _: service.analyse_league(777, include_scenarios=False), range(4)
                    )
                )
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(compute.call_count, 1)
        self.assertEqual(client.calls, 1)
        self.assertTrue(all(len(r["managers"]) == 12 for r in results))
        results[0]["managers"][0]["manager_name"] = "changed"
        self.assertNotEqual(results[1]["managers"][0]["manager_name"], "changed")
