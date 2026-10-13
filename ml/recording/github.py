"""Append-only GitHub release storage. No credentials are stored or printed."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fpl_forfeit.assistant.prospective import canonical, immutable

from .archive import TAG, digest, policy, read_bundle, receipt, stamp, validate_release


class StorageError(RuntimeError):
    pass


class GitHubStore:
    def __init__(
        self,
        repository: str,
        *,
        executable: str = "gh",
        sleep=time.sleep,
        now=lambda: datetime.now(UTC),
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.repository, self.executable, self.sleep, self.now = repository, executable, sleep, now
        self.base = f"repos/{repository}"

    def command(self, args: list[str], body: dict | None = None) -> bytes:
        for attempt in range(3):
            try:
                result = subprocess.run(
                    [self.executable, *args],
                    input=canonical(body) if body is not None else None,
                    capture_output=True,
                    timeout=90,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                if attempt == 2:
                    raise StorageError("GitHub command unavailable/timed out") from exc
            else:
                if result.returncode == 0:
                    return result.stdout
                error = result.stderr.decode(errors="replace")
                if "HTTP 404" in error:
                    raise FileNotFoundError(args[1])
                # Retrying GET/PATCH or an ambiguous create/upload never uses --clobber.
                if (
                    not any(
                        s in error
                        for s in (
                            "HTTP 429",
                            "HTTP 500",
                            "HTTP 502",
                            "HTTP 503",
                            "HTTP 504",
                            "timeout",
                            "connection",
                            "TLS",
                        )
                    )
                    or attempt == 2
                ):
                    raise StorageError(
                        f"GitHub {args[0]} failed; inspect permissions/service status (exit {result.returncode})"
                    )
            self.sleep((2, 5)[attempt])
        raise AssertionError("Unreachable")

    def api(self, route: str, *, method: str = "GET", body: dict | None = None):
        args = ["api", route, "--method", method, "-H", "X-GitHub-Api-Version: 2026-03-10"]
        if body is not None:
            args.extend(["--input", "-"])
        raw = self.command(args, body)
        return json.loads(raw) if raw else None

    def releases(self) -> list[dict]:
        records = []
        for page in range(1, 5):
            rows = self.api(f"{self.base}/releases?per_page=100&page={page}")
            records.extend(r for r in rows if TAG.fullmatch(r["tag_name"]))
            if len(rows) < 100:
                return records
        raise StorageError("Release listing bound reached; archive needs review")

    def release(self, tag: str) -> dict | None:
        if not TAG.fullmatch(tag):
            raise ValueError("Unknown recording tag")
        try:
            return self.api(f"{self.base}/releases/tags/{tag}")
        except FileNotFoundError:
            return None

    def fetch(self, release: dict, root: Path, *, published: bool = True) -> Path:
        folder = root / release["tag_name"]
        if not TAG.fullmatch(release["tag_name"]):
            raise ValueError("Unknown release path")
        path = folder / "capture.zip"
        if not path.exists():
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                self.command(
                    [
                        "release",
                        "download",
                        release["tag_name"],
                        "--repo",
                        self.repository,
                        "--pattern",
                        "capture.zip",
                        "--dir",
                        temporary,
                    ]
                )
                candidate = Path(temporary) / "capture.zip"
                validate_release(release, candidate, published=published)
                if (
                    not immutable(path, candidate.read_bytes())
                    and path.read_bytes() != candidate.read_bytes()
                ):
                    raise StorageError("Local archive conflict")
        validate_release(release, path, published=published)
        if published:
            evidence = canonical(receipt(release))
            # Release notes and download counts are mutable; receipts retain first observed proof.
            immutable(folder / "release.json", evidence)
        return path

    def finish_draft(self, release: dict, root: Path) -> dict:
        assets = [a for a in release["assets"] if a["name"] == "capture.zip"]
        if len(assets) != 1 or assets[0]["state"] != "uploaded":
            raise StorageError(
                f"Incomplete draft {release['tag_name']}; recover original bundle, never recreate it"
            )
        bundle = self.fetch(release, root, published=False)
        manifest, _ = validate_release(release, bundle, published=False)
        if manifest["stage"] != "actual" and stamp(
            manifest["deadline_utc"]
        ) - self.now() < timedelta(seconds=policy()["minimum_publish_lead_seconds"]):
            raise StorageError("Too late to publish draft forecast safely; leave the gap visible")
        self.api(
            f"{self.base}/releases/{release['id']}",
            method="PATCH",
            body={"draft": False, "make_latest": "false"},
        )
        published = self.release(release["tag_name"])
        validate_release(published, bundle)
        immutable(bundle.parent / "release.json", canonical(receipt(published)))
        return published

    def publish(self, bundle: Path, root: Path) -> dict:
        manifest, _ = read_bundle(bundle)
        if stamp(manifest["captured_at"]) > self.now():
            raise StorageError("Cannot publish a future-dated capture")
        from .archive import tag_for

        tag = tag_for(manifest["gameweek"], manifest["stage"])
        old = self.release(tag)
        if old:
            if any(a["name"] == "capture.zip" for a in old["assets"]):
                existing = self.fetch(old, root, published=not old["draft"])
                if digest(existing.read_bytes()) != digest(bundle.read_bytes()):
                    raise StorageError("Refusing to replace earlier release/candidate")
                return self.finish_draft(old, root) if old["draft"] else old
            raise StorageError(
                "Earlier empty draft reserved this slot; recover original candidate explicitly"
            )
        if (
            sum(a["size"] for r in self.releases() for a in r["assets"]) + bundle.stat().st_size
            > policy()["max_archive_bytes"]
        ):
            raise StorageError("512 MiB archive cap reached; no automatic deletion")
        if manifest["stage"] != "actual" and stamp(
            manifest["deadline_utc"]
        ) - self.now() < timedelta(seconds=policy()["minimum_publish_lead_seconds"]):
            raise StorageError("Too late for durable forecast publication")
        note = {
            k: manifest[k] for k in ("season", "gameweek", "stage", "captured_at", "deadline_utc")
        }
        note["bundle_sha256"] = digest(bundle.read_bytes())
        note["target_gameweeks"] = manifest.get("target_gameweeks", [manifest["gameweek"]])
        body = {
            "tag_name": tag,
            "target_commitish": manifest["context"]["code_commit"],
            "name": f"FPL {manifest['season']} GW{manifest['gameweek']} {manifest['stage']} capture",
            "body": "Public football archive; authoritative provenance is inside capture.zip.\n```json\n"
            + json.dumps(note, sort_keys=True)
            + "\n```",
            "draft": True,
            "prerelease": True,
            "make_latest": "false",
        }
        try:
            release = self.api(f"{self.base}/releases", method="POST", body=body)
        except StorageError:
            # A timed-out POST can still have reserved the immutable slot server-side.
            release = self.release(tag)
            if release is None:
                raise
        reserved = json.loads(release["body"].split("```json\n", 1)[1].split("\n```", 1)[0])
        if reserved["bundle_sha256"] != digest(bundle.read_bytes()):
            raise StorageError("Another original candidate reserved this slot; do not overwrite it")
        try:
            self.command(["release", "upload", tag, str(bundle), "--repo", self.repository])
        except StorageError:
            # An upload timeout after successful storage is recoverable by hash verification.
            uploaded = self.release(tag)
            validate_release(uploaded, bundle, published=False)
        return self.finish_draft(self.release(tag), root)

    def recover(self, bundle: Path, root: Path) -> dict:
        """Upload the exact original candidate to its empty draft; never use new inputs."""
        manifest, _ = read_bundle(bundle)
        from .archive import tag_for

        release = self.release(tag_for(manifest["gameweek"], manifest["stage"]))
        if release is None or not release["draft"] or release["assets"]:
            raise StorageError("Recovery requires the original empty draft")
        note = json.loads(release["body"].split("```json\n", 1)[1].split("\n```", 1)[0])
        if note["bundle_sha256"] != digest(bundle.read_bytes()):
            raise StorageError("Recovery bytes do not match the original reserved candidate")
        if manifest["stage"] != "actual" and stamp(manifest["deadline_utc"]) <= self.now():
            raise StorageError("Cannot recover a forecast after its deadline")
        self.command(
            ["release", "upload", release["tag_name"], str(bundle), "--repo", self.repository]
        )
        return self.finish_draft(self.release(release["tag_name"]), root)

    def heartbeat(self, code_commit: str) -> None:
        """A tiny monthly branch commit prevents 60-day repository inactivity."""
        branch = policy()["heartbeat_branch"]
        route = f"{self.base}/contents/ml/recording/heartbeat.json"
        try:
            old = self.api(f"{route}?ref={branch}")
        except FileNotFoundError:
            old = None
        month = self.now().strftime("%Y-%m")
        if old and json.loads(base64.b64decode(old["content"]))["month"] == month:
            return
        if old is None:
            try:
                self.api(f"{self.base}/git/ref/heads/{branch}")
            except FileNotFoundError:
                self.api(
                    f"{self.base}/git/refs",
                    method="POST",
                    body={"ref": f"refs/heads/{branch}", "sha": code_commit},
                )
        content = {
            "month": month,
            "observed_at": self.now().isoformat(),
            "season": policy()["season"],
            "note": "Operational keepalive only; no datasets, predictions or credentials. Do not merge this branch.",
        }
        body = {
            "branch": branch,
            "message": f"Prospective recording activity heartbeat {month}",
            "content": base64.b64encode(canonical(content)).decode(),
        }
        if old:
            body["sha"] = old["sha"]
        self.api(route, method="PUT", body=body)
