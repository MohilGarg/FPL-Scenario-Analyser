"""Record real live snapshots, replay forecasts and evaluate against official finals.

python -m fpl_forfeit.evaluation --help
Snapshots and predictions are intentionally stored outside Git in snapshots/*.json.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .api import FPLClient, load_snapshot, save_snapshot, state_from_snapshot
from .forecast import DEFAULT_SAMPLES, DEFAULT_SEED, outlook
from .models import LeagueState
from .scoring import last_ids
from .state import current_standings


def prediction_record(
    state: LeagueState,
    *,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    allow_tied_last: bool = True,
) -> dict[str, Any]:
    result = outlook(state, samples=samples, seed=seed, allow_tied_last=allow_tied_last)
    return {
        "schema_version": 1,
        "league_id": state.league_id,
        "gameweek": state.gameweek,
        "source": result["source"],
        "model_version": result["model_version"],
        "tie_rule": result["tie_rule"],
        "simulations": result["simulations"],
        "seed": result["seed"],
        "estimated": result["estimated"],
        "managers": result["managers"],
    }


def evaluate(records: list[dict[str, Any]], final: LeagueState) -> dict[str, Any]:
    if not final.event_final or any(m.official_points is None for m in final.managers):
        raise ValueError(
            "Evaluation requires an officially final Gameweek with all current members' event points."
        )
    scores = {r.manager.entry_id: r.effective_score for r in current_standings(final)}
    squared: list[float] = []
    logs: list[float] = []
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    results = []
    for record in records:
        if (record["league_id"], record["gameweek"]) != (final.league_id, final.gameweek):
            raise ValueError(
                "Prediction and final snapshots must describe the same league and Gameweek."
            )
        if not record["estimated"] or datetime.fromisoformat(record["source"]) >= final.fetched_at:
            raise ValueError("Only predictions saved before the final snapshot can be evaluated.")
        rows = {r["entry_id"]: r for r in record["managers"]}
        if rows.keys() != scores.keys():
            raise ValueError("Membership changed or is incomplete; do not silently drop managers.")
        losers = last_ids(scores, record["tie_rule"] == "include")
        errors = []
        for entry, row in rows.items():
            p = row["last_probability"]
            if p is None or not 0 <= p <= 1:
                raise ValueError("Every manager requires a probability between zero and one.")
            observed = int(entry in losers)
            squared.append((p - observed) ** 2)
            # Clip finite Monte Carlo estimates for reporting log loss; Brier is unclipped.
            clipped = min(1 - 1e-6, max(1e-6, p))
            logs.append(-math.log(clipped if observed else 1 - clipped))
            bins[min(9, int(p * 10))].append((p, observed))
            errors.append(abs(row["projected_score"] - scores[entry]))
        results.append(
            {
                "source": record["source"],
                "last_entry_ids": list(losers),
                "mean_absolute_score_error": sum(errors) / len(errors),
            }
        )
    if not squared:
        raise ValueError("Provide at least one recorded forecast.")
    return {
        "manager_predictions": len(squared),
        "forecast_snapshots": len(records),
        "mean_binary_brier": sum(squared) / len(squared),
        "mean_binary_log_loss_clipped": sum(logs) / len(logs),
        "definition": "Mean binary proper score per manager under each recorded tie convention; "
        "tied-last probabilities are not mutually exclusive. Multiple snapshots of one Gameweek are correlated.",
        "calibration": [
            {
                "range": [i / 10, (i + 1) / 10],
                "count": len(values),
                "mean_forecast": sum(p for p, _ in values) / len(values),
                "observed_frequency": sum(y for _, y in values) / len(values),
            }
            for i, values in enumerate(bins)
            if values
        ],
        "snapshots": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="Save actual current FPL inputs and forecast")
    record.add_argument("league_id", type=int)
    record.add_argument("--directory", type=Path, default=Path("snapshots"))
    replay = commands.add_parser(
        "replay", help="Forecast from saved inputs, never reconstruct old live states"
    )
    replay.add_argument("snapshot", type=Path)
    for command in (record, replay):
        command.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
        command.add_argument("--seed", type=int, default=DEFAULT_SEED)
        command.add_argument("--strict-last", action="store_true")
    score = commands.add_parser(
        "evaluate", help="Score saved forecasts against official final snapshot"
    )
    score.add_argument("--final", type=Path, required=True)
    score.add_argument("predictions", type=Path, nargs="+")
    args = parser.parse_args()
    if args.command == "evaluate":
        result = evaluate(
            [json.loads(p.read_text(encoding="utf-8")) for p in args.predictions],
            state_from_snapshot(load_snapshot(args.final)),
        )
        print(json.dumps(result, indent=2))
        return
    snapshot = (
        FPLClient().fetch_snapshot(args.league_id)
        if args.command == "record"
        else load_snapshot(args.snapshot)
    )
    state = state_from_snapshot(snapshot)
    result = prediction_record(
        state, samples=args.samples, seed=args.seed, allow_tied_last=not args.strict_last
    )
    if args.command == "record":
        stamp = state.fetched_at.strftime("%Y%m%dT%H%M%S%fZ")
        stem = f"league-{state.league_id}-gw-{state.gameweek}-{stamp}"
        save_snapshot(snapshot, args.directory / f"{stem}-input.json")
        output = args.directory / f"{stem}-forecast.json"
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved actual inputs and forecast: {output}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
