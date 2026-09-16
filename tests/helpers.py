from __future__ import annotations

from datetime import UTC, datetime

from fpl_forfeit.models import (
    ElementScore,
    Fixture,
    LeagueState,
    Manager,
    Pick,
    Player,
    Position,
)


def players() -> dict[int, Player]:
    positions = {
        1: Position.GOALKEEPER,
        2: Position.DEFENDER,
        3: Position.DEFENDER,
        4: Position.DEFENDER,
        5: Position.DEFENDER,
        6: Position.DEFENDER,
        7: Position.MIDFIELDER,
        8: Position.MIDFIELDER,
        9: Position.MIDFIELDER,
        10: Position.MIDFIELDER,
        11: Position.MIDFIELDER,
        12: Position.FORWARD,
        13: Position.FORWARD,
        14: Position.FORWARD,
        15: Position.GOALKEEPER,
    }
    return {
        player_id: Player(
            player_id,
            f"Player {player_id}",
            player_id,
            f"Team {player_id}",
            position,
        )
        for player_id, position in positions.items()
    }


def picks(*, captain: int = 12, vice: int = 7) -> tuple[Pick, ...]:
    ordered = [1, 2, 3, 4, 7, 8, 9, 10, 12, 13, 14, 15, 11, 5, 6]
    return tuple(
        Pick(
            player_id=player_id,
            squad_position=position,
            is_captain=player_id == captain,
            is_vice_captain=player_id == vice,
        )
        for position, player_id in enumerate(ordered, start=1)
    )


def manager(
    entry_id: int = 1,
    *,
    captain: int = 12,
    vice: int = 7,
    chip: str | None = None,
    transfer_cost: int = 0,
) -> Manager:
    return Manager(
        entry_id,
        f"Manager {entry_id}",
        f"Squad {entry_id}",
        picks(captain=captain, vice=vice),
        transfer_cost,
        chip,
    )


def scores(default_points: int = 1, default_minutes: int = 90) -> dict[int, ElementScore]:
    return {player_id: ElementScore(default_points, default_minutes) for player_id in players()}


def league_state(
    managers: tuple[Manager, ...],
    live_scores: dict[int, ElementScore],
    fixtures: tuple[Fixture, ...] = (),
) -> LeagueState:
    return LeagueState(
        league_id=123,
        league_name="Test League",
        gameweek=1,
        players=players(),
        fixtures=fixtures,
        managers=managers,
        live_scores=live_scores,
        fetched_at=datetime.now(UTC),
    )
