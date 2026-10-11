from __future__ import annotations

import copy
import os
import sqlite3
from threading import Lock
from typing import Any

from .analysis import analyse_state, analysis_to_dict
from .api import FPLClient, FPLGameweekUnavailable, FPLNotFoundError, state_from_snapshot
from .cache import TTLCache
from .comparison import compare_managers
from .demo import DemoMode, demo_state
from .forecast import (
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    Condition,
    FixtureModel,
    ScenarioConflict,
    canonical_conditions,
    outlook,
)
from .forecasting.service import ForecastEnricher
from .history import RiskHistoryStore, configured_history
from .models import LeagueState
from .settings import AnalysisSettings
from .state import current_standings

DEFAULT_CACHE_TTL_SECONDS = 90.0
DEFAULT_MAX_LEAGUE_ENTRIES = 50
SCENARIO_SOURCE_TTL_SECONDS = 600.0


class AnalysisService:
    def __init__(
        self,
        client: FPLClient | None = None,
        *,
        cache_ttl_seconds: float | None = None,
        max_league_entries: int = DEFAULT_MAX_LEAGUE_ENTRIES,
        history: RiskHistoryStore | None = None,
    ) -> None:
        ttl = _cache_ttl_from_environment() if cache_ttl_seconds is None else cache_ttl_seconds
        self.client = client or FPLClient()
        self.enricher = ForecastEnricher(self.client)
        self.history = history or configured_history()
        self.max_league_entries = max_league_entries
        self._snapshots: TTLCache[tuple, dict[str, Any]] = TTLCache(ttl, max_items=16)
        self._generations: TTLCache[tuple, dict[str, Any]] = TTLCache(
            max(ttl, SCENARIO_SOURCE_TTL_SECONDS), max_items=32
        )
        self._analyses: TTLCache[tuple, dict[str, Any]] = TTLCache(ttl, max_items=64)
        self._demos: TTLCache[tuple, dict[str, Any]] = TTLCache(ttl, max_items=16)
        self._fetch_locks = tuple(Lock() for _ in range(16))
        self._analysis_locks = tuple(Lock() for _ in range(16))
        self._states: TTLCache[tuple, LeagueState] = TTLCache(
            max(ttl, SCENARIO_SOURCE_TTL_SECONDS), max_items=32
        )
        self._outlooks: TTLCache[tuple, dict[str, Any]] = TTLCache(
            max(ttl, SCENARIO_SOURCE_TTL_SECONDS), max_items=64
        )
        self._outlook_locks = tuple(Lock() for _ in range(8))

    def cached_snapshot(self, league_id: int) -> dict[str, Any] | None:
        """Share the current immutable source with descriptive analytics, without a refetch."""
        return self._snapshots.get((league_id, None))

    def analyse_league(
        self,
        league_id: int,
        settings: AnalysisSettings | None = None,
        *,
        gameweek: int | None = None,
        include_scenarios: bool = True,
        candidate_id: int | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(league_id, bool) or league_id <= 0:
            raise ValueError("League ID must be a positive integer")
        settings = settings or AnalysisSettings()
        snapshot = (
            self._generations.get((league_id, gameweek, source))
            if source
            else self._snapshots.get((league_id, gameweek))
        )
        if source and snapshot is None:
            raise FPLGameweekUnavailable(
                "This scores update has expired. Refresh the league to generate scenarios for the latest scores."
            )
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
                    self._generations.set(
                        (league_id, gameweek, snapshot.get("fetched_at")), snapshot
                    )

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
        with self._analysis_locks[hash(cache_key) % len(self._analysis_locks)]:
            cached = self._analyses.get(cache_key)
            if cached is not None:
                response = copy.deepcopy(cached)
                response["cache"] = {"hit": True, "ttl_seconds": self._snapshots.ttl_seconds}
                return response
            state = self._state(snapshot, gameweek)
            self.enricher.prime(state)
            if candidate_id is not None and not any(
                m.entry_id == candidate_id for m in state.managers
            ):
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

    def _state(self, snapshot: dict[str, Any], gameweek: int | None) -> LeagueState:
        key = (snapshot["league_id"], gameweek, snapshot.get("fetched_at"))
        state = self._states.get(key)
        if state is None:
            state = state_from_snapshot(snapshot)
            self._states.set(key, state)
        return state

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
            state = self._state(snapshot, gameweek)
        managers = {m.entry_id: m for m in state.managers}
        if a not in managers or b not in managers:
            raise FPLNotFoundError("Choose managers from this league")
        scores = {row.manager.entry_id: row.effective_score for row in current_standings(state)}
        return compare_managers(
            state, managers[a], managers[b], scores, allow_tied_last=allow_tied_last
        )

    def forecast(
        self,
        league_id: int,
        *,
        source: str,
        gameweek: int | None = None,
        demo: DemoMode | None = None,
        allow_tied_last: bool = True,
        conditions: tuple[Condition, ...] = (),
        samples: int = DEFAULT_SAMPLES,
    ) -> dict[str, Any]:
        """Coalesce and cache computation against an immutable scores generation.

        A conditional outlook shares baseline seed/count, reducing comparison noise.
        Source is mandatory: an expired generation must never silently fetch new scores.
        """
        if demo:
            state = demo_state(demo)
            if state.fetched_at.isoformat() != source:
                raise FPLGameweekUnavailable("Demo generation changed; reload this Gameweek.")
        else:
            snapshot = self._generations.get((league_id, gameweek, source))
            if snapshot is None:
                raise FPLGameweekUnavailable(
                    "This scores update expired. Refresh before exploring outcomes."
                )
        if len(conditions) > 12:
            raise ScenarioConflict("Use at most twelve conditions in one scenario.")
        conditions = canonical_conditions(conditions)
        key = (league_id, gameweek, demo, source, allow_tied_last, conditions, samples)
        cached = self._outlooks.get(key)
        if cached is not None:
            response = copy.deepcopy(cached)
            response["cache"] = {"hit": True}
            return response
        if not demo:
            state = self.enricher.prepare(self._state(snapshot, gameweek))
        # Reject contradictory input before spending time on a missing baseline.
        if conditions:
            FixtureModel(state, conditions)
        baseline = (
            self.forecast(
                league_id,
                source=source,
                gameweek=gameweek,
                demo=demo,
                allow_tied_last=allow_tied_last,
                samples=samples,
            )
            if conditions
            else None
        )
        with self._outlook_locks[hash(key) % len(self._outlook_locks)]:
            cached = self._outlooks.get(key)
            if cached is None:
                # Baseline is computed first so all displayed deltas use the same sample count.
                cached = outlook(
                    state,
                    samples=samples,
                    seed=DEFAULT_SEED,
                    allow_tied_last=allow_tied_last,
                    conditions=conditions,
                )
                if baseline:
                    base_rows = {r["entry_id"]: r for r in baseline["managers"]}
                    for row in cached["managers"]:
                        base = base_rows[row["entry_id"]]
                        row["baseline_projected_score"] = base["projected_score"]
                        row["score_change"] = round(
                            row["projected_score"] - base["projected_score"], 2
                        )
                        row["baseline_last_probability"] = base["last_probability"]
                        row["risk_change"] = round(
                            row["last_probability"] - base["last_probability"], 4
                        )
                    cached["comparison_note"] = (
                        "Forced-event intervention versus unmodified simulation, not a Bayesian conditional probability. "
                        "Other events remain sampled; changes include model and sampling uncertainty."
                    )
                cached["cache"] = {"hit": False}
                if not demo and not conditions:
                    try:
                        self.history.record(state, cached)
                    except (OSError, ValueError, sqlite3.Error):
                        cached["history_recording_warning"] = (
                            "This forecast could not be durably recorded; risk estimates remain available."
                        )
                    cached["risk_history"] = self.history.read(
                        state.league_id, state.gameweek, source=source
                    )
                elif demo:
                    cached["risk_history"] = {
                        "status": "demo",
                        "recording_enabled": False,
                        "series": [],
                        "note": "Demo forecasts are not recorded as live probability history.",
                    }
                self._outlooks.set(key, cached)
            else:
                cached = copy.deepcopy(cached)
                cached["cache"] = {"hit": True}
        return copy.deepcopy(cached)


def _cache_ttl_from_environment() -> float:
    value = os.getenv("FPL_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        ttl = float(value)
    except ValueError as exc:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be numeric") from exc
    if ttl <= 0:
        raise ValueError("FPL_CACHE_TTL_SECONDS must be positive")
    return ttl
