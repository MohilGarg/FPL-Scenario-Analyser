"""Small, self-contained release bundles and strict provenance validation."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fpl_forfeit.assistant.prospective import canonical, immutable

CONFIG = Path(__file__).with_name("config.json")
TAG = re.compile(
    r"fpl-(2026-27)-gw(\d{2})-(advance|update|final|actual|rolling-\d{8}T\d{6}Z)-v([12])\Z"
)


def policy() -> dict:
    return json.loads(CONFIG.read_bytes())


def cutoff_policy() -> dict:
    # The original config/hash is immutable: old releases continue to verify unchanged.
    return json.loads(CONFIG.with_name("cutoffs.json").read_bytes())


def cutoff_evidence(captured: datetime, deadline: datetime, cfg: dict | None = None) -> dict:
    cfg = cfg or cutoff_policy()
    return {
        "policy_version": cfg["policy_version"],
        "policy_sha256": digest(canonical(cfg)),
        "lead_seconds": (deadline - captured).total_seconds(),
        "eligible_cutoffs": [
            name
            for name, hours in cfg["cutoffs"].items()
            if captured <= deadline - timedelta(hours=hours)
        ],
        "meaning": "capture completion; durable publication must independently precede the evaluation cutoff",
    }


def stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Offset-aware provenance timestamps required")
    return parsed


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def unpack_record(body: bytes) -> dict:
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
        raw = stream.read(policy()["max_record_bytes"] + 1)
    if len(raw) > policy()["max_record_bytes"]:
        raise ValueError("Record exceeds decompression bound")
    return json.loads(raw)


def validate_record(record: dict, manifest: dict) -> None:
    cfg = policy()
    if (
        manifest["season"] != cfg["season"]
        or not cfg["first_gameweek"] <= manifest["gameweek"] <= 38
    ):
        raise ValueError("Wrong season/Gameweek")
    if manifest["record_sha256"] == "" or manifest["policy_sha256"] != digest(canonical(cfg)):
        raise ValueError("Unknown recording policy")
    captured = stamp(manifest["captured_at"])
    if record["season"] != manifest["season"]:
        raise ValueError("Record season mismatch")
    if manifest["stage"] == "actual":
        from fpl_forfeit.api import parse_final_event_points

        if record["gameweek"] != manifest["gameweek"]:
            raise ValueError("Actual Gameweek mismatch")
        event = record["official_final_event"]
        if (
            event.get("finished") is not True
            or event.get("data_checked") is not True
            or int(event["id"]) != manifest["gameweek"]
        ):
            raise ValueError("Actuals require official final flags")
        if captured != stamp(record["source"]["retrieved_at"]):
            raise ValueError("Actual capture timestamp mismatch")
        if record["source_sha256"] != digest(canonical(record["source"]["payload"])):
            raise ValueError("Actual source checksum mismatch")
        if stamp(event["deadline_time"]) >= captured:
            raise ValueError("Official-final actuals cannot precede their deadline")
        if canonical(record["actual_points"]) != canonical(
            parse_final_event_points(record["source"]["payload"])
        ):
            raise ValueError("Actual points disagree with the preserved official source")
        return
    if record["schema_version"] != 2 or record["captured_at"] != manifest["captured_at"]:
        raise ValueError("Expected the current original prospective capture")
    deadline = stamp(record["deadlines"][str(manifest["gameweek"])])
    if deadline != stamp(manifest["deadline_utc"]) or captured >= deadline:
        raise ValueError("Late or inconsistent forecast")
    if any(captured >= stamp(d) for d in record["deadlines"].values()):
        raise ValueError("Forecast completed after a target deadline")
    if "cutoff_evidence" in manifest:
        known = [
            cutoff_policy(),
            json.loads(CONFIG.with_name("cutoffs-v2-hourly.json").read_bytes()),
        ]
        matched = [
            cfg
            for cfg in known
            if digest(canonical(cfg)) == manifest["cutoff_evidence"]["policy_sha256"]
        ]
        if not matched or manifest["cutoff_evidence"] != cutoff_evidence(
            captured, deadline, matched[0]
        ):
            raise ValueError("Cutoff evidence does not match original capture/deadline")
    if manifest["stage"].startswith("rolling-"):
        if manifest.get("archive_version") != 2 or "cutoff_evidence" not in manifest:
            raise ValueError("Rolling captures require the versioned cutoff policy")
        if manifest["stage"] != "rolling-" + captured.strftime("%Y%m%dT%H%M%SZ"):
            raise ValueError("Rolling slot must name the actual original completion time")
    cutoff = stamp(record["feature_asof_utc"])
    source = record["official_snapshot"]
    if (
        not stamp(source["source_started_utc"])
        <= stamp(source["source_finished_utc"])
        <= cutoff
        <= captured
    ):
        raise ValueError("Inconsistent source/cutoff/generation ordering")
    if any(stamp(h["retrieved_at"]) > cutoff for h in record["official_histories"].values()):
        raise ValueError("History obtained after the feature cutoff")
    expected = digest(canonical({"snapshot": source, "histories": record["official_histories"]}))
    if record["source_sha256"] != expected:
        raise ValueError("Forecast source checksum mismatch")
    if "external_forecasts" in record:
        from fpl_forfeit.assistant.external_forecasts import (
            batch_from_dict,
            may_redistribute,
            validate_batch,
        )

        external = record["external_forecasts"]
        if digest(canonical(external["batches"])) != external["sha256"]:
            raise ValueError("External forecast checksum mismatch")
        for batch in external["batches"]:
            normalized_batch = batch_from_dict(batch)
            if (
                not may_redistribute(normalized_batch)
                or batch["season"] != record["season"]
                or not batch.get("attribution")
                or not stamp(batch["computed_at"] or batch["retrieved_at"])
                <= stamp(batch["retrieved_at"])
                <= cutoff
                or stamp(batch["deadline"]) < deadline
            ):
                raise ValueError("External source restricted, misaligned or unavailable at cutoff")
            validate_batch(normalized_batch, source["bootstrap"], source["fixtures"])
            for row in batch["rows"]:
                if (
                    row["points_semantics"] != "unconditional"
                    or row["validation_status"] != "validated"
                ):
                    raise ValueError("Unvalidated external forecast semantics")
    if (
        record["ridge_model_sha256"] != manifest["ridge_model_sha256"]
        or record["statistical_model_version"] != manifest["statistical_model_version"]
    ):
        raise ValueError("Model provenance mismatch")
    if "fixture_component" in record:
        component = record["fixture_component"]
        meta = component["metadata"]
        if (
            digest(canonical(component["predictions"])) != component["prediction_sha256"]
            or digest(canonical(meta["model"])) != manifest.get("component_payload_sha256")
            or meta["version"] != manifest.get("component_version")
            or meta["feature_asof_utc"] != record["feature_asof_utc"]
        ):
            raise ValueError("Fixture component provenance mismatch")
    if "minute_model" in record:
        minutes = record["minute_model"]
        if digest(canonical(minutes["predictions"])) != minutes["prediction_sha256"] or digest(
            canonical(minutes["metadata"])
        ) != manifest.get("minutes_metadata_sha256"):
            raise ValueError("Minutes forecast provenance mismatch")
    for name, challenger in record.get("challengers", {}).items():
        if name not in {"fitted_minutes", "empirical_minutes"} or (
            digest(canonical(challenger["predictions"])) != challenger["prediction_sha256"]
            or digest(canonical(challenger["metadata"]))
            != manifest.get("challenger_metadata_sha256", {}).get(name)
            or challenger["metadata"]["feature_asof_utc"] != record["feature_asof_utc"]
            or challenger["archived_horizon"] != [manifest["gameweek"]]
        ):
            raise ValueError("Coherent challenger provenance mismatch")


def create_bundle(source: Path, stage: str, gw: int, context: dict, output: Path) -> Path:
    body = source.read_bytes()
    record = unpack_record(body)
    cfg = policy()
    forecast = stage != "actual"
    name = "forecast.json.gz" if forecast else "actual.json.gz"
    manifest = {
        "format_version": 1,
        "season": cfg["season"],
        "gameweek": gw,
        "stage": stage,
        "record_file": name,
        "record_sha256": digest(body),
        "record_bytes": len(body),
        "policy_version": cfg["policy_version"],
        "policy_sha256": digest(canonical(cfg)),
        "captured_at": record["captured_at"] if forecast else record["source"]["retrieved_at"],
        "deadline_utc": record["deadlines"][str(gw)]
        if forecast
        else record["official_final_event"]["deadline_time"],
        "context": context,
    }
    if forecast:
        manifest.update(
            ridge_model_sha256=record["ridge_model_sha256"],
            ridge_model_version=record["ridge_model"]["model_version"],
            statistical_model_version=record["statistical_model_version"],
            target_gameweeks=sorted(int(g) for g in record["deadlines"]),
            history_players=len(record["official_histories"]),
            roster_players=len(record["ml_predictions"]),
            archive_version=2,
            cutoff_evidence=cutoff_evidence(
                stamp(record["captured_at"]), stamp(manifest["deadline_utc"])
            ),
        )
        if "fixture_component" in record:
            meta = record["fixture_component"]["metadata"]
            manifest.update(
                component_version=meta["version"],
                component_payload_sha256=digest(canonical(meta["model"])),
            )
        if "fixture_component_status" in record:
            manifest["fixture_component_status"] = record["fixture_component_status"]
        if "minute_model" in record:
            manifest["minutes_metadata_sha256"] = digest(
                canonical(record["minute_model"]["metadata"])
            )
        if record.get("challengers"):
            manifest["challenger_metadata_sha256"] = {
                name: digest(canonical(value["metadata"]))
                for name, value in record["challengers"].items()
            }
    validate_record(record, manifest)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for filename, value in ((name, body), ("manifest.json", canonical(manifest))):
            item = zipfile.ZipInfo(filename, (1980, 1, 1, 0, 0, 0))
            archive.writestr(item, value)
    payload = target.getvalue()
    if len(payload) > cfg["max_bundle_bytes"]:
        raise ValueError("Bundle storage bound exceeded")
    if not immutable(output, payload) and output.read_bytes() != payload:
        raise ValueError("Refusing to replace an earlier candidate bundle")
    return output


def read_bundle(path: Path) -> tuple[dict, dict]:
    cfg = policy()
    if path.stat().st_size > cfg["max_bundle_bytes"]:
        raise ValueError("Oversized bundle")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if (
            len(names) != 2
            or "manifest.json" not in names
            or archive.getinfo("manifest.json").file_size > 65536
        ):
            raise ValueError("Unexpected bundle members")
        manifest = json.loads(archive.read("manifest.json"))
        name = manifest["record_file"]
        if (
            manifest["format_version"] != 1
            or name not in ("forecast.json.gz", "actual.json.gz")
            or set(names) != {name, "manifest.json"}
        ):
            raise ValueError("Unexpected record path/format")
        if archive.getinfo(name).file_size > cfg["max_bundle_bytes"]:
            raise ValueError("Oversized record")
        body = archive.read(name)
    if digest(body) != manifest["record_sha256"] or len(body) != manifest["record_bytes"]:
        raise ValueError("Record byte checksum mismatch")
    record = unpack_record(body)
    validate_record(record, manifest)
    return manifest, record


def tag_for(gw: int, stage: str) -> str:
    version = 2 if stage.startswith("rolling-") else 1
    tag = f"fpl-{policy()['season']}-gw{gw:02}-{stage}-v{version}"
    if not TAG.fullmatch(tag):
        raise ValueError("Invalid archive slot")
    return tag


def validate_release(release: dict, bundle: Path, *, published: bool = True) -> tuple[dict, dict]:
    manifest, record = read_bundle(bundle)
    if release["tag_name"] != tag_for(manifest["gameweek"], manifest["stage"]):
        raise ValueError("Release slot mismatch")
    assets = [a for a in release["assets"] if a["name"] == "capture.zip"]
    if (
        len(assets) != 1
        or assets[0]["state"] != "uploaded"
        or assets[0]["size"] != bundle.stat().st_size
        or assets[0]["digest"] != "sha256:" + digest(bundle.read_bytes())
    ):
        raise ValueError("GitHub asset checksum/state mismatch")
    if published and (release["draft"] or release.get("immutable") is not True):
        raise ValueError("Published archive must have GitHub immutability protection")
    if manifest["stage"] != "actual":
        deadline = stamp(manifest["deadline_utc"])
        if (
            stamp(assets[0]["created_at"]) >= deadline
            or published
            and stamp(release["published_at"]) >= deadline
        ):
            raise ValueError("GitHub cannot verify durable publication before deadline")
        if published:
            # Provider times are separate evidence. A slow upload cannot gain an earlier cutoff.
            manifest = {
                **manifest,
                "durably_published_at": max(
                    stamp(release["published_at"]), stamp(assets[0]["created_at"])
                ).isoformat(),
            }
    return manifest, record


def receipt(release: dict) -> dict[str, Any]:
    return {
        key: release[key]
        for key in ("id", "tag_name", "draft", "immutable", "published_at", "html_url", "assets")
    }
