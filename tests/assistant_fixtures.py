"""Small deterministic correctness fixtures; never served by production routes."""

from datetime import UTC, datetime, timedelta

from fpl_forfeit.assistant.optimisation import COUNTS, LIMITS
from fpl_forfeit.forecasting.horizon import OutcomeSamples
from fpl_forfeit.models import (
    ChipWindow,
    Fixture,
    FPLCatalogue,
    GameweekInfo,
    Manager,
    Pick,
    Player,
    PublicSquadSnapshot,
)

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def players():
    positions = [position for position, number in COUNTS.items() for _ in range(number)]
    return {
        pid: Player(
            pid,
            f"Player {pid}",
            (pid - 1) // 2 + 1,
            f"Club {(pid - 1) // 2 + 1}",
            position,
            season_starts=6,
            season_minutes=540,
            season_team_matches=6,
            price_tenths=50 + pid,
            season_goals=2,
            season_assists=1,
            season_saves=12,
            season_defensive_contribution=60,
            season_yellow_cards=1,
            expected_goals=1.8,
            expected_assists=0.8,
        )
        for pid, position in enumerate(positions, 1)
    }


def catalogue():
    roster = players()
    pairs = [(a, b) for a in range(1, 9) for b in range(1, 9) if a != b]
    fixtures = tuple(
        Fixture(
            i,
            7 + i % 6,
            a,
            b,
            f"Club {a}",
            f"Club {b}",
            False,
            False,
            NOW + timedelta(days=7 * (i % 6) + 1),
        )
        for i, (a, b) in enumerate(pairs, 1)
    )
    gameweeks = (GameweekInfo(6, NOW - timedelta(days=1), False),) + tuple(
        GameweekInfo(gw, NOW + timedelta(days=7 * (gw - 7) + 1), False) for gw in range(7, 13)
    )
    return FPLCatalogue(
        roster,
        fixtures,
        gameweeks,
        {},
        NOW,
        True,
        frozenset(),
        COUNTS,
        LIMITS,
        3,
        5,
        (ChipWindow("bboost", 1, 19, 1), ChipWindow("3xc", 1, 19, 1)),
    )


def snapshot():
    starters = (1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14)
    bench = (2, 6, 7, 15)
    picks = tuple(
        Pick(pid, i, 1 if i <= 11 else 0, pid == 13, pid == 14)
        for i, pid in enumerate(starters + bench, 1)
    )
    return PublicSquadSnapshot(
        42,
        "Fixture squad",
        6,
        Manager(42, "Test", "Fixture", picks),
        10,
        1000,
        {pid: None for pid in range(1, 16)},
        {pid: None for pid in range(1, 16)},
        (),
        NOW,
    )


def outcomes():
    return {pid: OutcomeSamples((pid, pid), (90, 90), bytes([1, 1])) for pid in range(1, 16)}


class FixtureClient:
    def __init__(self):
        self.catalogue_calls = 0
        self.squad_calls = 0

    def forecast_catalogue(self):
        self.catalogue_calls += 1
        return catalogue()

    def public_squad(self, entry_id, catalogue):
        self.squad_calls += 1
        return snapshot()
