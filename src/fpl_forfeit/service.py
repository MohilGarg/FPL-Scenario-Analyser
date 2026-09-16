from __future__ import annotations

import copy
import os
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
        self._snapshots: TTLCache[int, dict[str, Any]] = TTLCache(ttl)
        self._analyses: TTLCache[tuple[int, AnalysisSettings], dict[str, Any]] = TTLCache(ttl)

    def analyse_league(
        self, league_id: int, settings: AnalysisSettings | None = None
    ) -> dict[str, Any]:
        if isinstance(league_id, bool) or league_id <= 0:
            raise ValueError("League ID must be a positive integer")
        settings = settings or AnalysisSettings()
        cache_key = (league_id, settings)
        cached = self._analyses.get(cache_key)
        if cached is not None:
            response = copy.deepcopy(cached)
            response["cache"] = {"hit": True, "ttl_seconds": self._snapshots.ttl_seconds}
            return response

        snapshot = self._snapshots.get(league_id)
        snapshot_cache_hit = snapshot is not None
        if snapshot is None:
            snapshot = self.client.fetch_snapshot(league_id, max_entries=self.max_league_entries)
            self._snapshots.set(league_id, snapshot)

        result = analysis_to_dict(analyse_state(state_from_snapshot(snapshot), settings))
        result["cache"] = {
            "hit": snapshot_cache_hit,
            "ttl_seconds": self._snapshots.ttl_seconds,
        }
        self._analyses.set(cache_key, result)
        return copy.deepcopy(result)


def _cache_ttl_from_environment() -> float:
    value = os.getenv("FPL_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        ttl = float(value)
    except ValueError as exc:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be numeric") from exc
    if ttl <= 0:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be positive")
    return ttl
