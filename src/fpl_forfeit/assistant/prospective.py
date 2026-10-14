"""Immutable server-side public-football snapshots; never contains sessions or squads."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    def normalized(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, dict):
            return {str(k): normalized(item) for k, item in v.items()}
        if isinstance(v, (tuple, list)):
            return [normalized(item) for item in v]
        return v

    return json.dumps(
        normalized(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def immutable(path: Path, body: bytes) -> bool:
    """Atomic publication without replacing an earlier record, including across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            return False
        return True
    finally:
        Path(name).unlink(missing_ok=True)


class ForecastArchive:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.getenv("FPL_ML_ARCHIVE_DIR", "snapshots/ml"))

    def record(
        self,
        forecast,
        snapshot: dict,
        histories: dict,
        predictions: dict,
        captured: datetime,
        model,
        *,
        feature_asof: datetime | None = None,
        component=None,
        component_metadata: dict | None = None,
        component_error: str | None = None,
    ) -> Path:
        deadlines = {
            g.id: g.deadline
            for g in forecast.catalogue.gameweeks
            if g.id in forecast.gameweeks and g.deadline
        }
        if (
            len(deadlines) != len(forecast.gameweeks)
            or not deadlines
            or captured >= min(deadlines.values())
            or feature_asof is not None
            and feature_asof > captured
            or datetime.fromisoformat(snapshot["source_finished_utc"]) > captured
            or any(datetime.fromisoformat(h["retrieved_at"]) > captured for h in histories.values())
        ):
            raise ValueError(
                "All source inputs and generated forecasts must precede their deadlines"
            )
        year = int(snapshot["bootstrap"]["events"][0]["deadline_time"][:4])
        season = f"{year}-{str(year + 1)[-2:]}"
        hour = (
            captured.hour
            if min(deadlines.values()) - captured < timedelta(hours=2)
            else captured.hour // 6 * 6
        )
        key = f"forecast-{season}-GW{forecast.gameweeks[0]:02}-{captured:%Y%m%d}-{hour:02}"
        path = self.root / f"{key}.json.gz"
        if path.exists():
            return path
        if (
            self.root.exists()
            and sum(p.stat().st_size for p in self.root.glob("*.gz")) > 512 * 1024 * 1024
        ):
            raise OSError(
                "Archive storage limit reached; export existing records before increasing the limit"
            )
        record = {
            "schema_version": 2,
            "season": season,
            "captured_at": captured.isoformat(),
            "feature_asof_utc": (feature_asof or captured).isoformat(),
            "deadlines": {g: d.isoformat() for g, d in deadlines.items()},
            "official_snapshot": snapshot,
            "official_histories": histories,
            "source_sha256": hashlib.sha256(
                canonical({"snapshot": snapshot, "histories": histories})
            ).hexdigest(),
            "ridge_model": model.data,
            "ridge_model_sha256": model.sha256,
            "ml_predictions": predictions,
            "statistical_predictions": forecast.players,
            "statistical_model_version": forecast.inputs.model_version,
            "statistical_seed": forecast.seed,
            "statistical_samples": forecast.samples,
            "statistical_source_timestamp": forecast.catalogue.fetched_at.isoformat(),
            "note": "As-of preview; later GWs and unsupported rows use labelled statistical fallback. No personal squad or auth data.",
        }
        if component is not None and component_metadata is not None:
            record["fixture_component"] = {
                "metadata": component_metadata,
                "predictions": component.players,
                "fixtures": [asdict(p) for p in component.fixtures],
                "prediction_sha256": hashlib.sha256(canonical(component.players)).hexdigest(),
            }
        record["fixture_component_status"] = {
            "state": "ready" if component else "unavailable",
            "error": component_error,
        }
        body = canonical(record)
        if immutable(path, gzip.compress(body, mtime=0)):
            immutable(
                self.root / f"{key}.index.json",
                canonical(
                    {
                        "season": season,
                        "gameweeks": list(deadlines),
                        "captured_at": captured.isoformat(),
                        "snapshot_file": path.name,
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                ),
            )
        return path

    def finalize(self, bootstrap: dict, client) -> int:
        events = {int(e["id"]): e for e in bootstrap.get("events", [])}
        year = int(bootstrap["events"][0]["deadline_time"][:4])
        season = f"{year}-{str(year + 1)[-2:]}"
        tracked = set()
        for path in self.root.glob("*.index.json"):
            index = json.loads(path.read_bytes())
            if index["season"] == season:
                tracked.update(index["gameweeks"])
        count = 0
        for gw in sorted(tracked):
            event = events.get(gw, {})
            path = self.root / f"actual-{season}-GW{gw:02}.json.gz"
            if (
                event.get("finished") is not True
                or event.get("data_checked") is not True
                or path.exists()
            ):
                continue
            source = client.final_event_snapshot(gw)
            from ..api import parse_final_event_points

            points = parse_final_event_points(source["payload"])
            record = {
                "season": season,
                "gameweek": gw,
                "official_final_event": event,
                "source": source,
                "actual_points": points,
                "source_sha256": hashlib.sha256(canonical(source["payload"])).hexdigest(),
                "missing_elements": "Unknown, never filled with zero; first official-final capture retained",
            }
            count += immutable(path, gzip.compress(canonical(record), mtime=0))
        return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scheduled public-football recording; no login, league or squad data"
    )
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument(
        "--replay",
        type=Path,
        help="Verify/reproduce an own saved forecast without network or fitting",
    )
    args = parser.parse_args()
    if args.replay:
        from ..api import parse_forecast_catalogue
        from ..forecasting.horizon import forecast_horizon
        from .ml_inference import RidgeModel, predict_player

        data = json.loads(gzip.decompress(args.replay.read_bytes()))
        snapshot = data["official_snapshot"]
        source_hash = hashlib.sha256(
            canonical({"snapshot": snapshot, "histories": data["official_histories"]})
        ).hexdigest()
        if source_hash != data["source_sha256"]:
            # Preserve and verify the first v1 capture, whose integer history keys sorted numerically.
            legacy = {
                "snapshot": snapshot,
                "histories": {int(k): v for k, v in data["official_histories"].items()},
            }
            legacy_hash = hashlib.sha256(
                json.dumps(legacy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
            if data["schema_version"] != 1 or legacy_hash != data["source_sha256"]:
                raise ValueError("Source checksum mismatch")
        from dataclasses import replace

        catalogue = replace(
            parse_forecast_catalogue(snapshot["bootstrap"], snapshot["fixtures"]),
            fetched_at=datetime.fromisoformat(data["statistical_source_timestamp"]),
        )
        forecast = forecast_horizon(
            catalogue,
            tuple(sorted(int(g) for g in data["deadlines"])),
            samples=data["statistical_samples"],
            seed=data["statistical_seed"],
        )
        model = RidgeModel.__new__(RidgeModel)
        model.data, model.sha256, model.version = (
            data["ridge_model"],
            data["ridge_model_sha256"],
            data["ridge_model"]["model_version"],
        )
        for pid, saved in data["ml_predictions"].items():
            history = data["official_histories"].get(pid)
            reason = "official_history_fetch_failed" if history is None else None
            actual = predict_player(
                model,
                forecast,
                snapshot,
                int(pid),
                history,
                datetime.fromisoformat(data.get("feature_asof_utc", data["captured_at"])),
                reason,
            )
            if json.loads(canonical(actual)) != saved:
                raise ValueError(f"Prediction replay mismatch for {pid}")
        if json.loads(canonical(forecast.players)) != data["statistical_predictions"]:
            raise ValueError("Statistical replay mismatch")
        if "fixture_component" in data:
            from .component_forecast import build_component_forecast
            from .component_model import ComponentModel

            saved = data["fixture_component"]
            component, _ = build_component_forecast(
                forecast,
                snapshot,
                {int(k): v for k, v in data["official_histories"].items()},
                datetime.fromisoformat(data["feature_asof_utc"]),
                model=ComponentModel(data=saved["metadata"]["model"]),
            )
            if canonical(component.players) != canonical(saved["predictions"]):
                raise ValueError("Fixture component replay mismatch")
            if canonical([asdict(p) for p in component.fixtures]) != canonical(saved["fixtures"]):
                raise ValueError("Fixture component fixture replay mismatch")
        print(
            f"Reproduced {len(data['ml_predictions'])} players, {'3 forecast methods' if 'fixture_component' in data else 'both forecast methods'}; no network or fitting"
        )
        return
    if args.archive_dir:
        os.environ["FPL_ML_ARCHIVE_DIR"] = str(args.archive_dir)
    from .service import AssistantService

    service = AssistantService()
    forecast = service.forecast()
    service.ml.build(forecast, service.official_snapshot)
    print(
        json.dumps({**service.ml.status, "archive_error": service.ml.last_archive_error}, indent=2)
    )
    if service.ml.status["state"] != "ready" or service.ml.last_archive_error:
        raise SystemExit(1)


def start_recording(service) -> None:
    """Process-local scheduler. Free-host sleep/restarts require an external runner/backup."""
    from threading import Event, Thread

    stop = Event()
    service.ml_recording_stop = stop

    def loop() -> None:
        while not stop.is_set():
            try:
                forecast = service.forecast()
                service.ml_status(forecast)
                if service.official_snapshot:
                    ForecastArchive().finalize(
                        service.official_snapshot["bootstrap"], service.client
                    )
            except Exception:  # noqa: BLE001 - recorder failures must not stop the application
                # End of season still needs final actuals even when no future forecast exists.
                snapshot = getattr(service.client, "latest_forecast_snapshot", None)
                if snapshot:
                    try:
                        ForecastArchive().finalize(snapshot["bootstrap"], service.client)
                    except Exception:  # noqa: BLE001 - status exposes this recoverable failure
                        service.ml.last_archive_error = "actual_recording_unavailable"
            stop.wait(1800)

    Thread(target=loop, daemon=True, name="assistant-prospective").start()


if __name__ == "__main__":
    main()
