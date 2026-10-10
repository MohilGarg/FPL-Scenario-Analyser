"""Record real live snapshots, replay forecasts and evaluate against official finals.

python -m fpl_forfeit.evaluation --help
Snapshots and predictions are intentionally stored outside Git in snapshots/*.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .api import FPLAPIError, FPLClient, load_snapshot, save_snapshot, state_from_snapshot
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
    provenance: str = "recorded",
) -> dict[str, Any]:
    created = datetime.now(UTC)
    result = outlook(state, samples=samples, seed=seed, allow_tied_last=allow_tied_last)
    return {
        "schema_version": 2,
        "recorded_at": created.isoformat(),
        "provenance": provenance,
        "input_sha256": hashlib.sha256(json.dumps(state.raw, sort_keys=True).encode()).hexdigest()
        if state.raw
        else None,
        "input_event_final": state.event_final,
        "league_id": state.league_id,
        "gameweek": state.gameweek,
        "source": result["source"],
        "model_version": result["model_version"],
        "tie_rule": result["tie_rule"],
        "simulations": result["simulations"],
        "seed": result["seed"],
        "estimated": result["estimated"],
        "managers": result["managers"],
        "coverage": result["coverage"],
        "assumptions": result["assumptions"],
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
        recorded_at = record.get("recorded_at")
        if not recorded_at or record.get("provenance") != "recorded":
            raise ValueError(
                "Evaluation needs contemporaneously recorded forecasts. Replays and legacy records lack that evidence."
            )
        created, source = (
            datetime.fromisoformat(recorded_at),
            datetime.fromisoformat(record["source"]),
        )
        if (
            not created.tzinfo
            or not source.tzinfo
            or not record["estimated"]
            or record.get("input_event_final")
            or source >= final.fetched_at
            or created >= final.fetched_at
            or not 0 <= (created - source).total_seconds() <= 600
        ):
            raise ValueError("Only predictions saved before the final snapshot can be evaluated.")
        if record["tie_rule"] not in {"include", "strict"}:
            raise ValueError("Unknown recorded tie rule.")
        rows = {r["entry_id"]: r for r in record["managers"]}
        if rows.keys() != scores.keys() or len(rows) != len(record["managers"]):
            raise ValueError("Membership changed or is incomplete; do not silently drop managers.")
        losers = last_ids(scores, record["tie_rule"] == "include")
        errors = []
        snapshot_brier, snapshot_logs = [], []
        for entry, row in rows.items():
            p = row["last_probability"]
            if p is None or not 0 <= p <= 1:
                raise ValueError("Every manager requires a probability between zero and one.")
            observed = int(entry in losers)
            squared.append((p - observed) ** 2)
            snapshot_brier.append((p - observed) ** 2)
            # Clip finite Monte Carlo estimates for reporting log loss; Brier is unclipped.
            clipped = min(1 - 1e-6, max(1e-6, p))
            logs.append(-math.log(clipped if observed else 1 - clipped))
            snapshot_logs.append(logs[-1])
            bins[min(9, int(p * 10))].append((p, observed))
            errors.append(abs(row["projected_score"] - scores[entry]))
        results.append(
            {
                "source": record["source"],
                "recorded_at": recorded_at,
                "model_version": record["model_version"],
                "mean_binary_brier": sum(snapshot_brier) / len(snapshot_brier),
                "mean_binary_log_loss_clipped": sum(snapshot_logs) / len(snapshot_logs),
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


def timeline(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare actual forecast records without requiring a completed event."""
    if not records:
        raise ValueError("Provide at least one forecast record.")
    ordered = sorted(records, key=lambda r: datetime.fromisoformat(r["source"]))
    identity = {(r["league_id"], r["gameweek"], r["tie_rule"], r["model_version"]) for r in ordered}
    if len(identity) != 1:
        raise ValueError("A timeline requires the same league, Gameweek, model and tie rule.")
    previous, snapshots = {}, []
    for record in ordered:
        rows = {r["entry_id"]: r for r in record["managers"]}
        if len(rows) != len(record["managers"]) or (previous and rows.keys() != previous.keys()):
            raise ValueError("Timeline membership is incomplete or changed.")
        snapshots.append(
            {
                "source": record["source"],
                "recorded_at": record.get("recorded_at"),
                "provenance": record.get("provenance", "legacy"),
                "managers": [
                    {
                        "entry_id": entry,
                        "projected_score": row["projected_score"],
                        "last_probability": row["last_probability"],
                        "risk_change": round(
                            row["last_probability"] - previous[entry]["last_probability"], 4
                        )
                        if previous
                        else None,
                    }
                    for entry, row in rows.items()
                ],
            }
        )
        previous = rows
    first = ordered[0]
    return {
        "league_id": first["league_id"],
        "gameweek": first["gameweek"],
        "model_version": first["model_version"],
        "tie_rule": first["tie_rule"],
        "note": "Changes describe model estimates at recorded times; repeated observations of one Gameweek are correlated. Replay provenance is explicit.",
        "snapshots": snapshots,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="Save actual current FPL inputs and forecast")
    record.add_argument("league_id", type=int)
    record.add_argument("--directory", type=Path, default=Path("snapshots"))
    record.add_argument("--gameweek", type=int)
    replay = commands.add_parser(
        "replay", help="Forecast from saved inputs, never reconstruct old live states"
    )
    replay.add_argument("snapshot", type=Path)
    replay.add_argument("--output", type=Path)
    for command in (record, replay):
        command.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
        command.add_argument("--seed", type=int, default=DEFAULT_SEED)
        command.add_argument("--strict-last", action="store_true")
    score = commands.add_parser(
        "evaluate", help="Score saved forecasts against official final snapshot"
    )
    score.add_argument("--final", type=Path, required=True)
    score.add_argument("predictions", type=Path, nargs="+")
    score.add_argument("--output", type=Path)
    trend = commands.add_parser("timeline", help="Compare saved forecasts throughout one Gameweek")
    trend.add_argument("predictions", type=Path, nargs="+")
    trend.add_argument("--output", type=Path)
    final = commands.add_parser(
        "final", help="Fetch and save official final inputs for all current members"
    )
    final.add_argument("league_id", type=int)
    final.add_argument("--gameweek", type=int, required=True)
    final.add_argument("--directory", type=Path, default=Path("snapshots"))
    args = parser.parse_args()
    if args.command in {"evaluate", "timeline"}:
        records = [json.loads(p.read_text(encoding="utf-8")) for p in args.predictions]
        result = (
            evaluate(records, state_from_snapshot(load_snapshot(args.final)))
            if args.command == "evaluate"
            else timeline(records)
        )
        _output(result, args.output)
        return
    if args.command == "final":
        snapshot = FPLClient().fetch_snapshot(
            args.league_id, gameweek=args.gameweek, max_entries=50
        )
        state = state_from_snapshot(snapshot)
        if not state.event_final or any(m.official_points is None for m in state.managers):
            parser.error("This Gameweek is not officially final with complete member scores.")
        output = args.directory / f"league-{state.league_id}-gw-{state.gameweek}-final-input.json"
        save_snapshot(snapshot, output)
        print(f"Saved official final inputs: {output}")
        return
    snapshot = (
        FPLClient().fetch_snapshot(args.league_id, gameweek=args.gameweek, max_entries=50)
        if args.command == "record"
        else load_snapshot(args.snapshot)
    )
    state = state_from_snapshot(snapshot)
    if args.command == "record" and state.event_final:
        parser.error("This Gameweek is already final. Use final to record its result.")
    result = prediction_record(
        state,
        samples=args.samples,
        seed=args.seed,
        allow_tied_last=not args.strict_last,
        provenance="recorded" if args.command == "record" else "replay",
    )
    if args.command == "record":
        stamp = state.fetched_at.strftime("%Y%m%dT%H%M%S%fZ")
        stem = f"league-{state.league_id}-gw-{state.gameweek}-{stamp}"
        save_snapshot(snapshot, args.directory / f"{stem}-input.json")
        output = args.directory / f"{stem}-forecast.json"
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved actual inputs and forecast: {output}")
    else:
        _output(result, args.output)


def _output(result: dict[str, Any], output: Path | None) -> None:
    payload = json.dumps(result, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(f"Saved {output}")
    else:
        print(payload)


def main() -> None:
    try:
        _main()
    except (FPLAPIError, ValueError, OSError) as exc:
        raise SystemExit(f"Evaluation error: {exc}") from None


if __name__ == "__main__":
    main()
