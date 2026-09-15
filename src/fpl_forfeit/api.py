from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import (
    POSITION_BY_ELEMENT_TYPE,
    ElementScore,
    Fixture,
    LeagueState,
    Manager,
    Pick,
    Player,
)

BASE_URL = "https://fantasy.premierleague.com/api"
USER_AGENT = "FPL-Forfeit-Analyser/0.1 (+local command-line tool)"


class FPLAPIError(RuntimeError):
    """An FPL request succeeded badly or returned an unexpected shape."""


class FPLClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.load(response)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise FPLAPIError(
                    "FPL refused this request. The league may be private or authentication may "
                    "be required. Save an authenticated snapshot and use --snapshot instead."
                ) from exc
            raise FPLAPIError(f"FPL returned HTTP {exc.code} for {path}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FPLAPIError(f"Could not read FPL endpoint {path}: {exc}") from exc

    def fetch_snapshot(self, league_id: int, gameweek: int | None = None) -> dict[str, Any]:
        bootstrap = self._get("bootstrap-static/")
        if gameweek is None:
            gameweek = _current_gameweek(bootstrap)

        standings: list[dict[str, Any]] = []
        page = 1
        league_name = str(league_id)
        while True:
            payload = self._get(
                f"leagues-classic/{league_id}/standings/?page_standings={page}"
            )
            league_name = payload.get("league", {}).get("name", league_name)
            block = payload.get("standings", {})
            standings.extend(block.get("results", []))
            if not block.get("has_next"):
                break
            page += 1
            if page > 100:
                raise FPLAPIError("League pagination exceeded 100 pages; refusing an unsafe fetch")

        picks: dict[str, Any] = {}
        for row in standings:
            entry_id = int(row["entry"])
            picks[str(entry_id)] = self._get(f"entry/{entry_id}/event/{gameweek}/picks/")

        return {
            "schema_version": 1,
            "fetched_at": datetime.now(UTC).isoformat(),
            "league_id": league_id,
            "league_name": league_name,
            "gameweek": gameweek,
            "bootstrap": bootstrap,
            "fixtures": self._get(f"fixtures/?event={gameweek}"),
            "live": self._get(f"event/{gameweek}/live/"),
            "standings": standings,
            "picks": picks,
        }


def _current_gameweek(bootstrap: dict[str, Any]) -> int:
    events = bootstrap.get("events", [])
    current = next((event for event in events if event.get("is_current")), None)
    if current:
        return int(current["id"])
    finished = [int(event["id"]) for event in events if event.get("finished")]
    if finished:
        return max(finished)
    upcoming = [int(event["id"]) for event in events if event.get("is_next")]
    if upcoming:
        return min(upcoming)
    raise FPLAPIError("The bootstrap response did not identify a usable Gameweek")


def save_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FPLAPIError(f"Could not load snapshot {path}: {exc}") from exc
    if value.get("schema_version") != 1:
        raise FPLAPIError("Unsupported snapshot schema; expected schema_version 1")
    return value


def state_from_snapshot(snapshot: dict[str, Any]) -> LeagueState:
    bootstrap = snapshot["bootstrap"]
    teams = {int(team["id"]): str(team["name"]) for team in bootstrap["teams"]}
    players = {
        int(element["id"]): Player(
            id=int(element["id"]),
            name=str(element["web_name"]),
            team_id=int(element["team"]),
            team_name=teams[int(element["team"])],
            position=POSITION_BY_ELEMENT_TYPE[int(element["element_type"])],
        )
        for element in bootstrap["elements"]
    }

    gameweek = int(snapshot["gameweek"])
    fixtures = tuple(
        Fixture(
            id=int(item["id"]),
            gameweek=gameweek,
            home_team_id=int(item["team_h"]),
            away_team_id=int(item["team_a"]),
            home_team_name=teams[int(item["team_h"])],
            away_team_name=teams[int(item["team_a"])],
            started=bool(item.get("started")),
            finished=bool(item.get("finished") or item.get("finished_provisional")),
            kickoff_time=_parse_datetime(item.get("kickoff_time")),
            home_score=_optional_int(item.get("team_h_score")),
            away_score=_optional_int(item.get("team_a_score")),
        )
        for item in snapshot["fixtures"]
    )

    live_scores = {
        int(item["id"]): ElementScore(
            points=int(item.get("stats", {}).get("total_points", 0)),
            minutes=int(item.get("stats", {}).get("minutes", 0)),
        )
        for item in snapshot["live"].get("elements", [])
    }
    for player_id in players:
        live_scores.setdefault(player_id, ElementScore(points=0, minutes=0))
    fixture_minutes: dict[tuple[int, int], int] = {}
    for item in snapshot["live"].get("elements", []):
        player_id = int(item["id"])
        for explanation in item.get("explain", []):
            fixture_id = int(explanation["fixture"])
            minutes = next(
                (
                    int(stat.get("value", 0))
                    for stat in explanation.get("stats", [])
                    if stat.get("identifier") == "minutes"
                ),
                0,
            )
            fixture_minutes[(player_id, fixture_id)] = minutes

    managers: list[Manager] = []
    pick_payloads = snapshot["picks"]
    for row in snapshot["standings"]:
        entry_id = int(row["entry"])
        payload = pick_payloads[str(entry_id)]
        picks = tuple(
            Pick(
                player_id=int(item["element"]),
                squad_position=int(item["position"]),
                api_multiplier=int(item.get("multiplier", 0)),
                is_captain=bool(item.get("is_captain")),
                is_vice_captain=bool(item.get("is_vice_captain")),
            )
            for item in sorted(payload["picks"], key=lambda value: int(value["position"]))
        )
        history = payload.get("entry_history", {})
        managers.append(
            Manager(
                entry_id=entry_id,
                manager_name=str(row.get("player_name") or f"Entry {entry_id}"),
                team_name=str(row.get("entry_name") or f"Team {entry_id}"),
                picks=picks,
                transfer_cost=int(history.get("event_transfers_cost", 0)),
                active_chip=payload.get("active_chip"),
            )
        )

    fetched_at = _parse_datetime(snapshot.get("fetched_at")) or datetime.now(UTC)
    return LeagueState(
        league_id=int(snapshot["league_id"]),
        league_name=str(snapshot.get("league_name") or snapshot["league_id"]),
        gameweek=gameweek,
        players=players,
        fixtures=fixtures,
        managers=tuple(managers),
        live_scores=live_scores,
        fetched_at=fetched_at,
        fixture_minutes=fixture_minutes,
        raw=snapshot,
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
