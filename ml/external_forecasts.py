"""Offline external-forecast collection/import CLI; never executes in web requests."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fpl_forfeit.assistant.external_forecasts import (
    collect_sources,
    parse_manual_csv,
    save_batch,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--output-dir", type=Path, default=Path("snapshots/ml/external"))
    manual = sub.add_parser("import-csv")
    manual.add_argument("csv", type=Path)
    manual.add_argument("--metadata", type=Path, required=True)
    manual.add_argument("--official-input", type=Path, required=True)
    manual.add_argument("--fixtures", type=Path)
    manual.add_argument("--exceptions", type=Path)
    manual.add_argument("--output-dir", type=Path, default=Path("snapshots/ml/external"))
    args = parser.parse_args()
    if args.command == "collect":
        result = collect_sources(None, args.output_dir)
        print(json.dumps(result, indent=2))
        if not any(s["state"] == "ready" for s in result.values()):
            raise SystemExit(1)
    else:
        # Retrieval is the import time, never a user-supplied historical date.
        batch = parse_manual_csv(
            args.csv.read_text(encoding="utf-8-sig"), json.loads(args.metadata.read_text()),
            json.loads(args.official_input.read_text()), datetime.now(UTC),
            json.loads(args.fixtures.read_text()) if args.fixtures else None,
            json.loads(args.exceptions.read_text()) if args.exceptions else None,
        )
        print(json.dumps({"path": str(save_batch(batch, args.output_dir)), "rows": len(batch.rows), "redistribution_allowed": False}))


if __name__ == "__main__":
    main()
