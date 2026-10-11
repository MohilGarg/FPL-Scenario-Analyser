"""Optional authorised API-Football source; explicit IDs, bounded calls, fail-soft use."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.request import Request, urlopen

from .cache import TTLCache
from .models import ConfirmedAppearance, LeagueState


def normalise_lineups(
    state: LeagueState,
    metadata: dict[str, Any],
    lineups: list[dict[str, Any]],
    events: list[dict[str, Any]],
    mapping: dict[str, Any],
    observed_at: datetime,
) -> dict[tuple[int, int], ConfirmedAppearance]:
    """Validate provider fixture/team identity and explicit one-to-one player mappings."""
    provider_id = int(metadata["fixture"]["id"])
    fixture_ids = {int(k): int(v) for k, v in mapping["fixtures"].items()}
    player_ids = {int(k): int(v) for k, v in mapping["players"].items()}
    team_ids = {int(k): int(v) for k, v in mapping["teams"].items()}
    if len(set(player_ids.values())) != len(player_ids) or len(set(fixture_ids.values())) != len(
        fixture_ids
    ):
        raise ValueError("Mappings must be one-to-one, validated IDs.")
    fid = fixture_ids[provider_id]
    f = next(f for f in state.fixtures if f.id == fid)
    if int(metadata["league"]["id"]) != 39:
        raise ValueError("Provider fixture is not Premier League.")
    if (
        team_ids[int(metadata["teams"]["home"]["id"])],
        team_ids[int(metadata["teams"]["away"]["id"])],
    ) != (f.home_team_id, f.away_team_id):
        raise ValueError("Provider fixture teams do not match FPL.")
    kickoff = datetime.fromisoformat(metadata["fixture"]["date"])
    if f.kickoff_time is None or abs((kickoff - f.kickoff_time).total_seconds()) > 300:
        raise ValueError("Provider fixture kickoff does not match FPL.")
    if observed_at.tzinfo is None:
        raise ValueError("Lineup confirmation requires a timezone.")
    result = {}
    on_field = {}
    participant_teams = {}
    for team in lineups:
        fpl_team = team_ids[int(team["team"]["id"])]
        if not f.involves(fpl_team) or len(team.get("startXI", [])) != 11:
            raise ValueError("Only complete confirmed starting elevens are accepted.")
        seen = set()
        for status, key in (("starter", "startXI"), ("bench", "substitutes")):
            for row in team.get(key, []):
                pid = int(row["player"]["id"])
                if pid in seen:
                    raise ValueError("Duplicate player in confirmed lineup.")
                seen.add(pid)
                if pid in participant_teams:
                    raise ValueError("Player appears for both teams.")
                participant_teams[pid] = fpl_team
                on_field[pid] = status == "starter"
                if pid not in player_ids:
                    continue  # Unknown IDs are not guessed or silently treated as absent.
                fpl_id = player_ids[pid]
                if fpl_id not in state.players or state.players[fpl_id].team_id != fpl_team:
                    raise ValueError("Mapped player is not in this FPL team.")
                result[(fpl_id, fid)] = ConfirmedAppearance(
                    fpl_id,
                    fid,
                    pid,
                    provider_id,
                    status,
                    observed_at,
                    "api-football",
                    on_minute=0 if status == "starter" else None,
                )
    for event in sorted(events, key=lambda e: e.get("time", {}).get("elapsed", 0)):
        if event.get("type") != "subst":
            continue
        minute = int(event["time"]["elapsed"])
        if not 0 <= minute <= 90:
            continue
        pair = [int(event["player"]["id"]), int(event["assist"]["id"])]
        if (
            any(pid not in on_field for pid in pair)
            or participant_teams[pair[0]] != participant_teams[pair[1]]
            or on_field[pair[0]] == on_field[pair[1]]
        ):
            raise ValueError(
                "Substitution IDs do not identify an active player and a reserve from one team."
            )
        outgoing, incoming = pair if on_field[pair[0]] else pair[::-1]
        on_field[outgoing], on_field[incoming] = False, True
        for provider_pid, field in ((outgoing, "off_minute"), (incoming, "on_minute")):
            fpl_id = player_ids.get(provider_pid)
            key = (fpl_id, fid)
            if key in result:
                result[key] = replace(result[key], **{field: minute})
    return result


class APIFootballLineups:
    def __init__(self, key: str, mapping: dict[str, Any], *, daily_limit: int = 90) -> None:
        self._key, self.mapping, self.limit = key, mapping, min(90, max(1, daily_limit))
        self._cache: TTLCache[int, tuple] = TTLCache(600, max_items=128)
        self._lock = Lock()
        self._day, self._calls = "", 0

    def _get(self, path: str) -> dict[str, Any]:
        day = datetime.now(UTC).date().isoformat()
        if self._day != day:
            self._day, self._calls = day, 0
        if self._calls >= self.limit:
            raise ValueError("Provider daily request budget reached.")
        self._calls += 1
        request = Request(
            "https://v3.football.api-sports.io/" + path,
            headers={"x-apisports-key": self._key, "Accept": "application/json"},
        )
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
        if payload.get("errors"):
            raise ValueError("Provider denied or could not serve this request.")
        return payload

    def enrich(self, state: LeagueState) -> tuple[dict, dict]:
        confirmed, failures = {}, 0
        now = datetime.now(UTC)
        eligible = [
            f
            for f in state.fixtures
            if not f.finished
            and (
                f.started
                or f.kickoff_time
                and -900 <= (f.kickoff_time - now).total_seconds() <= 5400
            )
        ]
        with self._lock:  # Coalesce requests across leagues and enforce the shared budget.
            for f in eligible[:3]:
                try:
                    provider_id = next(
                        int(k) for k, v in self.mapping["fixtures"].items() if int(v) == f.id
                    )
                    raw = self._cache.get(provider_id)
                    if raw is None:
                        metadata = self._get(f"fixtures?id={provider_id}")["response"][0]
                        lineups = self._get(f"fixtures/lineups?fixture={provider_id}")["response"]
                        events = (
                            self._get(f"fixtures/events?fixture={provider_id}")["response"]
                            if f.started
                            else []
                        )
                        raw = metadata, lineups, events, now
                        self._cache.set(provider_id, raw)
                    confirmed.update(normalise_lineups(state, *raw[:3], self.mapping, raw[3]))
                except (OSError, ValueError, KeyError, IndexError, StopIteration, TypeError):
                    failures += 1
        return confirmed, {
            "lineup_source": "api-football" if confirmed else "official_fpl_only",
            "lineup_provider_failures": failures,
            "lineup_feed_verified": bool(confirmed),
        }


def configured_lineups() -> APIFootballLineups | None:
    if os.getenv("FPL_LINEUPS_AUTHORISED") != "1":
        return None
    key, path = os.getenv("FPL_LINEUPS_API_KEY"), os.getenv("FPL_LINEUPS_MAPPING_PATH")
    if not key or not path:
        return None
    try:
        return APIFootballLineups(key, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
