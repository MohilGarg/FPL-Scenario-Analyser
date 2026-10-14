"""Private, dependency-free Ridge inference. Statistical draws are never replaced."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, RLock, Thread

from ..api import FPLClient, parse_ml_history
from ..forecasting.horizon import HorizonForecast
from .ml_features import FEATURE_COLUMNS, ROLLING_STATS, historical_features

CORE = tuple(f"pre_gw_last3_matches_{s}_mean" for s in ROLLING_STATS) + (
    "pre_gw_last3_matches_appearance_rate",
    "pre_gw_last3_matches_start_rate",
)
logger = logging.getLogger(__name__)


class RidgeModel:
    def __init__(self, path: Path | None = None) -> None:
        path = path or Path(os.getenv("FPL_RIDGE_MODEL", Path(__file__).with_name("ridge_v1.json")))
        body = path.read_bytes()
        self.data = json.loads(body)
        d = self.data
        if d["format_version"] != 1 or d["features"] != list(FEATURE_COLUMNS):
            raise ValueError("Ridge feature contract mismatch")
        n = len(d["kept"])
        if (
            not n
            or len(set(d["kept"])) != n
            or any(not isinstance(i, int) or not 0 <= i < len(FEATURE_COLUMNS) for i in d["kept"])
            or any(len(d[k]) != n for k in ("medians", "means", "scales", "coefficients"))
            or any(
                not math.isfinite(v)
                for k in ("medians", "means", "scales", "coefficients")
                for v in d[k]
            )
            or any(v <= 0 for v in d["scales"])
            or not math.isfinite(d["intercept"])
        ):
            raise ValueError("Invalid Ridge preprocessing/coefficients")
        self.version = d["model_version"]
        self.sha256 = hashlib.sha256(body).hexdigest()

    def predict(self, features: dict) -> float:
        if set(features) != set(FEATURE_COLUMNS):
            raise ValueError("Exact approved feature schema required")
        d = self.data
        terms = []
        for i, median, mean, scale, weight in zip(
            d["kept"], d["medians"], d["means"], d["scales"], d["coefficients"]
        ):
            raw = features[FEATURE_COLUMNS[i]]
            value = median if raw is None else float(raw)
            if not math.isfinite(value):
                raise ValueError("Nonfinite live feature")
            terms.append(weight * (value - mean) / scale)
        return d["intercept"] + math.fsum(terms)


def predict_player(
    model: RidgeModel,
    forecast: HorizonForecast,
    snapshot: dict,
    pid: int,
    history: dict | None,
    asof: datetime,
    error: str | None = None,
) -> dict[int, dict]:
    next_gw = forecast.gameweeks[0]
    deadline = next(g.deadline for g in forecast.catalogue.gameweeks if g.id == next_gw)
    player = forecast.catalogue.players[pid]
    reasons = []
    features = None
    if deadline is None or asof >= deadline:
        reasons.append("deadline_passed_or_missing")
    if error or history is None:
        reasons.append(error or "history_unavailable")
    else:
        try:
            records = parse_ml_history(history["payload"], snapshot["fixtures"], pid, next_gw, asof)
            features = historical_features(records)
            if not records:
                reasons.append("no_eligible_season_history")
            elif any(features[c] is None for c in CORE):
                reasons.append("missing_core_history")
            if records and records[-1]["team_id"] != player.team_id:
                reasons.append("transfer_since_last_observation")
        except (KeyError, ValueError, TypeError, OverflowError):
            reasons.append("history_schema_or_fixture_conflict")
    if (
        player.availability_status not in (None, "a")
        or player.chance_of_playing is not None
        and player.chance_of_playing < 100
    ):
        reasons.append("availability_requires_statistical_adjustment")
    coverage = {
        "eligible_fixtures": features["pre_gw_history_fixtures"] if features else 0,
        "eligible_gameweeks": features["pre_gw_history_gameweeks"] if features else 0,
        "known_features": sum(features[c] is not None for c in FEATURE_COLUMNS) if features else 0,
        "approved_features": len(FEATURE_COLUMNS),
        "retained_known_features": sum(
            features[FEATURE_COLUMNS[i]] is not None for i in model.data["kept"]
        )
        if features
        else 0,
    }
    result = {}
    for gw in forecast.gameweeks:
        stat = forecast.players[pid][gw]
        flags = list(reasons)
        if gw != next_gw:
            flags.append("multi_step_history_not_observed")
        if stat["coverage"] == "confirmed_blank":
            flags.append("blank_outside_training_population")
        elif stat["coverage"] != "published":
            flags.append("schedule_incomplete")
        elif len(stat["fixture_ids"]) != 1:
            flags.append("double_fixture_effect_not_trained")
        elif not any(
            f.get("id") == stat["fixture_ids"][0] and f.get("event") == gw and f.get("kickoff_time")
            for f in snapshot["fixtures"]
        ):
            flags.append("published_fixture_time_unavailable")
        prediction = None
        if not flags and features:
            try:
                prediction = model.predict(features)
                if not math.isfinite(prediction):
                    raise ValueError("Nonfinite prediction")
            except (ValueError, ArithmeticError):
                flags.append("inference_failed")
        result[gw] = {
            "expected_points": prediction,
            "comparison_points": prediction if prediction is not None else stat["expected_points"],
            "source": "ridge" if prediction is not None else "statistical_fallback",
            "fallback_reasons": flags,
            "difference": prediction - stat["expected_points"]
            if prediction is not None and stat["expected_points"] is not None
            else None,
            "model_version": model.version,
            "model_sha256": model.sha256,
            "feature_asof_utc": asof.isoformat(),
            "target_deadline_utc": next(
                (
                    g.deadline.isoformat()
                    for g in forecast.catalogue.gameweeks
                    if g.id == gw and g.deadline
                ),
                None,
            ),
            "history_source_timestamp": history["retrieved_at"] if history else None,
            "feature_coverage": coverage,
        }
    return result


class LiveML:
    """One coalesced four-worker history job; a failed job cannot interrupt statistical planning."""

    def __init__(
        self, client: FPLClient, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.client, self.now = client, now
        self.lock = RLock()
        self.generation = None
        self.running = False
        self.rows: dict[int, dict] = {}
        self.status = {"state": "idle", "completed": 0, "total": 0}
        self.history_cache: dict[tuple, dict] = {}
        self.last_archive_error = ""
        self.raw_histories: dict[int, dict] = {}
        self.component_forecast: HorizonForecast | None = None
        self.component_metadata: dict | None = None
        self.stopped = Event()

    def request(self, forecast: HorizonForecast, snapshot: dict | None) -> dict:
        generation = (forecast.catalogue.fetched_at.isoformat(), forecast.gameweeks)
        with self.lock:
            if snapshot is None:
                return {"state": "unavailable", "reason": "official_source_snapshot_unavailable"}
            if generation != self.generation:
                self.generation, self.rows = generation, {}
                self.component_forecast = self.component_metadata = None
                self.status = {
                    "state": "loading",
                    "completed": 0,
                    "total": len(forecast.catalogue.players),
                }
            if not self.running and self.status["state"] == "loading":
                self.running = True
                Thread(
                    target=self.build,
                    args=(forecast, snapshot, generation),
                    daemon=True,
                    name="assistant-ridge",
                ).start()
            return {**self.status, "archive_error": self.last_archive_error}

    def build(
        self, forecast: HorizonForecast, snapshot: dict, generation: tuple | None = None
    ) -> None:
        try:
            model = RidgeModel()
            season = str(snapshot["bootstrap"]["events"][0]["deadline_time"])[:4]
            signature = tuple(
                (f["id"], f.get("team_h_score"), f.get("team_a_score"))
                for f in snapshot["fixtures"]
                if f.get("finished")
            )
            histories, errors = {}, {}
            history_failed = Event()

            def fetch(pid: int) -> dict:
                if self.stopped.is_set() or history_failed.is_set():
                    raise RuntimeError("Service stopped")
                # Code + season prevent reused element IDs crossing season boundaries.
                code = next(e["code"] for e in snapshot["bootstrap"]["elements"] if e["id"] == pid)
                key = (season, pid, code, signature)
                cached = self.history_cache.get(key)
                if cached and self.now() - datetime.fromisoformat(
                    cached["retrieved_at"]
                ) < timedelta(hours=6):
                    return cached
                data = self.client.ml_history_snapshot(pid)
                self.history_cache[key] = data
                return data

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ridge-history") as pool:
                pending = {pool.submit(fetch, pid): pid for pid in forecast.catalogue.players}
                for future in as_completed(pending):
                    pid = pending[future]
                    try:
                        histories[pid] = future.result()
                    except Exception:  # noqa: BLE001 - isolate individual upstream/history failures
                        errors[pid] = "official_history_fetch_failed"
                        if len(errors) >= 16 and not histories:
                            history_failed.set()  # do not spend a whole roster's timeouts on an outage
                    with self.lock:
                        if generation is None or generation == self.generation:
                            self.status["completed"] = len(histories) + len(errors)
            asof = self.now()
            rows = {
                pid: predict_player(
                    model, forecast, snapshot, pid, histories.get(pid), asof, errors.get(pid)
                )
                for pid in forecast.catalogue.players
            }
            component, component_metadata, component_error = None, None, None
            try:
                from .component_forecast import build_component_forecast

                component, component_metadata = build_component_forecast(
                    forecast, snapshot, histories, asof
                )
            except Exception as exc:
                logger.exception("Private fixture component inference failed")
                component_error = f"component_failed:{type(exc).__name__}"
            generated = self.now()
            with self.lock:
                if self.stopped.is_set():
                    return
                if generation is not None and generation != self.generation:
                    return  # explicit refresh superseded this immutable source generation
                self.rows, self.raw_histories = rows, histories
                self.component_forecast, self.component_metadata = component, component_metadata
                self.status = {
                    "state": "ready",
                    "completed": len(rows),
                    "total": len(rows),
                    "model_version": model.version,
                    "source_timestamp": asof.isoformat(),
                    "model_sha256": model.sha256,
                    "generated_at": generated.isoformat(),
                    "component_state": "ready" if component else "unavailable",
                    "component_error": component_error,
                    "component_version": component_metadata["version"]
                    if component_metadata
                    else None,
                    "supported_next_gw_players": sum(
                        r[forecast.gameweeks[0]]["source"] == "ridge" for r in rows.values()
                    ),
                }
            from .prospective import ForecastArchive

            try:
                archive = ForecastArchive()
                archive.record(
                    forecast,
                    snapshot,
                    histories,
                    rows,
                    generated,
                    model,
                    feature_asof=asof,
                    component=component,
                    component_metadata=component_metadata,
                    component_error=component_error,
                )
                archive.finalize(snapshot["bootstrap"], self.client)
                self.last_archive_error = ""
            except Exception as exc:
                logger.exception("Private football forecast recording failed")
                self.last_archive_error = f"recording_unavailable:{type(exc).__name__}"
            if len(self.history_cache) > 2000:
                self.history_cache.clear()
        except Exception as exc:
            logger.exception(
                "Private Ridge inference failed; statistical planning remains available"
            )
            with self.lock:
                if generation is None or generation == self.generation:
                    self.rows = {}
                    self.status = {
                        "state": "unavailable",
                        "reason": f"ml_failed:{type(exc).__name__}",
                    }
        finally:
            with self.lock:
                self.running = False

    def player(
        self, pid: int, gw: int, statistical: dict, *, generation: tuple | None = None
    ) -> dict:
        with self.lock:
            row = (
                self.rows.get(pid, {}).get(gw)
                if generation is None or generation == self.generation
                else None
            )
            if (
                row
                and row.get("target_deadline_utc")
                and datetime.fromisoformat(row["target_deadline_utc"]) > self.now()
            ):
                prediction = row["expected_points"]
                return {
                    **row,
                    "comparison_points": prediction
                    if prediction is not None
                    else statistical["expected_points"],
                    "difference": prediction - statistical["expected_points"]
                    if prediction is not None and statistical["expected_points"] is not None
                    else None,
                }
            return {
                "expected_points": None,
                "comparison_points": statistical["expected_points"],
                "source": "statistical_fallback",
                "difference": None,
                "fallback_reasons": [
                    self.status.get(
                        "reason", "history_loading" if self.running else "ml_unavailable"
                    )
                ],
            }

    def invalidate(self) -> None:
        with self.lock:
            self.rows, self.history_cache = {}, {}
            self.component_forecast = self.component_metadata = None
            self.generation = None

    def components(self, forecast: HorizonForecast) -> HorizonForecast | None:
        with self.lock:
            generation = (forecast.catalogue.fetched_at.isoformat(), forecast.gameweeks)
            first = next(g for g in forecast.catalogue.gameweeks if g.id == forecast.gameweeks[0])
            return (
                self.component_forecast
                if (
                    generation == self.generation and first.deadline and self.now() < first.deadline
                )
                else None
            )
