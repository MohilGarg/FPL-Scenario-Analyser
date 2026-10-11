from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .cache import TTLCache
from .forecasting.models import TeamMatchResult
from .forecasting.statistics import estimate_team_goal_rates
from .models import (
    POSITION_BY_ELEMENT_TYPE,
    ChipWindow,
    ConfirmedAppearance,
    ElementScore,
    Fixture,
    FPLCatalogue,
    GameweekInfo,
    LeagueState,
    Manager,
    MatchAppearance,
    Pick,
    Player,
    PublicSquadSnapshot,
)

BASE_URL = "https://fantasy.premierleague.com/api"
USER_AGENT = "FPL-Forfeit-Analyser/0.3 (+public analysis service)"


class FPLAPIError(RuntimeError):
    """An FPL request succeeded badly or returned an unexpected shape."""


class FPLNotFoundError(FPLAPIError):
    """The requested public FPL resource does not exist."""


class FPLAccessError(FPLAPIError):
    """FPL denied access to a resource."""


class FPLLeagueSizeError(FPLAPIError):
    """A league is too large for this focused mini-league analyser."""


class FPLGameweekUnavailable(FPLAPIError):
    """The requested final/current Gameweek data is not available yet."""


class FPLClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        transport = os.getenv("FPL_HTTP_TRANSPORT", "auto")
        if transport not in {"auto", "urllib", "curl"}:
            raise ValueError("FPL_HTTP_TRANSPORT must be auto, urllib or curl")
        # Native Windows curl uses Schannel, avoiding a process-level OpenSSL
        # failure seen with some Windows Python builds. Linux hosting keeps urllib.
        self._curl = (
            shutil.which("curl.exe" if os.name == "nt" else "curl")
            if (transport == "curl" or (transport == "auto" and os.name == "nt"))
            else None
        )
        if transport == "curl" and not self._curl:
            raise FPLAPIError("curl is unavailable; install it or select FPL_HTTP_TRANSPORT=urllib")
        self._season_fixture_cache: TTLCache[int, list[dict[str, Any]]] = TTLCache(
            3600, max_items=1
        )
        self._player_histories: TTLCache[int, dict[str, Any]] = TTLCache(21600, max_items=1500)
        self._history_locks = tuple(Lock() for _ in range(32))

    def player_history(self, player_id: int) -> dict[str, Any]:
        with self._history_locks[player_id % len(self._history_locks)]:
            cached = self._player_histories.get(player_id)
            if cached is None:
                cached = self._get(f"element-summary/{player_id}/")
                self._player_histories.set(player_id, cached)
            return cached

    def forecast_catalogue(self) -> FPLCatalogue:
        """Two public requests; callers cache the whole observed generation."""
        return parse_forecast_catalogue(self._get("bootstrap-static/"), self._get("fixtures/"))

    def public_squad(self, entry_id: int, catalogue: FPLCatalogue) -> PublicSquadSnapshot:
        if entry_id <= 0:
            raise ValueError("Team ID must be positive.")
        profile = self._get(f"entry/{entry_id}/")
        now = datetime.now(UTC)
        available = sorted(
            (g.id for g in catalogue.gameweeks if g.deadline and g.deadline <= now), reverse=True
        )
        if profile.get("current_event") is not None:
            available = [g for g in available if g <= int(profile["current_event"])]
        payload = None
        for gameweek in available[:3]:
            try:
                payload = self._get(f"entry/{entry_id}/event/{gameweek}/picks/")
                break
            except FPLNotFoundError:
                continue
        if payload is None:
            raise FPLGameweekUnavailable(
                "No public deadline squad is available yet. Pre-deadline squads require FPL account access and are not guessed."
            )
        try:
            history = self._get(f"entry/{entry_id}/history/")
        except FPLAPIError:
            history = None
        return parse_public_squad(entry_id, gameweek, profile, payload, history)

    def _get(self, path: str) -> Any:
        if self._curl:
            return self._get_with_curl(path)
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                raise FPLNotFoundError(
                    "FPL could not find that league or Gameweek. Check the classic league ID."
                ) from exc
            if exc.code in (401, 403):
                raise FPLAccessError(
                    "FPL refused this request. The league may be private or authentication may "
                    "be required. Only publicly accessible leagues are supported."
                ) from exc
            raise FPLAPIError(f"FPL returned HTTP {exc.code} for {path}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FPLAPIError(f"Could not read FPL endpoint {path}: {exc}") from exc

    def _get_with_curl(self, path: str) -> Any:
        """Argument-list invocation, normal TLS verification and bounded timeouts."""
        try:
            response = subprocess.run(
                [
                    self._curl,
                    "--silent",
                    "--show-error",
                    "--location",
                    "--max-redirs",
                    "3",
                    "--connect-timeout",
                    str(self.timeout),
                    "--max-time",
                    str(self.timeout),
                    "--user-agent",
                    USER_AGENT,
                    "--header",
                    "Accept: application/json",
                    "--write-out",
                    "\n%{http_code}",
                    "--url",
                    f"{self.base_url}/{path.lstrip('/')}",
                ],
                capture_output=True,
                timeout=self.timeout + 3,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FPLAPIError(f"Could not read FPL endpoint {path}: {exc}") from exc
        if response.returncode:
            raise FPLAPIError(
                f"FPL request failed for {path}: " + response.stderr.decode(errors="replace")[:300]
            )
        try:
            body, code = response.stdout.rsplit(b"\n", 1)
            status = int(code)
        except (ValueError, TypeError) as exc:
            raise FPLAPIError("FPL HTTP transport returned an invalid response") from exc
        if status == 404:
            raise FPLNotFoundError(
                "FPL could not find that league or Gameweek. Check the classic league ID."
            )
        if status in (401, 403):
            raise FPLAccessError(
                "FPL refused this request. Only publicly accessible leagues are supported."
            )
        if not 200 <= status < 300:
            raise FPLAPIError(f"FPL returned HTTP {status} for {path}")
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise FPLAPIError(f"Could not parse FPL endpoint {path}") from exc

    def fetch_snapshot(
        self,
        league_id: int,
        gameweek: int | None = None,
        *,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        bootstrap = self._get("bootstrap-static/")
        current_gameweek = _current_gameweek(bootstrap)
        if gameweek is None:
            gameweek = current_gameweek
        event = next((item for item in bootstrap.get("events", []) if item["id"] == gameweek), None)
        if event is None:
            raise FPLGameweekUnavailable("That Gameweek is not available in the current season.")
        historical = gameweek != current_gameweek
        if historical and not event.get("finished"):
            raise FPLGameweekUnavailable(
                "Only the current Gameweek and completed previous Gameweeks can be viewed."
            )

        standings: list[dict[str, Any]] = []
        page = 1
        league_name = str(league_id)
        while True:
            payload = self._get(f"leagues-classic/{league_id}/standings/?page_standings={page}")
            league_name = payload.get("league", {}).get("name", league_name)
            block = payload.get("standings", {})
            standings.extend(block.get("results", []))
            if max_entries is not None and (
                len(standings) > max_entries
                or (block.get("has_next") and len(standings) >= max_entries)
            ):
                raise FPLLeagueSizeError(
                    f"This analyser supports mini-leagues with at most {max_entries} managers."
                )
            if not block.get("has_next"):
                break
            page += 1
            if page > 100:
                raise FPLAPIError("League pagination exceeded 100 pages; refusing an unsafe fetch")

        picks: dict[str, Any] = {}
        if not standings:
            raise FPLNotFoundError("No managers were found in that public classic league.")
        for row in standings:
            entry_id = int(row["entry"])
            try:
                picks[str(entry_id)] = self._get(f"entry/{entry_id}/event/{gameweek}/picks/")
            except FPLNotFoundError as exc:
                raise FPLGameweekUnavailable(
                    "Historical squad data is unavailable for one or more current league members. "
                    "Choose another Gameweek."
                    if historical
                    else "Gameweek squads are not public yet. Try again after the deadline."
                ) from exc
            if historical and picks[str(entry_id)].get("entry_history", {}).get("points") is None:
                raise FPLGameweekUnavailable("Final points are unavailable for this Gameweek.")

        # The season-wide feed can lag the event feed while a match is live. Never use
        # it as the source of current scorelines; it is only a minutes-history denominator.
        fixtures = self._get(f"fixtures/?event={gameweek}")
        season_fixtures = self._season_fixture_cache.get(1)
        if season_fixtures is None:
            try:
                season_fixtures = self._get("fixtures/")
                self._season_fixture_cache.set(1, season_fixtures)
            except FPLAPIError:
                pass  # Missing optional context must not prevent live analysis.
        season = {item["id"]: item for item in season_fixtures or []}
        season.update({item["id"]: item for item in fixtures})
        return {
            "schema_version": 1,
            "fetched_at": datetime.now(UTC).isoformat(),
            "league_id": league_id,
            "league_name": league_name,
            "gameweek": gameweek,
            "current_gameweek": current_gameweek,
            "historical": historical,
            "bootstrap": bootstrap,
            "fixtures": fixtures,
            **({"season_fixtures": list(season.values())} if season_fixtures is not None else {}),
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
    raise FPLGameweekUnavailable(
        "FPL has not published an active Gameweek yet. Please check back later."
    )


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


def parse_players(
    bootstrap: dict[str, Any],
    season_fixtures: list[dict[str, Any]] | None,
    histories: dict[str, Any] | None = None,
    *,
    future: bool = False,
) -> dict[int, Player]:
    """One roster parser for public live analysis and private future projections."""
    teams = {int(t["id"]): str(t["name"]) for t in bootstrap["teams"]}
    completed = {int(f["id"]) for f in season_fixtures or [] if f.get("finished")}
    matches = {
        tid: sum(
            bool(f.get("started"))
            for f in season_fixtures or []
            if tid in (f["team_h"], f["team_a"])
        )
        for tid in teams
    }
    return {
        int(e["id"]): Player(
            id=int(e["id"]),
            name=str(e["web_name"]),
            team_id=int(e["team"]),
            team_name=teams[int(e["team"])],
            position=POSITION_BY_ELEMENT_TYPE[int(e["element_type"])],
            season_starts=_optional_int(e.get("starts")),
            season_minutes=_optional_int(e.get("minutes")),
            price_tenths=_optional_int(e.get("now_cost")),
            availability_status=e.get("status"),
            availability_news=str(e.get("news") or ""),
            chance_of_playing=_optional_int(
                e.get("chance_of_playing_next_round")
                if future
                else e.get("chance_of_playing_this_round")
            ),
            season_team_matches=matches[int(e["team"])] if season_fixtures is not None else None,
            season_goals=_optional_int(e.get("goals_scored")),
            season_assists=_optional_int(e.get("assists")),
            season_saves=_optional_int(e.get("saves")),
            season_defensive_contribution=_optional_int(e.get("defensive_contribution")),
            season_yellow_cards=_optional_int(e.get("yellow_cards")),
            expected_goals=_optional_float(e.get("expected_goals")),
            expected_assists=_optional_float(e.get("expected_assists")),
            expected_goal_involvements=_optional_float(e.get("expected_goal_involvements")),
            appearance_history=parse_appearance_history(
                (histories or {}).get(str(e["id"]), {}), completed
            ),
        )
        for e in bootstrap["elements"]
    }


def parse_fixture(
    item: dict[str, Any], teams: dict[int, str], *, gameweek: int | None = None
) -> Fixture:
    return Fixture(
        id=int(item["id"]),
        gameweek=gameweek if gameweek is not None else int(item["event"]),
        home_team_id=int(item["team_h"]),
        away_team_id=int(item["team_a"]),
        home_team_name=teams[int(item["team_h"])],
        away_team_name=teams[int(item["team_a"])],
        started=bool(item.get("started")),
        finished=bool(item.get("finished") or item.get("finished_provisional")),
        kickoff_time=_parse_datetime(item.get("kickoff_time")),
        home_score=_optional_int(item.get("team_h_score")),
        away_score=_optional_int(item.get("team_a_score")),
        elapsed_minutes=_optional_int(item.get("minutes")) or None,
    )


def parse_forecast_catalogue(
    bootstrap: dict[str, Any], fixtures: list[dict[str, Any]]
) -> FPLCatalogue:
    teams = {int(t["id"]): str(t["name"]) for t in bootstrap["teams"]}
    expected_pairs = {(h, a) for h in teams for a in teams if h != a}
    pairs = {(int(f["team_h"]), int(f["team_a"])) for f in fixtures}
    settings = bootstrap.get("game_settings", {})
    return FPLCatalogue(
        players=parse_players(bootstrap, fixtures, future=True),
        fixtures=tuple(parse_fixture(f, teams) for f in fixtures if f.get("event") is not None),
        gameweeks=tuple(
            GameweekInfo(
                int(e["id"]), _parse_datetime(e.get("deadline_time")), bool(e.get("finished"))
            )
            for e in bootstrap.get("events", [])
        ),
        team_goal_rates=_team_goal_rates(fixtures),
        fetched_at=datetime.now(UTC),
        schedule_complete=len(fixtures) == len(expected_pairs) and pairs == expected_pairs,
        unassigned_team_ids=frozenset(
            int(t) for f in fixtures if f.get("event") is None for t in (f["team_h"], f["team_a"])
        ),
        squad_counts={
            POSITION_BY_ELEMENT_TYPE[int(p["id"])]: int(p["squad_select"])
            for p in bootstrap.get("element_types", [])
        },
        formation_limits={
            POSITION_BY_ELEMENT_TYPE[int(p["id"])]: (
                int(p["squad_min_play"]),
                int(p["squad_max_play"]),
            )
            for p in bootstrap.get("element_types", [])
        },
        club_limit=_optional_int(settings.get("squad_team_limit")),
        maximum_free_transfers=1 + int(settings["max_extra_free_transfers"])
        if settings.get("max_extra_free_transfers") is not None
        else None,
        chips=tuple(
            ChipWindow(
                str(c["name"]), int(c["start_event"]), int(c["stop_event"]), int(c["number"])
            )
            for c in bootstrap.get("chips", [])
        ),
        missing_schedule_team_ids=frozenset(t for pair in expected_pairs - pairs for t in pair),
    )


def parse_public_squad(
    entry_id: int,
    gameweek: int,
    profile: dict[str, Any],
    payload: dict[str, Any],
    history: dict[str, Any] | None,
) -> PublicSquadSnapshot:
    rows = sorted(payload["picks"], key=lambda p: int(p["position"]))
    picks = tuple(
        Pick(
            int(p["element"]),
            int(p["position"]),
            int(p.get("multiplier", 0)),
            bool(p.get("is_captain")),
            bool(p.get("is_vice_captain")),
        )
        for p in rows
    )
    entry = payload.get("entry_history", {})
    return PublicSquadSnapshot(
        entry_id=entry_id,
        name=str(profile.get("name") or f"Team {entry_id}"),
        gameweek=gameweek,
        manager=Manager(entry_id, "My team", str(profile.get("name") or f"Team {entry_id}"), picks),
        bank_tenths=_optional_int(entry.get("bank", profile.get("last_deadline_bank"))),
        value_tenths=_optional_int(entry.get("value", profile.get("last_deadline_value"))),
        purchase_prices={int(p["element"]): _optional_int(p.get("purchase_price")) for p in rows},
        selling_prices={int(p["element"]): _optional_int(p.get("selling_price")) for p in rows},
        chips_used=tuple((str(c["name"]), int(c["event"])) for c in history["chips"])
        if history and "chips" in history
        else None,
        fetched_at=datetime.now(UTC),
    )


def state_from_snapshot(snapshot: dict[str, Any]) -> LeagueState:
    bootstrap = snapshot["bootstrap"]
    teams = {int(team["id"]): str(team["name"]) for team in bootstrap["teams"]}
    players = parse_players(
        bootstrap, snapshot.get("season_fixtures"), snapshot.get("player_histories", {})
    )

    gameweek = int(snapshot["gameweek"])
    fixtures = tuple(parse_fixture(item, teams, gameweek=gameweek) for item in snapshot["fixtures"])

    live_scores = {
        int(item["id"]): ElementScore(
            points=int(item.get("stats", {}).get("total_points", 0)),
            minutes=int(item.get("stats", {}).get("minutes", 0)),
            carded=bool(
                item.get("stats", {}).get("yellow_cards", 0)
                or item.get("stats", {}).get("red_cards", 0)
            ),
        )
        for item in snapshot["live"].get("elements", [])
    }
    for player_id in players:
        live_scores.setdefault(player_id, ElementScore(points=0, minutes=0))
    fixture_minutes: dict[tuple[int, int], int] = {}
    fixture_stats: dict[tuple[int, int], dict[str, int]] = {}
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
            fixture_stats[(player_id, fixture_id)] = {
                str(stat["identifier"]): int(stat.get("value", 0))
                for stat in explanation.get("stats", [])
            }

    # For a single fixture, the aggregate live categories provide sub-threshold
    # saves/DC/concessions too, even when a scoring explanation omits a zero-point
    # category. Do not distribute those aggregate counts across a DGW by guessing.
    event_categories = (
        "minutes",
        "goals_scored",
        "assists",
        "clean_sheets",
        "goals_conceded",
        "saves",
        "penalties_saved",
        "penalties_missed",
        "yellow_cards",
        "red_cards",
        "own_goals",
        "bonus",
        "defensive_contribution",
    )
    for item in snapshot["live"].get("elements", []):
        pid = int(item["id"])
        if pid not in players:
            continue
        schedule = tuple(f for f in fixtures if f.involves(players[pid].team_id))
        if len(schedule) != 1 or not schedule[0].started:
            continue
        key = (pid, schedule[0].id)
        aggregate = item.get("stats", {})
        observed = {
            category: int(aggregate[category])
            for category in event_categories
            if aggregate.get(category) is not None
        }
        fixture_stats[key] = fixture_stats.get(key, {}) | observed
        fixture_minutes[key] = observed.get("minutes", fixture_minutes.get(key, 0))

    # Zero-minute players often have no explain entry for a live fixture. Confirm a
    # zero contribution only when all official aggregated minutes AND points are
    # accounted for by the other per-fixture explanations (important in a DGW).
    explained_points = {
        int(item["id"]): sum(
            int(s.get("points", 0)) for x in item.get("explain", []) for s in x.get("stats", [])
        )
        for item in snapshot["live"].get("elements", [])
    }
    for fixture in fixtures:
        if not fixture.started or fixture.finished:
            continue
        for player in players.values():
            key = (player.id, fixture.id)
            if not fixture.involves(player.team_id) or key in fixture_stats:
                continue
            score = live_scores[player.id]
            other_minutes = sum(
                value for (pid, _), value in fixture_minutes.items() if pid == player.id
            )
            if score.minutes == other_minutes and score.points == explained_points.get(player.id):
                fixture_minutes[key] = 0
                fixture_stats[key] = {"minutes": 0}

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
                official_points=_optional_int(history.get("points")),
            )
        )

    fetched_at = _parse_datetime(snapshot.get("fetched_at")) or datetime.now(UTC)
    confirmed = {}
    for row in snapshot.get("confirmed_appearances", []):
        observed = _parse_datetime(row["confirmed_at"])
        if (
            observed is None
            or observed.tzinfo is None
            or row["status"] not in {"starter", "bench", "absent"}
        ):
            raise ValueError("Invalid saved confirmed appearance.")
        appearance = ConfirmedAppearance(**{**row, "confirmed_at": observed})
        if appearance.player_id not in players or not any(
            f.id == appearance.fixture_id and f.involves(players[appearance.player_id].team_id)
            for f in fixtures
        ):
            raise ValueError("Saved lineup mapping does not match the fixture roster.")
        confirmed[(appearance.player_id, appearance.fixture_id)] = appearance
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
        fixture_stats=fixture_stats,
        team_goal_rates=_team_goal_rates(snapshot.get("season_fixtures", [])),
        event_final=bool(
            next(
                (
                    e.get("finished")
                    for e in bootstrap.get("events", [])
                    if int(e["id"]) == gameweek
                ),
                False,
            )
        ),
        raw=snapshot,
        provenance="official_fpl",
        confirmed_appearances=confirmed,
        forecast_context=snapshot.get("forecast_context", {}),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _team_goal_rates(fixtures: list[dict[str, Any]]) -> dict[int, tuple[float, float]]:
    """Normalise official season scorelines before shared statistical estimation."""
    return estimate_team_goal_rates(
        TeamMatchResult(
            home_team_id=int(f["team_h"]),
            away_team_id=int(f["team_a"]),
            home_goals=int(f["team_h_score"]),
            away_goals=int(f["team_a_score"]),
        )
        for f in fixtures
        if f.get("finished")
        and f.get("team_h_score") is not None
        and f.get("team_a_score") is not None
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def parse_appearance_history(
    payload: dict[str, Any], completed_ids: set[int]
) -> tuple[MatchAppearance, ...]:
    """Only fixtures completed in the original generation; no hindsight enrichment."""
    rows = []
    for item in payload.get("history", []):
        fid = int(item["fixture"])
        if fid not in completed_ids:
            continue
        minutes = int(item.get("minutes", 0))
        if not 0 <= minutes <= 120:
            continue
        start = item.get("starts")
        rows.append(
            MatchAppearance(
                fid, int(item["round"]), min(90, minutes), bool(start) if start in (0, 1) else None
            )
        )
    return tuple(sorted(rows, key=lambda r: (r.gameweek, r.fixture_id)))
