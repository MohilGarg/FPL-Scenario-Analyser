"""Test-only bridge from a league state to the shared football simulator.

The public Monte Carlo adapter was removed from the application. The shared simulator in
``fpl_forfeit.forecasting`` still powers the private assistant, so these helpers keep its
football invariants under test with league-shaped fixtures.
"""

from __future__ import annotations

import random
from dataclasses import replace

from fpl_forfeit.forecasting.models import (
    MODEL_VERSION,
    Condition,
    DataSource,
    FixtureSample,
    ForecastInputs,
)
from fpl_forfeit.forecasting.simulation import FixtureSimulator
from fpl_forfeit.models import ElementScore, Fixture, LeagueState
from fpl_forfeit.points import event_points
from tests.helpers import league_state, manager, scores


def fixture_state(count: int = 5) -> LeagueState:
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


def football_inputs(state: LeagueState, *, model_version: str = MODEL_VERSION) -> ForecastInputs:
    """Strip league identity, squads, multipliers, hits and official event totals."""
    return ForecastInputs(
        players=state.players,
        fixtures=() if state.event_final else state.fixtures,
        fetched_at=state.fetched_at,
        fixture_minutes=state.fixture_minutes,
        fixture_stats=state.fixture_stats,
        team_goal_rates=state.team_goal_rates,
        provenance=(DataSource(state.provenance),),
        confirmed_appearances=state.confirmed_appearances,
        model_version=model_version,
    )


class FixtureModel(FixtureSimulator):
    """Simulate the league's owned players and fold draws into live element scores."""

    def __init__(self, state: LeagueState, conditions: tuple[Condition, ...] = ()) -> None:
        self.state = state
        owned = {p.player_id for m in state.managers for p in m.picks}
        super().__init__(football_inputs(state), conditions, player_ids=tuple(sorted(owned)))

    def draw(self, rng: random.Random) -> tuple[dict[int, ElementScore], tuple[FixtureSample, ...]]:
        result = dict(self.state.live_scores)
        fixtures = self.sample(rng)
        for sample in fixtures:
            for pid, stats in sample.stats.items():
                old = self.state.fixture_stats.get((pid, sample.fixture_id), {})
                delta = event_points(self.players[pid].position, stats) - event_points(
                    self.players[pid].position, old
                )
                original = result[pid]
                result[pid] = ElementScore(
                    original.points + delta,
                    original.minutes + stats["minutes"] - old.get("minutes", 0),
                    carded=original.carded
                    or bool(stats.get("yellow_cards", 0) or stats.get("red_cards", 0)),
                )
        return result, fixtures
