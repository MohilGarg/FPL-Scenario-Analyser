from __future__ import annotations

import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic
from typing import Any

from ..api import (
    FPLAPIError,
    FPLLeagueSizeError,
    FPLNotFoundError,
    _current_gameweek,
    state_from_snapshot,
)
from ..cache import TTLCache
from ..exposures import remaining_effective_multipliers
from ..service import AnalysisService
from .calculations import (
    current_squads,
    enrich_season,
    head_to_head,
    scoring_detail,
    season_summary,
)

HISTORY_TTL_SECONDS = 24 * 60 * 60
SCORE_HISTORY_TTL_SECONDS = 15 * 60
LIVE_TTL_SECONDS = 90
DETAIL_BUDGET_SECONDS = 240
MAX_CONCURRENT_FETCHES = 4
MAX_ACTIVE_JOBS = 2

DEFINITIONS = [
    "Season statistics cover completed Gameweeks for today's league members, not membership at each past deadline.",
    "Effective weekly score = official event points minus that event's transfer deduction. Cumulative points sum the available completed weeks, not overall FPL rank.",
    "Tied scores share competition rank. Every tied lowest scorer counts as last; bottom three includes all ties at the third-lowest score. These descriptive counts do not change with the live scenario tie setting.",
    "Last-place margin is the second-lowest manager score minus the lowest, including zero for a tie. Incomplete league weeks have no ranks, last-place counts or margins.",
    "Bench, captaincy and positional totals use only reconciled final squads. Unused bench excludes autosubbed players and is zero on Bench Boost. Bench Boost contribution is counted points from slots 12–15.",
    "Captain points include the whole multiplied contribution; additional captain points count only the extra multiplier (8 base points: 16 total, +8 additional; Triple Captain: 24 total, +16 additional). A vice-captain takeover is credited to the effective captain.",
    "Position and league-player contributions include final captaincy, autosubs and Bench Boost. Transfer deductions remain separate. DGW event points are counted once.",
    "Standard deviation describes score variation; lower means more consistent, not better. A dash means unavailable or not applicable, never an assumed zero.",
]


