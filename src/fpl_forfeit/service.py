from __future__ import annotations

import copy
import os
from threading import Lock
from typing import Any

from .analysis import analyse_state, analysis_to_dict
from .api import FPLClient, state_from_snapshot
from .cache import TTLCache
from .settings import AnalysisSettings

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
        self._fetch_locks = tuple(Lock() for _ in range(16))
        self._analysis_locks = tuple(Lock() for _ in range(16))

    def cached_snapshot(self, league_id: int) -> dict[str, Any] | None:
        """Share the current immutable source with descriptive analytics, without a refetch."""
        return self._snapshots.get((league_id, None))

    def analyse_league(
        self,
        league_id: int,
        settings: AnalysisSettings | None = None,
        *,
        gameweek: int | None = None,
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

        # Derived results must never outlive their source generation.
        cache_key = (league_id, gameweek, snapshot.get("fetched_at"), settings)
        hit = True
        cached = self._analyses.get(cache_key)
        if cached is None:
            with self._analysis_locks[hash(cache_key) % len(self._analysis_locks)]:
                cached = self._analyses.get(cache_key)
                if cached is None:
                    hit = snapshot_cache_hit
                    cached = analysis_to_dict(
                        analyse_state(state_from_snapshot(snapshot), settings)
                    )
                    self._analyses.set(cache_key, cached)
        response = copy.deepcopy(cached)
        response["cache"] = {"hit": hit, "ttl_seconds": self._snapshots.ttl_seconds}
        return response


def _cache_ttl_from_environment() -> float:
    value = os.getenv("FPL_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        ttl = float(value)
    except ValueError as exc:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be numeric") from exc
    if ttl <= 0:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be positive")
    return ttl
