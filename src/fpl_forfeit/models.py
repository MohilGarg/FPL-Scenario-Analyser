from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Position(StrEnum):
    GOALKEEPER = "GK"
    DEFENDER = "DEF"
    MIDFIELDER = "MID"
    FORWARD = "FWD"


POSITION_BY_ELEMENT_TYPE = {
    1: Position.GOALKEEPER,
    2: Position.DEFENDER,
    3: Position.MIDFIELDER,
    4: Position.FORWARD,
}


@dataclass(frozen=True, slots=True)
class MatchAppearance:
    fixture_id: int
    gameweek: int
    minutes: int
    started: bool | None


@dataclass(frozen=True, slots=True)
class ConfirmedAppearance:
    player_id: int
    fixture_id: int
    provider_player_id: int
    provider_fixture_id: int
    status: str  # starter / bench / absent
    confirmed_at: datetime
    source: str
    on_minute: int | None = None
    off_minute: int | None = None


@dataclass(frozen=True, slots=True)
class Player:
    id: int
    name: str
    team_id: int
    team_name: str
    position: Position
    season_starts: int | None = None
    season_minutes: int | None = None
    price_tenths: int | None = None
    availability_status: str | None = None
    availability_news: str = ""
    chance_of_playing: int | None = None
    season_team_matches: int | None = None
    season_goals: int | None = None
    season_assists: int | None = None
    season_saves: int | None = None
    season_defensive_contribution: int | None = None
    season_yellow_cards: int | None = None
    expected_goals: float | None = None
    expected_assists: float | None = None
    expected_goal_involvements: float | None = None
    appearance_history: tuple[MatchAppearance, ...] = ()


@dataclass(frozen=True, slots=True)
class Fixture:
    id: int
    gameweek: int
    home_team_id: int
    away_team_id: int
    home_team_name: str
    away_team_name: str
    started: bool
    finished: bool
    kickoff_time: datetime | None = None
    home_score: int | None = None
    away_score: int | None = None
    elapsed_minutes: int | None = None

    def involves(self, team_id: int) -> bool:
        return team_id in (self.home_team_id, self.away_team_id)

    def opponent_of(self, team_id: int) -> int:
        if team_id == self.home_team_id:
            return self.away_team_id
        if team_id == self.away_team_id:
            return self.home_team_id
        raise ValueError(f"Team {team_id} is not in fixture {self.id}")

    def team_name(self, team_id: int) -> str:
        if team_id == self.home_team_id:
            return self.home_team_name
        if team_id == self.away_team_id:
            return self.away_team_name
        raise ValueError(f"Team {team_id} is not in fixture {self.id}")

    def goals_against(self, team_id: int) -> int | None:
        if team_id == self.home_team_id:
            return self.away_score
        if team_id == self.away_team_id:
            return self.home_score
        raise ValueError(f"Team {team_id} is not in fixture {self.id}")


@dataclass(frozen=True, slots=True)
class Pick:
    player_id: int
    squad_position: int
    api_multiplier: int = 0
    is_captain: bool = False
    is_vice_captain: bool = False

    @property
    def is_starter(self) -> bool:
        return self.squad_position <= 11


@dataclass(frozen=True, slots=True)
class Manager:
    entry_id: int
    manager_name: str
    team_name: str
    picks: tuple[Pick, ...]
    transfer_cost: int = 0
    active_chip: str | None = None
    official_points: int | None = None

    @property
    def display_name(self) -> str:
        return f"{self.manager_name} ({self.team_name})"


@dataclass(frozen=True, slots=True)
class ElementScore:
    points: int
    minutes: int
    carded: bool = False

    @property
    def appeared(self) -> bool:
        return self.minutes > 0 or self.carded


@dataclass(slots=True)
class LeagueState:
    league_id: int
    league_name: str
    gameweek: int
    players: dict[int, Player]
    fixtures: tuple[Fixture, ...]
    managers: tuple[Manager, ...]
    live_scores: dict[int, ElementScore]
    fetched_at: datetime
    fixture_minutes: dict[tuple[int, int], int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    fixture_stats: dict[tuple[int, int], dict[str, int]] = field(default_factory=dict)
    team_goal_rates: dict[int, tuple[float, float]] = field(default_factory=dict)
    event_final: bool = False
    provenance: str = "caller_supplied"
    confirmed_appearances: dict[tuple[int, int], ConfirmedAppearance] = field(default_factory=dict)
    forecast_context: dict[str, Any] = field(default_factory=dict)

    def fixtures_for_team(self, team_id: int) -> tuple[Fixture, ...]:
        return tuple(fixture for fixture in self.fixtures if fixture.involves(team_id))

    def unfinished_fixtures_for_team(self, team_id: int) -> tuple[Fixture, ...]:
        return tuple(fixture for fixture in self.fixtures_for_team(team_id) if not fixture.finished)

    def team_complete(self) -> dict[int, bool]:
        team_ids = {player.team_id for player in self.players.values()}
        return {
            # A blank-Gameweek team has no remaining chance to supply minutes.
            team_id: all(fixture.finished for fixture in self.fixtures_for_team(team_id))
            for team_id in team_ids
        }
