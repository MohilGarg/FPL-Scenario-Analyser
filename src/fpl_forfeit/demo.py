from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from .models import ElementScore, Fixture, LeagueState, Manager, Pick, Player, Position
from .substitutions import effective_multipliers

DemoMode = Literal["early", "late", "live", "complete"]
DEMO_MODES: tuple[DemoMode, ...] = ("early", "late", "live", "complete")

_MANAGER_NAMES = (
    "Alex",
    "Ben",
    "Chloe",
    "Dev",
    "Ella",
    "Farah",
    "George",
    "Hannah",
    "Imran",
    "Jess",
    "Kieran",
    "Lucy",
)

_POSITIONS = (
    Position.GOALKEEPER,
    Position.DEFENDER,
    Position.DEFENDER,
    Position.DEFENDER,
    Position.MIDFIELDER,
    Position.MIDFIELDER,
    Position.MIDFIELDER,
    Position.MIDFIELDER,
    Position.FORWARD,
    Position.FORWARD,
    Position.FORWARD,
    Position.GOALKEEPER,
    Position.MIDFIELDER,
    Position.DEFENDER,
    Position.DEFENDER,
)

_PENDING_PLAYERS = {
    9001: ("Haaland", Position.FORWARD, 1, "Manchester City"),
    9002: ("Bruno", Position.MIDFIELDER, 2, "Manchester United"),
    9003: ("Gvardiol", Position.DEFENDER, 1, "Manchester City"),
    9004: ("Saka", Position.MIDFIELDER, 3, "Arsenal"),
    9005: ("Isak", Position.FORWARD, 4, "Newcastle"),
    9006: ("Saliba", Position.DEFENDER, 3, "Arsenal"),
    9007: ("Watkins", Position.FORWARD, 5, "Aston Villa"),
    9008: ("Palmer", Position.MIDFIELDER, 6, "Chelsea"),
    9009: ("Van Dijk", Position.DEFENDER, 5, "Liverpool"),
}


def demo_state(mode: DemoMode) -> LeagueState:
    if mode not in DEMO_MODES:
        raise ValueError(f"Unknown demo mode: {mode}")

    players: dict[int, Player] = {}
    scores: dict[int, ElementScore] = {}
    managers: list[Manager] = []
    manager_unique_ids: dict[int, list[int]] = {}

    for player_id, (name, position, team_id, team_name) in _PENDING_PLAYERS.items():
        players[player_id] = Player(player_id, name, team_id, team_name, position)
        scores[player_id] = ElementScore(0, 0)

    for number, manager_name in enumerate(_MANAGER_NAMES, start=1):
        unique_ids: list[int] = []
        for slot, position in enumerate(_POSITIONS, start=1):
            player_id = number * 100 + slot
            unique_ids.append(player_id)
            players[player_id] = Player(
                player_id,
                f"{manager_name} player {slot}",
                100,
                "Completed FC",
                position,
            )
            scores[player_id] = ElementScore(0, 90)
        manager_unique_ids[number] = unique_ids

        selected = list(unique_ids)
        if mode in {"late", "live"} and number <= 4:
            if number in {1, 2}:
                selected[10] = 9001
            elif number == 3:
                selected[6] = 9002
            else:
                selected[2] = 9003
        elif mode == "early":
            selected[2] = (9003, 9006, 9009)[number % 3]
            selected[6] = (9002, 9004, 9008)[number % 3]
            selected[10] = (9001, 9005, 9007)[number % 3]

        captain_id = selected[9]
        if mode in {"late", "live"} and number == 1:
            captain_id = 9001
        elif mode in {"late", "live"} and number == 3:
            captain_id = 9002
        elif mode == "early" and number % 3 == 0:
            captain_id = selected[10]
        vice_id = selected[5]
        picks = tuple(
            Pick(
                player_id=player_id,
                squad_position=slot,
                is_captain=player_id == captain_id,
                is_vice_captain=player_id == vice_id,
            )
            for slot, player_id in enumerate(selected, start=1)
        )
        transfer_cost = 4 if number == 2 and mode in {"late", "live"} else 0
        chip = "3xc" if number == 3 and mode in {"late", "live"} else None
        if number == 5 and mode == "early":
            chip = "bboost"
        managers.append(
            Manager(
                entry_id=number,
                manager_name=manager_name,
                team_name=f"{manager_name}'s XI",
                picks=picks,
                transfer_cost=transfer_cost,
                active_chip=chip,
            )
        )

    fixtures = _fixtures_for_mode(mode)
    if mode == "live":
        scores[9001] = ElementScore(2, 68)
        scores[9002] = ElementScore(6, 68)
        scores[9003] = ElementScore(6, 68)

    # Dev has a confirmed missing starter and a legal defender autosub in the late demos.
    if mode in {"late", "live"}:
        scores[manager_unique_ids[4][1]] = ElementScore(0, 0)
        scores[manager_unique_ids[4][13]] = ElementScore(5, 90)

    targets = {
        "early": (18, 20, 22, 25, 27, 29, 31, 33, 35, 37, 39, 41),
        "late": (45, 48, 49, 51, 86, 88, 90, 92, 94, 96, 98, 100),
        "live": (51, 53, 54, 58, 92, 94, 96, 98, 100, 102, 104, 106),
        "complete": (61, 64, 67, 69, 71, 73, 75, 77, 79, 81, 83, 85),
    }[mode]
    _allocate_manager_scores(players, scores, tuple(managers), fixtures, targets)

    managers = [
        Manager(
            entry_id=manager.entry_id,
            manager_name=manager.manager_name,
            team_name=manager.team_name,
            picks=manager.picks,
            transfer_cost=manager.transfer_cost,
            active_chip=manager.active_chip,
            official_points=targets[manager.entry_id - 1] + manager.transfer_cost,
        )
        for manager in managers
    ]
    now = datetime(2026, 9, 12, 16, 30, tzinfo=UTC)
    fixture_minutes = (
        {(player_id, 701): 68 for player_id in (9001, 9002, 9003)} if mode == "live" else {}
    )
    return LeagueState(
        league_id=900000 + DEMO_MODES.index(mode),
        league_name=f"Demo League — {mode.title()} Gameweek",
        gameweek=7,
        players=players,
        fixtures=fixtures,
        managers=tuple(managers),
        live_scores=scores,
        fetched_at=now,
        fixture_minutes=fixture_minutes,
        raw={
            "demo_mode": mode,
            "bootstrap": {
                "events": [{"id": 7, "finished": mode == "complete"}],
            },
        },
    )


