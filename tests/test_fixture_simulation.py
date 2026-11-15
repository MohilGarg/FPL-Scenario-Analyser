from __future__ import annotations

import random
import unittest
from dataclasses import replace

from fpl_forfeit.forecasting.models import Condition, ScenarioConflict
from fpl_forfeit.models import ElementScore, Fixture, Position
from fpl_forfeit.safety import conservative_score_bounds
from fpl_forfeit.scoring import event_points, final_manager_scores
from fpl_forfeit.state import current_standings
from fpl_forfeit.substitutions import effective_multipliers
from tests.helpers import league_state, manager, scores
from tests.simulator_helpers import FixtureModel, fixture_state


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
