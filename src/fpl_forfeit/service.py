from __future__ import annotations

import copy
import os
from threading import Lock
from typing import Any

from .analysis import analyse_state, analysis_to_dict
from .api import FPLClient, FPLNotFoundError, state_from_snapshot
from .cache import TTLCache
from .comparison import compare_managers
from .demo import DemoMode, demo_state
from .settings import AnalysisSettings
from .state import current_standings

DEFAULT_CACHE_TTL_SECONDS = 90.0
DEFAULT_MAX_LEAGUE_ENTRIES = 50


class AnalysisService:
    def __init__(
        self,
        client: FPLClient | None = None,
        *,
        cache_ttl_seconds: float | None = None,
        max_league_entries: int = DEFAULT_MAX_LEAGUE_ENTRIES,
    ) -> None:
        ttl = _cache_ttl_from_environment() if cache_ttl_seconds is None else cache_ttl_seconds
        self.client = client or FPLClient()
        self.max_league_entries = max_league_entries
        self._snapshots: TTLCache[tuple, dict[str, Any]] = TTLCache(ttl, max_items=16)
        self._analyses: TTLCache[tuple, dict[str, Any]] = TTLCache(ttl, max_items=64)
        self._demos: TTLCache[tuple, dict[str, Any]] = TTLCache(ttl, max_items=16)
        self._fetch_locks = tuple(Lock() for _ in range(16))

    def analyse_league(
        self,
        league_id: int,
        settings: AnalysisSettings | None = None,
        *,
        gameweek: int | None = None,
        include_scenarios: bool = True,
        candidate_id: int | None = None,
    ) -> dict[str, Any]:
        if isinstance(league_id, bool) or league_id <= 0:
            raise ValueError("League ID must be a positive integer")
        settings = settings or AnalysisSettings()
        snapshot = self._snapshots.get((league_id, gameweek))
        snapshot_cache_hit = snapshot is not None
        if snapshot is None:
            # Coalesce simultaneous refreshes of the same league instead of duplicating FPL calls.
            with self._fetch_locks[hash((league_id, gameweek)) % len(self._fetch_locks)]:
                snapshot = self._snapshots.get((league_id, gameweek))
                snapshot_cache_hit = snapshot is not None
                if snapshot is None:
                    snapshot = self.client.fetch_snapshot(
                        league_id, gameweek=gameweek, max_entries=self.max_league_entries
                    )
                    self._snapshots.set((league_id, gameweek), snapshot)

        # Derived entries must never outlive their source generation. A late Show more request
        # must not mix an old candidate result with a freshly refreshed league summary.
        cache_key = (
            league_id,
            gameweek,
            snapshot.get("fetched_at"),
            settings,
            include_scenarios,
            candidate_id,
        )
        cached = self._analyses.get(cache_key)
        if cached is not None:
            response = copy.deepcopy(cached)
            response["cache"] = {"hit": True, "ttl_seconds": self._snapshots.ttl_seconds}
            return response
        state = state_from_snapshot(snapshot)
        if candidate_id is not None and not any(m.entry_id == candidate_id for m in state.managers):
            raise FPLNotFoundError("That manager is not in this league")
        result = analysis_to_dict(
            analyse_state(
                state, settings, include_scenarios=include_scenarios, candidate_id=candidate_id
            )
        )
        result["cache"] = {
            "hit": snapshot_cache_hit,
            "ttl_seconds": self._snapshots.ttl_seconds,
        }
        self._analyses.set(cache_key, result)
        return copy.deepcopy(result)

    def analyse_demo(
        self,
        mode: DemoMode,
        settings: AnalysisSettings | None = None,
        *,
        include_scenarios: bool = True,
        candidate_id: int | None = None,
    ) -> dict[str, Any]:
        settings = settings or AnalysisSettings()
        cache_key = (mode, settings, include_scenarios, candidate_id)
        cached = self._demos.get(cache_key)
        if cached is not None:
            response = copy.deepcopy(cached)
            response["cache"] = {"hit": True, "ttl_seconds": self._demos.ttl_seconds}
            return response
        result = analysis_to_dict(
            analyse_state(
                demo_state(mode),
                settings,
                include_scenarios=include_scenarios,
                candidate_id=candidate_id,
            )
        )
        result["demo"] = {"active": True, "mode": mode}
        result["cache"] = {"hit": False, "ttl_seconds": self._demos.ttl_seconds}
        self._demos.set(cache_key, result)
        return copy.deepcopy(result)

    def compare(
        self,
        league_id: int,
        a: int,
        b: int,
        *,
        gameweek: int | None = None,
        demo: DemoMode | None = None,
        allow_tied_last: bool = True,
    ) -> dict[str, Any]:
        if demo:
            state = demo_state(demo)
        else:
            self.analyse_league(league_id, gameweek=gameweek, include_scenarios=False)
            snapshot = self._snapshots.get((league_id, gameweek))
            if snapshot is None:
                snapshot = self.client.fetch_snapshot(
                    league_id, gameweek=gameweek, max_entries=self.max_league_entries
                )
            state = state_from_snapshot(snapshot)
        managers = {m.entry_id: m for m in state.managers}
        if a not in managers or b not in managers:
            raise FPLNotFoundError("Choose managers from this league")
        scores = {row.manager.entry_id: row.effective_score for row in current_standings(state)}
        return compare_managers(
            state, managers[a], managers[b], scores, allow_tied_last=allow_tied_last
        )


def _cache_ttl_from_environment() -> float:
    value = os.getenv("FPL_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        ttl = float(value)
    except ValueError as exc:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be numeric") from exc
    if ttl <= 0:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be positive")
    return ttl