def _fixtures_for_mode(mode: DemoMode) -> tuple[Fixture, ...]:
    completed = Fixture(
        700,
        7,
        100,
        101,
        "Completed FC",
        "Played United",
        True,
        True,
        home_score=2,
        away_score=1,
    )
    if mode == "complete":
        return (completed,)
    if mode in {"late", "live"}:
        return (
            completed,
            Fixture(
                701,
                7,
                1,
                2,
                "Manchester City",
                "Manchester United",
                mode == "live",
                False,
                home_score=0 if mode == "live" else None,
                away_score=0 if mode == "live" else None,
            ),
        )
    fixtures = [completed]
    for player_id, (_, _, team_id, team_name) in _PENDING_PLAYERS.items():
        fixtures.append(
            Fixture(
                710 + (player_id - 9001),
                7,
                team_id,
                200 + team_id,
                team_name,
                f"Opponent {team_id}",
                False,
                False,
            )
        )
    return tuple(fixtures)


def _allocate_manager_scores(
    players: dict[int, Player],
    scores: dict[int, ElementScore],
    managers: tuple[Manager, ...],
    fixtures: tuple[Fixture, ...],
    targets: tuple[int, ...],
) -> None:
    team_ids = {player.team_id for player in players.values()}
    team_complete = {
        team_id: all(fixture.finished for fixture in fixtures if fixture.involves(team_id))
        for team_id in team_ids
    }
    for manager, target in zip(managers, targets, strict=True):
        multipliers = effective_multipliers(manager, players, scores, team_complete)
        current = sum(scores[player_id].points * value for player_id, value in multipliers.items())
        required = target + manager.transfer_cost - current
        candidates = [
            pick.player_id
            for pick in manager.picks
            if multipliers[pick.player_id] == 1
            and pick.player_id < 9000
            and not pick.is_captain
            and not pick.is_vice_captain
        ]
        if required < 0 or not candidates:
            raise ValueError("Demo score target cannot be allocated")
        quotient, remainder = divmod(required, len(candidates))
        for index, player_id in enumerate(candidates):
            existing = scores[player_id]
            scores[player_id] = ElementScore(
                existing.points + quotient + (1 if index < remainder else 0),
                existing.minutes,
            )
