"""Deadline-aware orchestration around the unchanged live forecast/recording pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fpl_forfeit.api import FPLAccessError, FPLAPIError, FPLClient, FPLNotFoundError
from fpl_forfeit.assistant.prospective import ForecastArchive, canonical, immutable
from fpl_forfeit.assistant.service import AssistantService

from .archive import TAG, create_bundle, policy, read_bundle, stamp, tag_for
from .github import GitHubStore, StorageError


class RetryClient(FPLClient):
    """Transport-only retries; feature values and forecast functions remain unchanged."""

    def _get(self, path: str):
        for attempt in range(3):
            try:
                return super()._get(path)
            except (FPLAccessError, FPLNotFoundError):
                raise
            except FPLAPIError:
                if attempt == 2:
                    raise
                time.sleep((2, 5)[attempt])
        raise AssertionError("Unreachable")


def context() -> dict:
    commit = (
        os.getenv("GITHUB_SHA")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    )
    return {
        "code_commit": commit,
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "event": os.getenv("GITHUB_EVENT_NAME", "local"),
        "run_url": f"https://github.com/{policy()['repository']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        if os.getenv("GITHUB_RUN_ID")
        else None,
    }


def season_of(bootstrap: dict) -> str:
    year = stamp(bootstrap["events"][0]["deadline_time"]).year
    return f"{year}-{str(year + 1)[-2:]}"


def due_slot(bootstrap: dict, now: datetime, releases: list[dict]) -> tuple[int, str] | None:
    cfg = policy()
    if season_of(bootstrap) != cfg["season"] or now >= stamp(cfg["stop_after_utc"]):
        return None
    future = sorted(
        (int(e["id"]), stamp(e["deadline_time"]))
        for e in bootstrap["events"]
        if e.get("deadline_time")
        and int(e["id"]) >= cfg["first_gameweek"]
        and stamp(e["deadline_time"]) > now
    )
    if not future:
        return None
    gw, deadline = future[0]
    lead = (deadline - now).total_seconds() / 3600
    for stage in cfg["stages"]:
        if stage["closes_hours"] < lead <= stage["opens_hours"]:
            tag = tag_for(gw, stage["name"])
            if any(
                r["tag_name"] == tag and not r["draft"] and r.get("immutable") is True
                for r in releases
            ):
                return None
            return gw, stage["name"]
    return None


def tracked_gameweeks(releases: list[dict]) -> set[int]:
    tracked = set()
    for release in releases:
        match = TAG.fullmatch(release["tag_name"])
        if not match or release["draft"]:
            continue
        if release.get("immutable") is not True:
            raise StorageError("Mutable recording release found; immutability must be enabled")
        if match[3] != "actual":
            # Notes are a routing hint only. Scoring verifies the immutable bundle itself.
            note = json.loads(release["body"].split("```json\n", 1)[1].split("\n```", 1)[0])
            gws = note["target_gameweeks"]
            if any(
                not isinstance(g, int) or not policy()["first_gameweek"] <= g <= 38 for g in gws
            ):
                raise ValueError("Invalid routing Gameweeks")
            tracked.update(gws)
    return tracked


def capture_forecast(client: FPLClient, gw: int, stage: str, root: Path, provenance: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as work:
        old = os.environ.get("FPL_ML_ARCHIVE_DIR")
        os.environ["FPL_ML_ARCHIVE_DIR"] = work
        try:
            service = AssistantService(client)
            forecast = service.forecast()
            if forecast.gameweeks[0] != gw:
                raise ValueError("Deadline crossed/changed during collection; no backfill allowed")
            service.ml.build(forecast, service.official_snapshot)
            if (
                service.ml.status["state"] != "ready"
                or service.ml.last_archive_error
                or not service.ml.raw_histories
            ):
                raise RuntimeError("Inference/recording unavailable; next scheduled run will retry")
            sources = list(Path(work).glob("forecast-*.json.gz"))
            if len(sources) != 1:
                raise RuntimeError("Expected one original forecast capture")
            candidate = (
                root
                / "candidates"
                / tag_for(gw, stage)
                / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                / "capture.zip"
            )
            return create_bundle(sources[0], stage, gw, provenance, candidate)
        finally:
            if old is None:
                os.environ.pop("FPL_ML_ARCHIVE_DIR", None)
            else:
                os.environ["FPL_ML_ARCHIVE_DIR"] = old


def capture_actuals(
    client: FPLClient, snapshot: dict, releases: list[dict], root: Path, provenance: dict
) -> list[Path]:
    existing = {
        int(TAG.fullmatch(r["tag_name"])[2])
        for r in releases
        if TAG.fullmatch(r["tag_name"])[3] == "actual" and not r["draft"]
    }
    wanted = tracked_gameweeks(releases) - existing
    bootstrap = snapshot["bootstrap"]
    if season_of(bootstrap) != policy()["season"]:
        return []
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as work:
        immutable(
            Path(work) / "tracked.index.json",
            canonical({"season": policy()["season"], "gameweeks": sorted(wanted)}),
        )
        ForecastArchive(Path(work)).finalize(bootstrap, client)
        candidates = []
        for source in sorted(Path(work).glob("actual-*.json.gz")):
            from .archive import unpack_record

            gw = unpack_record(source.read_bytes())["gameweek"]
            candidate = (
                root
                / "candidates"
                / tag_for(gw, "actual")
                / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                / "capture.zip"
            )
            evidence = {
                **provenance,
                "official_final_flags_source": snapshot["sources"][0],
                "official_final_flags_observed_at": snapshot["source_finished_utc"],
            }
            candidates.append(create_bundle(source, "actual", gw, evidence, candidate))
        return candidates


def missing_stages(bootstrap: dict, now: datetime, releases: list[dict]) -> list[str]:
    present = {r["tag_name"] for r in releases if not r["draft"] and r.get("immutable") is True}
    gaps = []
    for event in bootstrap["events"]:
        gw = int(event["id"])
        if gw < policy()["first_gameweek"] or not event.get("deadline_time"):
            continue
        lead = (stamp(event["deadline_time"]) - now).total_seconds() / 3600
        for stage in policy()["stages"]:
            if lead <= stage["closes_hours"] and tag_for(gw, stage["name"]) not in present:
                gaps.append(f"GW{gw:02} {stage['name']} window missed; never backfilled")
    return gaps


def run(
    store: GitHubStore, client: FPLClient, root: Path, *, check: bool = False, now=None
) -> dict:
    now = now or (lambda: datetime.now(UTC))
    if now() >= stamp(policy()["stop_after_utc"]):
        return {
            "state": "outside_recording_season",
            "published": [],
            "gaps": [],
            "new_actual_gameweeks": [],
        }
    snapshot = client.forecast_snapshot()
    cfg = policy()
    if season_of(snapshot["bootstrap"]) != cfg["season"] or now() >= stamp(cfg["stop_after_utc"]):
        return {
            "state": "outside_recording_season",
            "published": [],
            "gaps": [],
            "new_actual_gameweeks": [],
        }
    releases = store.releases()
    provenance = context()
    slot = due_slot(snapshot["bootstrap"], now(), releases)
    report = {
        "state": "check" if check else "recorded",
        "checked_at": now().isoformat(),
        "due_slot": list(slot) if slot else None,
        "published": [],
        "errors": [],
        "gaps": [],
        "new_actual_gameweeks": [],
    }
    if check:
        report["gaps"] = missing_stages(snapshot["bootstrap"], now(), releases)
        return report
    # Recover fully uploaded candidates first. An incomplete draft never blocks later slots/actuals.
    for release in releases:
        if release["draft"] and any(
            a["name"] == "capture.zip" and a["state"] == "uploaded" for a in release["assets"]
        ):
            try:
                published = store.finish_draft(release, root / "archive")
                report["published"].append(published["html_url"])
                match = TAG.fullmatch(published["tag_name"])
                if match[3] == "actual":
                    report["new_actual_gameweeks"].append(int(match[2]))
            except (StorageError, ValueError) as exc:
                report["errors"].append(str(exc))
    releases = store.releases()
    slot = due_slot(snapshot["bootstrap"], now(), releases)
    if slot:
        try:
            reserved = store.release(tag_for(*slot))
            if reserved:
                raise StorageError(
                    f"Incomplete earlier draft {reserved['tag_name']}; preserve the reserved original, see recovery instructions"
                )
            candidate = capture_forecast(client, *slot, root, provenance)
            published = store.publish(candidate, root / "archive")
            report["published"].append(published["html_url"])
        except (RuntimeError, ValueError, OSError) as exc:
            report["errors"].append(str(exc))
    releases = store.releases()
    try:
        for candidate in capture_actuals(client, snapshot, releases, root, provenance):
            manifest, _ = read_bundle(candidate)
            reserved = store.release(tag_for(manifest["gameweek"], "actual"))
            if reserved and reserved["draft"]:
                raise StorageError("Incomplete actuals draft; recover original capture explicitly")
            published = store.publish(candidate, root / "archive")
            report["published"].append(published["html_url"])
            report["new_actual_gameweeks"].append(manifest["gameweek"])
    except (RuntimeError, ValueError, OSError) as exc:
        report["errors"].append(str(exc))
    try:
        store.heartbeat(provenance["code_commit"])
    except (StorageError, OSError) as exc:
        report["errors"].append(f"Monthly keepalive failed: {exc}")
    report["gaps"] = missing_stages(snapshot["bootstrap"], now(), store.releases())
    published_primary = {
        int(TAG.fullmatch(r["tag_name"])[2])
        for r in store.releases()
        if not r["draft"]
        and r.get("immutable") is True
        and TAG.fullmatch(r["tag_name"])[3] != "actual"
    }
    acknowledged = {
        int(g.strip())
        for g in os.getenv("FPL_ACKNOWLEDGED_MISSING_GWS", "").split(",")
        if g.strip()
    }
    if any(not cfg["first_gameweek"] <= g <= 38 for g in acknowledged):
        raise ValueError("Invalid acknowledged missing Gameweeks")
    report["acknowledged_missing_gameweeks"] = sorted(acknowledged)
    for event in snapshot["bootstrap"]["events"]:
        gw = int(event["id"])
        if (
            gw >= cfg["first_gameweek"]
            and event.get("deadline_time")
            and stamp(event["deadline_time"]) <= now()
            and gw not in published_primary
            and gw not in acknowledged
        ):
            report["errors"].append(
                f"No primary pre-deadline capture for GW{gw:02}; permanent gap, never backfilled. Acknowledge explicitly only after review."
            )
    if report["errors"]:
        report["state"] = "failed"
    return report
