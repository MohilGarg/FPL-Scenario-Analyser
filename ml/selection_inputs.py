"""Prospective selection-evidence collection. Credentials and raw reports never enter Git."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from fpl_forfeit.api import FPLClient
from fpl_forfeit.assistant.external_forecasts import read_batch
from fpl_forfeit.assistant.selection_inputs import (
    collect_calendar,
    competition_matches,
    official_availability,
    provider_selection,
    save_evidence,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("snapshots/ml/selection"))
    parser.add_argument("--club-map", type=Path)
    parser.add_argument("--competitions", nargs="+", choices=["CL", "PL"], default=["CL"])
    args = parser.parse_args()
    snapshot = FPLClient().forecast_snapshot()
    bootstrap = snapshot["bootstrap"]
    official = official_availability(
        bootstrap, datetime.fromisoformat(snapshot["source_finished_utc"])
    )
    save_evidence(official, args.output_dir)
    status = {"official_availability": {"rows": len(official["rows"]), "state": "captured"}}
    candidates = sorted(
        Path("snapshots/ml/external").glob("*-sideline_quant-*.json.gz"), reverse=True
    )
    if candidates:
        batch = read_batch(candidates[0])
        evidence = provider_selection(batch)
        save_evidence(evidence, args.output_dir)
        status["provider_selection"] = {
            "rows": len(evidence["rows"]),
            "state": "captured_original_source_time",
        }
    token = os.getenv("FOOTBALL_DATA_TOKEN")
    if not token or not args.club_map:
        status["cross_competition"] = {
            "state": "not_configured",
            "reason": "Documented free API requires token and explicit season club map; no coverage invented.",
        }
    else:
        mapping = json.loads(args.club_map.read_bytes())
        for competition in args.competitions:
            try:
                payload, retrieved = collect_calendar(
                    competition, token, args.output_dir / "http-cache"
                )
                evidence = competition_matches(payload, bootstrap, mapping, retrieved)
                save_evidence(evidence, args.output_dir)
                status[competition] = {"state": "captured", "rows": len(evidence["rows"])}
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                status[competition] = {"state": "unavailable", "reason": type(error).__name__}
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
