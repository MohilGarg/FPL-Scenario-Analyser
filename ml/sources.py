"""Bounded, revision-pinned downloads with a verified offline cache."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def source_lock() -> dict:
    lock = json.loads((ROOT / "source-lock.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{40}", lock["revision"]):
        raise ValueError("Source revision must be an immutable Git commit SHA")
    return lock


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(raw_dir: Path, seasons: list[str]) -> dict:
    """Fetch only four small files per season; never clone the upstream repository."""
    lock = source_lock()
    previous = None
    if (raw_dir / "manifest.json").exists():
        previous = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
        if previous["revision"] != lock["revision"]:
            raise ValueError("Cache revision differs; use a fresh raw directory")
    entries = dict(previous["files"]) if previous else {}
    total_bytes = 0
    for season in seasons:
        if season not in lock["seasons"]:
            raise ValueError(f"Unsupported season: {season}")
        for filename in lock["files"]:
            key = f"{season}/{filename}"
            url = (f"https://raw.githubusercontent.com/{lock['repository']}/"
                   f"{lock['revision']}/data/{key}")
            path = raw_dir / key
            if key in entries and path.exists():
                data = path.read_bytes()
                if digest(data) != entries[key]["sha256"]:
                    raise ValueError(f"Cache checksum mismatch: {key}")
            else:
                request = Request(url, headers={"User-Agent": "FPL-Scenario-Analyser-ML/1"})
                with urlopen(request, timeout=60) as response:
                    data = response.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError(f"File exceeds download limit: {key}")
                if total_bytes + len(data) > MAX_TOTAL_BYTES:
                    raise ValueError("Total download limit exceeded")
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".csv.part")
                temporary.write_bytes(data)
                temporary.replace(path)
                entries[key] = {"url": url, "sha256": digest(data), "bytes": len(data)}
                # Persist after each file so interrupted downloads can resume.
                raw_dir.mkdir(parents=True, exist_ok=True)
                manifest = {"repository": lock["repository"], "revision": lock["revision"],
                            "retrieved_at": datetime.now(timezone.utc).isoformat(), "files": entries}
                (raw_dir / "manifest.json").write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
            total_bytes += len(data)
    return verify_cache(raw_dir, seasons)


def verify_cache(raw_dir: Path, seasons: list[str]) -> dict:
    lock = source_lock()
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["revision"] != lock["revision"] or manifest["repository"] != lock["repository"]:
        raise ValueError("Cache does not match the source lock")
    for season in seasons:
        if season not in lock["seasons"]:
            raise ValueError(f"Unsupported season: {season}")
        for filename in lock["files"]:
            key = f"{season}/{filename}"
            data = (raw_dir / key).read_bytes()
            entry = manifest["files"][key]
            if digest(data) != entry["sha256"] or len(data) != entry["bytes"]:
                raise ValueError(f"Cache checksum mismatch: {key}")
    return manifest
