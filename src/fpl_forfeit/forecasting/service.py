"""Bounded background retrieval; freeze enriched inputs per public source generation."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, replace
from datetime import datetime
from threading import BoundedSemaphore, Lock
from typing import Any

from ..api import FPLClient, parse_appearance_history
from ..cache import TTLCache
from ..lineups import configured_lineups
from ..models import LeagueState


class ForecastEnricher:
    def __init__(self, client: FPLClient) -> None:
        self.client = client
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="fpl-forecast-data")
        self.jobs: TTLCache[int, Future[dict[str, Any]]] = TTLCache(21600, max_items=1500)
        self.generations: TTLCache[tuple, LeagueState] = TTLCache(600, max_items=32)
        self.lineup_jobs: TTLCache[tuple, Future[tuple[dict, dict]]] = TTLCache(600, max_items=32)
        self.lineups = configured_lineups()
        self.lock = Lock()
        self.generation_locks = tuple(Lock() for _ in range(32))
        self.slots = BoundedSemaphore(48)

    def _key(self, state: LeagueState) -> tuple[int, int, datetime]:
        return state.league_id, state.gameweek, state.fetched_at

    def prime(self, state: LeagueState) -> None:
        if state.provenance != "official_fpl" or state.event_final or state.raw.get("historical"):
            return
        owned = {p.player_id for m in state.managers for p in m.picks}
        relevant = [
            pid for pid in owned if state.unfinished_fixtures_for_team(state.players[pid].team_id)
        ]
        captains = {
            p.player_id
            for m in state.managers
            for p in m.picks
            if p.is_captain or p.is_vice_captain
        }
        relevant.sort(
            key=lambda pid: (pid not in captains, -(state.players[pid].season_minutes or 0), pid)
        )
        with self.lock:
            if hasattr(self.client, "player_history"):
                for pid in relevant[:12]:
                    if self.jobs.get(pid) is None and self.slots.acquire(blocking=False):
                        job = self.executor.submit(self.client.player_history, pid)
                        job.add_done_callback(lambda _: self.slots.release())
                        self.jobs.set(pid, job)
            if self.lineups and self.lineup_jobs.get(self._key(state)) is None:
                self.lineup_jobs.set(
                    self._key(state), self.executor.submit(self.lineups.enrich, state)
                )

    def prepare(self, state: LeagueState) -> LeagueState:
        self.prime(state)
        with self.generation_locks[hash(self._key(state)) % len(self.generation_locks)]:
            cached = self.generations.get(self._key(state))
            if cached:
                return cached
            ids = {p.player_id for m in state.managers for p in m.picks}
            jobs = {pid: self.jobs.get(pid) for pid in ids}
            jobs = {pid: job for pid, job in jobs.items() if job is not None}
            if jobs:
                wait(jobs.values(), timeout=2)
            players, raw_histories = dict(state.players), {}
            completed = {
                int(f["id"]) for f in state.raw.get("season_fixtures", []) if f.get("finished")
            }
            for pid, job in jobs.items():
                if job.done() and job.exception() is None:
                    payload = job.result()
                    history = parse_appearance_history(payload, completed)
                    players[pid] = replace(players[pid], appearance_history=history)
                    raw_histories[str(pid)] = payload
            appearances, context = dict(state.confirmed_appearances), dict(state.forecast_context)
            job = self.lineup_jobs.get(self._key(state))
            if job and job.done() and job.exception() is None:
                appearances, external = job.result()
                context.update(external)
            context.update(
                {
                    "history_jobs_requested": len(jobs),
                    "history_jobs_pending": sum(not j.done() for j in jobs.values()),
                    "lineup_provider_configured": self.lineups is not None,
                }
            )
            enriched = replace(
                state,
                players=players,
                confirmed_appearances=appearances,
                forecast_context=context,
                raw={
                    **state.raw,
                    "player_histories": raw_histories,
                    "forecast_context": context,
                    "confirmed_appearances": [
                        {**asdict(a), "confirmed_at": a.confirmed_at.isoformat()}
                        for a in appearances.values()
                    ],
                }
                if state.raw and (raw_histories or appearances)
                else state.raw,
            )
            self.generations.set(self._key(state), enriched)
            return enriched