class AnalyticsService:
    """Bounded in-memory public-data cache and lazy historical enrichment.

    One coalesced job per league/source generation, up to two concurrent jobs and
    four upstream requests globally. Completed squads/live resources last a day;
    score history lasts 15 minutes. No login, database or scheduled worker needed.
    """

    def __init__(self, analysis: AnalysisService | None = None) -> None:
        self.analysis = analysis or AnalysisService()
        self.client = self.analysis.client
        self._live: TTLCache[tuple, Any] = TTLCache(LIVE_TTL_SECONDS, 64)
        self._histories: TTLCache[tuple, Any] = TTLCache(SCORE_HISTORY_TTL_SECONDS, 256)
        self._historical: TTLCache[tuple, Any] = TTLCache(HISTORY_TTL_SECONDS, 2048)
        self._summaries: TTLCache[int, dict] = TTLCache(LIVE_TTL_SECONDS, 16)
        self._results: TTLCache[tuple, dict] = TTLCache(HISTORY_TTL_SECONDS, 4)
        self._partial: TTLCache[tuple, dict] = TTLCache(300, 4)
        self._locks = tuple(Lock() for _ in range(32))
        self._summary_locks = tuple(Lock() for _ in range(16))
        self._upstream = BoundedSemaphore(MAX_CONCURRENT_FETCHES)
        self._jobs_lock = Lock()
        self._jobs: dict[tuple, dict] = {}

    def _resource(
        self,
        path: str,
        *,
        season: str = "",
        historical: bool = False,
        history: bool = False,
        generation: tuple = (),
    ) -> Any:
        cache = self._historical if historical else self._histories if history else self._live
        key = (season, generation, path)
        value = cache.get(key)
        if value is not None:
            return value
        with self._locks[hash(key) % len(self._locks)]:
            value = cache.get(key)
            if value is None:
                with self._upstream:
                    value = self.client._get(path)
                if historical and path.startswith("event/") and path.endswith("/live/"):
                    # Only event totals are needed, not thousands of per-fixture explain rows.
                    value = {
                        "elements": [
                            {
                                "id": p["id"],
                                "stats": {"total_points": p.get("stats", {}).get("total_points")},
                            }
                            for p in value.get("elements", [])
                        ]
                    }
                cache.set(key, value)
            return value

    def summary(self, league_id: int) -> dict:
        if league_id <= 0:
            raise ValueError("League ID must be positive")
        cached = self._summaries.get(league_id)
        if cached is not None:
            return copy.deepcopy(cached)
        # A separate league lock coalesces all history fetches, not just each endpoint.
        with self._summary_locks[league_id % len(self._summary_locks)]:
            cached = self._summaries.get(league_id)
            if cached is not None:
                return copy.deepcopy(cached)
            result = self._build_summary(league_id)
            self._summaries.set(league_id, result)
            return copy.deepcopy(result)

    def _build_summary(self, league_id: int) -> dict:
        bootstrap = self._resource("bootstrap-static/")
        events = bootstrap.get("events", [])
        season = str(events[0].get("deadline_time", "unknown")) if events else "unknown"
        completed = sorted(e["id"] for e in events if e.get("finished"))
        members = []
        name = str(league_id)
        for page in range(1, 101):
            standings = self._resource(
                f"leagues-classic/{league_id}/standings/?page_standings={page}"
            )
            name = standings.get("league", {}).get("name", name)
            block = standings.get("standings", {})
            members.extend(
                {"entry_id": int(r["entry"]), "name": r["player_name"], "team": r["entry_name"]}
                for r in block.get("results", [])
            )
            if len(members) > self.analysis.max_league_entries or (
                block.get("has_next") and len(members) >= self.analysis.max_league_entries
            ):
                raise FPLLeagueSizeError(
                    f"Analytics supports at most {self.analysis.max_league_entries} managers."
                )
            if not block.get("has_next"):
                break
        else:
            raise FPLAPIError("League pagination exceeded its limit")
        if not members:
            raise FPLNotFoundError("No managers found in that public classic league")

        def fetch_history(member: dict) -> tuple[int, dict, str | None]:
            try:
                return (
                    member["entry_id"],
                    self._resource(
                        f"entry/{member['entry_id']}/history/",
                        season=season,
                        history=True,
                        generation=tuple(completed),
                    ),
                    None,
                )
            except FPLAPIError:
                return member["entry_id"], {}, f"Score history unavailable for {member['name']}."

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_FETCHES) as pool:
            fetched = list(pool.map(fetch_history, members))
        result = season_summary(
            members, {entry: history for entry, history, _ in fetched}, completed
        )
        current_gw = _current_gameweek(bootstrap)
        current_event = next(e for e in events if e["id"] == current_gw)
        missing = [
            f"GW{w['gameweek']}: no published score for "
            + ", ".join(m["name"] for m in w["unavailable_members"])
            + ". This week is not ranked; a member may have started FPL later."
            for w in result["gameweeks"]
            if w["unavailable_members"]
        ]
        result.update(
            {
                "api_version": 1,
                "league": {
                    "id": league_id,
                    "name": name,
                    "manager_count": len(members),
                    "current_gameweek": current_gw,
                    "current_status": "Complete"
                    if current_event.get("finished")
                    else "Upcoming"
                    if str(current_event.get("deadline_time", "")) > datetime.now(UTC).isoformat()
                    else "In progress",
                    "season": season,
                    "completed_gameweeks": completed,
                },
                "fetched_at": datetime.now(UTC).isoformat(),
                "warnings": [error for _, _, error in fetched if error] + missing,
                "definitions": DEFINITIONS,
                "cache": {
                    "live_seconds": LIVE_TTL_SECONDS,
                    "scores_seconds": SCORE_HISTORY_TTL_SECONDS,
                    "historical_seconds": HISTORY_TTL_SECONDS,
                },
            }
        )
        return result

    @staticmethod
    def _key(summary: dict) -> tuple:
        # Include scores/chips in the source identity so official corrections produce a new job.
        return (
            summary["league"]["id"],
            summary["league"]["season"],
            tuple(summary["league"]["completed_gameweeks"]),
            tuple(
                (
                    m["entry_id"],
                    tuple(
                        (r["gameweek"], r["raw"], r["hits"], r["transfers"], tuple(r["chips"]))
                        for r in m["history"]
                    ),
                )
                for m in summary["managers"]
            ),
        )

    def detail_status(self, league_id: int) -> dict:
        summary = self.summary(league_id)
        key = self._key(summary)
        result = self._results.get(key) or self._partial.get(key)
        if result is not None:
            return {
                "status": "ready",
                "progress": result["progress"],
                "coverage": result["detail_coverage"],
                "warnings": result["warnings"],
            }
        with self._jobs_lock:
            if key in self._jobs:
                return copy.deepcopy(self._jobs[key])
            if len(self._jobs) >= MAX_ACTIVE_JOBS:
                return {
                    "status": "busy",
                    "message": "Historical workers are busy. Retry shortly.",
                    "retry_after_seconds": 5,
                }
            total = summary["coverage"]["scores"]
            status = {
                "status": "loading",
                "progress": {
                    "done": 0,
                    "total": total,
                    "managers": len(summary["managers"]),
                    "gameweeks": len(summary["gameweeks"]),
                },
            }
            self._jobs[key] = status
            Thread(
                target=self._enrich, args=(key, summary), daemon=True, name=f"analytics-{league_id}"
            ).start()
            return copy.deepcopy(status)

    def _enrich(self, key: tuple, summary: dict) -> None:
        deadline = monotonic() + DETAIL_BUDGET_SECONDS
        season = summary["league"]["season"]
        details = {}
        errors = []
        try:
            bootstrap = self._resource("bootstrap-static/")
            players = {p["id"]: p for p in bootstrap.get("elements", [])}
            work = [(m["entry_id"], r) for m in summary["managers"] for r in m["history"]]

            def fetch_detail(item: tuple[int, dict]) -> tuple[tuple, dict | None, str | None]:
                entry, row = item
                gw = row["gameweek"]
                try:
                    if monotonic() >= deadline:
                        raise ValueError(
                            "Time budget reached; some historical squads remain unavailable"
                        )
                    picks = self._resource(
                        f"entry/{entry}/event/{gw}/picks/", season=season, historical=True
                    )
                    live = self._resource(f"event/{gw}/live/", season=season, historical=True)
                    if picks.get("entry_history", {}).get("points") != row["raw"]:
                        raise ValueError("Score history and final picks disagree")
                    detail = scoring_detail(picks, live, players)
                    return (entry, gw), detail, None
                except (FPLAPIError, KeyError, TypeError, ValueError) as exc:
                    message = (
                        str(exc)
                        if not isinstance(exc, FPLAPIError)
                        else "Historical FPL resource unavailable"
                    )
                    return (entry, gw), None, f"{row['name']}, GW{gw}: {message}."
                finally:
                    with self._jobs_lock:
                        self._jobs[key]["progress"]["done"] += 1

            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_FETCHES) as pool:
                for item_key, detail, error in pool.map(fetch_detail, work):
                    if detail is not None:
                        details[item_key] = detail
                    if error:
                        errors.append(error)
            enriched = enrich_season(summary, details)
            enriched["warnings"].extend(errors)
            enriched["progress"] = {
                "done": len(work),
                "total": len(work),
                "managers": len(summary["managers"]),
                "gameweeks": len(summary["gameweeks"]),
            }
            (self._partial if errors else self._results).set(key, enriched)
        except Exception:
            logging.getLogger(__name__).exception("Historical analytics worker failed")
            # A failed worker must never leave the UI polling an orphaned loading job.
            enriched = enrich_season(summary, {})
            enriched["warnings"].append(
                "Historical detail could not be assembled. Retry after five minutes; score history is still available."
            )
            enriched["progress"] = {"done": 0, "total": summary["coverage"]["scores"]}
            self._partial.set(key, enriched)
        finally:
            with self._jobs_lock:
                self._jobs.pop(key, None)

    def section(
        self,
        league_id: int,
        section: str,
        *,
        entry_id: int | None = None,
        player_id: int | None = None,
        gameweek: int | None = None,
        a: int | None = None,
        b: int | None = None,
    ) -> dict:
        summary = self.summary(league_id)
        key = self._key(summary)
        enriched = self._results.get(key) or self._partial.get(key)
        source = copy.deepcopy(enriched or summary)
        # Reuse completed-season detail without freezing the header when a new live GW starts.
        source["league"] = summary["league"]
        source["fetched_at"] = summary["fetched_at"]
        meta = {
            k: source[k] for k in ("league", "fetched_at", "coverage", "warnings", "definitions")
        }
        meta["detail_coverage"] = source.get("detail_coverage")
        if section == "summary":
            # Avoid repeating all histories and final squads in the initial response.
            return {
                **meta,
                "totals": source["totals"],
                "distribution": source["distribution"],
                "gameweeks": [
                    {k: v for k, v in w.items() if k != "rows"} for w in source["gameweeks"]
                ],
                "managers": [
                    {k: v for k, v in m.items() if k != "history"} for m in source["managers"]
                ],
                "captaincy": source.get("captaincy"),
                "cache": source["cache"],
            }
        if section == "gameweeks":
            weeks = source["gameweeks"]
            if gameweek is not None:
                weeks = [w for w in weeks if w["gameweek"] == gameweek]
                if not weeks:
                    raise FPLNotFoundError("Choose a completed Gameweek in this season")
            return {**meta, "gameweeks": weeks}
        if section in ("managers", "manager"):
            managers = source["managers"]
            if entry_id is not None:
                managers = [m for m in managers if m["entry_id"] == entry_id]
                if not managers:
                    raise FPLNotFoundError("That manager is not in this league")
            return {
                **meta,
                "managers": managers,
                "trends": [
                    {"gameweek": w["gameweek"], "average": w["average"], "bottom": w["bottom"]}
                    for w in source["gameweeks"]
                ],
            }
        if section == "head-to-head":
            ids = {m["entry_id"] for m in source["managers"]}
            if a not in ids or b not in ids:
                raise FPLNotFoundError("Choose two managers from this league")
            return {**meta, **head_to_head(source, a, b)}
        if section == "player":
            player = next(
                (p for p in source.get("contributions", []) if p["id"] == player_id), None
            )
            if player is None:
                raise FPLNotFoundError("No verified historical ownership for this player")
            rows = []
            for week in source["ownership_history"]:
                owned = next((p for p in week["players"] if p["id"] == player_id), {})
                rows.append(
                    {
                        "gameweek": week["gameweek"],
                        "coverage": week["coverage"],
                        "owners": owned.get("owned", 0) if week["coverage"] else None,
                        "captains": owned.get("captains", 0) if week["coverage"] else None,
                    }
                )
            return {**meta, "player": player, "history": rows}
        if section == "players":
            current = self.current(league_id)
            return {
                **meta,
                "current": current,
                "contributions": source.get("contributions"),
                "captaincy": source.get("captaincy"),
                "ownership_history": [
                    {k: v for k, v in w.items() if k != "players"}
                    for w in source.get("ownership_history", [])
                ],
            }
        raise ValueError("Unknown analytics section")

    def current(self, league_id: int) -> dict:
        try:
            self.analysis.analyse_league(league_id, include_scenarios=False)
            snapshot = self.analysis.cached_snapshot(league_id)
            if snapshot is None:
                raise FPLAPIError("Current snapshot expired; retry")
            state = state_from_snapshot(snapshot)
            players = {p["id"]: p for p in snapshot["bootstrap"]["elements"]}
            members = [{"entry_id": m.entry_id, "name": m.manager_name} for m in state.managers]
            complete = next(
                (
                    event.get("finished", False)
                    for event in snapshot["bootstrap"]["events"]
                    if event["id"] == state.gameweek
                ),
                False,
            )
            weights = (
                {
                    m.entry_id: remaining_effective_multipliers(
                        m, state.players, state.live_scores, state.team_complete()
                    )
                    for m in state.managers
                }
                if not complete
                else None
            )
            result = current_squads(
                members, {int(k): v for k, v in snapshot["picks"].items()}, players, weights
            )
            result.update(
                {
                    "gameweek": state.gameweek,
                    "fetched_at": snapshot["fetched_at"],
                    "available": True,
                }
            )
            result["definition"] += (
                " During a live GW these are provisional: pending captains are included if they play; future absences can change the lineup."
            )
            return result
        except FPLAPIError:
            return {
                "available": False,
                "message": "Current squads are not publicly available right now. Historical statistics are unaffected.",
            }
