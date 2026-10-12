"""Recover actual deadlines from pinned, timestamped official-API archive copies.

Only events are read. End-of-season elements/teams in these payloads NEVER become
features. Finished past events and the imminent final event are verifiable; future
provisional events stay gaps. No kickoff-offset deadline inference is implemented.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .dataset import boolean, timestamp
from .sources import ROOT, digest

MAX_STORED_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 4 * 1024 * 1024
VERIFIED = "verified_archived_official_api"


def deadline_lock() -> dict:
    lock = json.loads((ROOT / "deadline-source-lock.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{40}", lock["revision"]):
        raise ValueError("Deadline archive needs an immutable Git commit")
    return lock


def fetch_deadline_sources(raw_dir: Path, seasons: list[str]) -> None:
    lock = deadline_lock()
    captures = {c["season"]: c for c in lock["captures"]}
    for season in seasons:
        for filename, expected in captures[season]["files"].items():
            path = raw_dir / "deadlines" / season / filename
            if path.exists():
                data = path.read_bytes()
            else:
                url = (f"https://raw.githubusercontent.com/{lock['repository']}/"
                       f"{lock['revision']}/{captures[season]['path']}/{filename}")
                with urlopen(Request(url, headers={"User-Agent": "FPL-Phase2a/1"}), timeout=60) as r:
                    data = r.read(MAX_STORED_BYTES + 1)
            if len(data) > MAX_STORED_BYTES or len(data) != expected["bytes"]:
                raise ValueError(f"Deadline archive size mismatch: {season}/{filename}")
            if digest(data) != expected["sha256"]:
                raise ValueError(f"Deadline archive checksum mismatch: {season}/{filename}")
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + ".part")
                temporary.write_bytes(data)
                temporary.replace(path)


def extract_deadlines(season: str, manifest: dict, body: bytes, source_url: str) -> list[dict]:
    """Verify archived bytes and distinguish finalized evidence from provisional dates."""
    files = [f for f in manifest["files"] if f["name"] == "bootstrap-static.json.gz"
             and f["endpoint"] == "/bootstrap-static/"]
    if len(files) != 1 or files[0]["bytes"] != len(body) or files[0]["sha256"] != digest(body):
        raise ValueError("Archived API body does not match its provenance manifest")
    captured = timestamp(manifest["captured_at"])
    wayback = manifest["backfill"]["wayback_timestamp"]
    expected_capture = datetime.strptime(wayback, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    archive_url = manifest["backfill"]["source"]
    if (manifest["backfill"]["season"] != season or captured != expected_capture
            or archive_url != f"https://web.archive.org/web/{wayback}id_/"
                              "https://fantasy.premierleague.com/api/bootstrap-static/"):
        raise ValueError("Archive season/crawl timestamp/original endpoint mismatch")
    payload = json.loads(body)
    events = {int(e["id"]): e for e in payload["events"]}
    if len(events) != len(payload["events"]) or set(events) != set(range(1, 39)):
        raise ValueError("Expected exactly 38 distinct official FPL event IDs")
    year = int(season[:4])
    start = datetime(year, 7, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 7, 1, tzinfo=timezone.utc)
    if captured is None or not start < captured < end:
        raise ValueError("Crawl is outside its claimed season")
    rows = []
    previous = None
    for gw, event in sorted(events.items()):
        deadline = timestamp(event["deadline_time"])
        if deadline is None or not start < deadline < end or (previous and deadline <= previous):
            raise ValueError("Invalid season or non-increasing official deadlines")
        if int(event["deadline_time_epoch"]) != int(deadline.timestamp()):
            raise ValueError("API deadline ISO/epoch mismatch")
        previous = deadline
        past_final = boolean(event["finished"]) is True and deadline < captured
        imminent = (boolean(event["is_next"]) is True and boolean(event["finished"]) is False
                    and captured < deadline and int(manifest["event"]) == gw
                    and timestamp(manifest["event_deadline"]) == deadline)
        verified = past_final or imminent
        rows.append({
            "season": season, "gameweek": gw,
            "deadline_utc": deadline.isoformat() if verified else "",
            "reported_deadline_utc": deadline.isoformat(),
            "verification_status": VERIFIED if verified else "unverified_provisional",
            "evidence": "finished_event_in_archived_api" if past_final else
                        "is_next_event_in_predeadline_api" if imminent else "provisional",
            "source_url": source_url, "original_endpoint": "https://fantasy.premierleague.com/api/bootstrap-static/",
            "archive_url": archive_url, "source_capture_utc": captured.isoformat(),
            "source_body_sha256": digest(body), "corroborating_source_url": "",
        })
    return rows


def reconstruct_deadlines(raw_dir: Path, seasons: list[str]) -> list[dict]:
    """Entirely offline after fetch; check every pinned file and write no inferred values."""
    lock = deadline_lock()
    captures = {c["season"]: c for c in lock["captures"]}
    rows = []
    for season in seasons:
        capture = captures[season]
        data = {}
        for name, expected in capture["files"].items():
            content = (raw_dir / "deadlines" / season / name).read_bytes()
            if len(content) != expected["bytes"] or digest(content) != expected["sha256"]:
                raise ValueError(f"Deadline archive checksum mismatch: {season}/{name}")
            data[name] = content
        manifest = json.loads(data["manifest.json"])
        with gzip.GzipFile(fileobj=io.BytesIO(data["bootstrap-static.json.gz"])) as compressed:
            body = compressed.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("Uncompressed archived API body exceeds limit")
        url = (f"https://raw.githubusercontent.com/{lock['repository']}/{lock['revision']}/"
               f"{capture['path']}/bootstrap-static.json.gz")
        rows.extend(extract_deadlines(season, manifest, body, url))
    by_key = {(r["season"], r["gameweek"]): r for r in rows}
    for check in lock["official_cross_checks"]:
        row = by_key.get((check["season"], check["gameweek"]))
        if row is not None:
            if timestamp(row["deadline_utc"]) != timestamp(check["deadline_utc"]):
                raise ValueError(f"Archive disagrees with official calendar check: {check}")
            row["corroborating_source_url"] = check["source_url"]
    return sorted(rows, key=lambda r: (r["season"], r["gameweek"]))


def write_deadlines(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("No deadline archive rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
