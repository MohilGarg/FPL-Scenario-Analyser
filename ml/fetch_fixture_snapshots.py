"""Fetch only the three reviewed small fixtures snapshots, verifying pinned bytes."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen


def main():
    review = json.loads(Path("ml/fixture4-source-review.json").read_bytes())
    root = Path("ml/data/raw/fixture_snapshots")
    root.mkdir(parents=True, exist_ok=True)
    for item in review["snapshots"]:
        if not item["success"]:
            continue
        path = root / (item["capture_utc"] + ".json")
        if not path.exists():
            temp = path.with_suffix(".part")
            try:
                if curl := shutil.which("curl.exe") or shutil.which("curl"):
                    subprocess.run(
                        [
                            curl,
                            "--fail",
                            "--location",
                            "--retry",
                            "2",
                            "--max-time",
                            "40",
                            "--max-filesize",
                            "2000000",
                            "--output",
                            str(temp),
                            item["url"],
                        ],
                        check=True,
                        capture_output=True,
                    )
                else:
                    with urlopen(item["url"], timeout=40) as response:
                        temp.write_bytes(response.read(2_000_001))
                if hashlib.sha256(temp.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError(
                        "Archived bytes changed or unavailable; do not substitute an HTML replay or later capture"
                    )
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Local archive hash mismatch")
        print(f"Verified {path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
