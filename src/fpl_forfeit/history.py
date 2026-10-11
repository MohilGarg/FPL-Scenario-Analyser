"""Durable, deduplicated observations; never manufacture a historical live forecast."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evaluation import prediction_record
from .models import LeagueState


class RiskHistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.unavailable = False
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as db:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS observations (league INTEGER, gameweek INTEGER, source TEXT, series TEXT, recorded TEXT, payload TEXT, UNIQUE(league,gameweek,source,series))"
                    )
                    db.execute(
                        "CREATE INDEX IF NOT EXISTS history_lookup ON observations(league,gameweek,recorded)"
                    )
            except (OSError, sqlite3.Error):
                self.path, self.unavailable = None, True

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, record: dict[str, Any]) -> bool:
        if not self.path:
            return False
        created = datetime.fromisoformat(record["recorded_at"])
        source = datetime.fromisoformat(record["source"])
        if (
            record.get("provenance") != "recorded"
            or record.get("data_provenance") != "official_fpl"
            or not created.tzinfo
            or not source.tzinfo
            or not 0 <= (created - source).total_seconds() <= 600
        ):
            raise ValueError("Only contemporaneously recorded observations can enter risk history.")
        if any(row["last_probability"] is None for row in record["managers"]):
            raise ValueError("Risk history requires a full probability forecast.")
        ids = [row["entry_id"] for row in record["managers"]]
        if len(ids) != len(set(ids)) or any(
            not 0 <= row["last_probability"] <= 1 for row in record["managers"]
        ):
            raise ValueError("Risk history requires unique members and valid probabilities.")
        record = {
            **record,
            "source": source.astimezone(UTC).isoformat(),
            "recorded_at": created.astimezone(UTC).isoformat(),
        }
        settings = {k: record[k] for k in ("model_version", "seed", "simulations", "tie_rule")}
        settings["manager_ids"] = sorted(row["entry_id"] for row in record["managers"])
        series = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
        record = {**record, "configuration": settings, "series_id": series}
        with self._connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)",
                (
                    record["league_id"],
                    record["gameweek"],
                    record["source"],
                    series,
                    record["recorded_at"],
                    json.dumps(record),
                ),
            )
            db.execute(
                "DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations WHERE league=? AND gameweek=? ORDER BY recorded DESC LIMIT -1 OFFSET 1000)",
                (record["league_id"], record["gameweek"]),
            )
            db.execute(
                "DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations ORDER BY recorded DESC LIMIT -1 OFFSET 30000)"
            )
        return result.rowcount > 0

    def record(self, state: LeagueState, result: dict[str, Any]) -> bool:
        if (
            not self.path
            or state.provenance != "official_fpl"
            or state.raw.get("historical")
            or result.get("conditions")
        ):
            return False
        return self.append(prediction_record(state, result=result))

    def read(self, league_id: int, gameweek: int, *, source: str | None = None) -> dict[str, Any]:
        if not self.path:
            return {
                "status": "storage_unavailable" if self.unavailable else "not_configured",
                "recording_enabled": False,
                "series": [],
                "note": "Durable risk recording is not configured on this server. Only forecasts actually saved at the time can appear here.",
            }
        try:
            with self._connect() as db:
                records = [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT payload FROM observations WHERE league=? AND gameweek=? AND (? IS NULL OR source<=?) ORDER BY source DESC,recorded DESC LIMIT 240",
                        (league_id, gameweek, source, source),
                    ).fetchall()
                ][::-1]
        except sqlite3.Error:
            return {
                "status": "storage_unavailable",
                "recording_enabled": False,
                "series": [],
                "note": "Recorded risk history is temporarily unavailable.",
            }
        series: dict[str, dict] = {}
        previous: dict[str, dict] = {}
        for record in records:
            identity = record["series_id"]
            if identity not in series:
                series[identity] = {
                    "series_id": identity,
                    "configuration": record["configuration"],
                    "observations": [],
                }
            last = previous.get(identity)
            annotations = []
            if last:
                old_f = {f["fixture_id"]: f for f in last.get("fixture_status", [])}
                for fixture in record.get("fixture_status", []):
                    old = old_f.get(fixture["fixture_id"])
                    if old and any(
                        fixture[k] != old[k]
                        for k in ("home_score", "away_score", "started", "finished")
                    ):
                        annotations.append(
                            {"fixture_id": fixture["fixture_id"], "before": old, "after": fixture}
                        )
                if record["coverage"] != last["coverage"]:
                    annotations.append({"kind": "input_coverage_changed"})
            old_players = (
                {p["player_id"]: p for p in last.get("player_observations", [])} if last else {}
            )
            changes = [
                {
                    "player_id": p["player_id"],
                    "points_change": p["points"] - old_players[p["player_id"]]["points"],
                    "minutes_change": p["minutes"] - old_players[p["player_id"]]["minutes"],
                }
                for p in record.get("player_observations", [])
                if p["player_id"] in old_players and p != old_players[p["player_id"]]
            ]
            old_rows = {r["entry_id"]: r for r in last["managers"]} if last else {}
            series[identity]["observations"].append(
                {
                    "recorded_at": record["recorded_at"],
                    "source": record["source"],
                    "coverage": record["coverage"],
                    "annotations": annotations,
                    "player_changes": changes,
                    "managers": [
                        {
                            **{
                                k: r[k]
                                for k in (
                                    "entry_id",
                                    "current_score",
                                    "projected_score",
                                    "last_probability",
                                )
                            },
                            "risk_change": round(
                                r["last_probability"] - old_rows[r["entry_id"]]["last_probability"],
                                4,
                            )
                            if old_rows
                            else None,
                        }
                        for r in record["managers"]
                    ],
                }
            )
            previous[identity] = record
        for group in series.values():
            peaks = {
                e: max(
                    r["last_probability"]
                    for observation in group["observations"]
                    for r in observation["managers"]
                    if r["entry_id"] == e
                )
                for e in group["configuration"]["manager_ids"]
            }
            group["contender_entry_ids"] = sorted(peaks, key=lambda e: (-peaks[e], e))[:4]
        return {
            "status": "ready" if records else "awaiting_observations",
            "recording_enabled": True,
            "series": list(series.values())[::-1],
            "note": "Actually recorded forecasts; incompatible models/settings/membership form separate series. Football and player-point changes between observations are context, not a causal allocation of risk movement. Sampling and model error remain.",
        }


def configured_history() -> RiskHistoryStore:
    path = os.getenv("FPL_RISK_HISTORY_PATH")
    return RiskHistoryStore(Path(path)) if path else RiskHistoryStore()
