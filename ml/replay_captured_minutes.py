"""Counterfactual challenger comparison on original pre-deadline official inputs.

Original statistical forecasts are reproduced first. New models did not exist
then, so their scores are retrospective development, not prospective confirmation.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from fpl_forfeit.api import parse_forecast_catalogue, parse_ml_history
from fpl_forfeit.forecasting.horizon import forecast_horizon
from ml.joint_minutes_benchmark import capture, summarize
from ml.minutes_data import groups
from ml.prediction_benchmark import write_json
from ml.recording.archive import read_bundle, stamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, default=Path("ml/artifacts/sprint-collection-audit/archive")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("ml/artifacts/captured-minutes-sprint-v2")
    )
    parser.add_argument("--samples", type=int, default=1024)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bundles = [read_bundle(p) for p in args.archive.glob("*/capture.zip")]
    actuals = {m["gameweek"]: r for m, r in bundles if m["stage"] == "actual"}
    chosen = {}
    for m, r in bundles:
        gw = m["gameweek"]
        if m["stage"] == "actual" or gw not in actuals:
            continue
        if gw not in chosen or stamp(m["captured_at"]) > stamp(chosen[gw][0]["captured_at"]):
            chosen[gw] = m, r
    membership = {}
    proofs = {}
    for gw, (manifest, record) in sorted(chosen.items()):
        snapshot = record["official_snapshot"]
        cutoff = stamp(record["feature_asof_utc"])
        cat = parse_forecast_catalogue(snapshot["bootstrap"], snapshot["fixtures"])
        cat = replace(
            cat,
            fixtures=tuple(f for f in cat.fixtures if f.gameweek == gw),
            gameweeks=tuple(g for g in cat.gameweeks if g.id == gw),
            fetched_at=stamp(record["statistical_source_timestamp"]),
        )
        original = forecast_horizon(
            cat, (gw,), samples=record["statistical_samples"], seed=record["statistical_seed"]
        )
        maximum = max(
            abs(
                row[gw]["expected_points"]
                - record["statistical_predictions"][str(pid)][str(gw)]["expected_points"]
            )
            for pid, row in original.players.items()
        )
        if maximum > 1e-12:
            raise ValueError("Original production statistical forecast does not reproduce")
        proofs[gw] = {
            "original_statistical_max_difference": maximum,
            "capture": manifest["captured_at"],
            "code_commit": manifest["context"]["code_commit"],
        }
        prior = {}
        targets = {}
        actual = {int(e["id"]): e["stats"] for e in actuals[gw]["source"]["payload"]["elements"]}
        for pid, player in cat.players.items():
            history = record["official_histories"].get(str(pid))
            prior[pid] = (
                parse_ml_history(history["payload"], snapshot["fixtures"], pid, gw, cutoff)
                if history
                else []
            )
            ids = record["statistical_predictions"][str(pid)][str(gw)]["fixture_ids"]
            if len(ids) != 1:
                raise ValueError("This captured-input replay requires single-fixture target GWs")
            targets[pid] = [
                {
                    "team_id": player.team_id,
                    "fixture_id": ids[0],
                    "total_points": actual[pid]["total_points"],
                    **actual[pid],
                }
            ]
            subset = groups(prior[pid], targets[pid][0])
            subset["fixed_selection_candidates"] = (
                record["statistical_predictions"][str(pid)][str(gw)]["expected_minutes"] or 0
            ) >= 45
            subset["uncertain_availability"] = player.chance_of_playing not in (
                None,
                100,
            ) or player.availability_status in {"d", "i", "s", "u", "n"}
            membership[gw, pid] = subset
        ctx = {
            "gw": gw,
            "cutoff": cutoff,
            "catalogue": cat,
            "prior": prior,
            "targets": targets,
            "quarantine": set(),
            "captured_input_replay": True,
            "season": cat.scoring_season,
        }
        for method in (
            "statistical",
            "minutes_v1",
            "empirical",
            "fitted_greedy",
            "fitted_balanced",
        ):
            for seed in (20261105, 20261205):
                path = args.output_dir / f"GW{gw}-{method}-{seed}-{args.samples}.json"
                if path.exists():
                    continue
                result = capture(
                    ctx, method, args.samples, seed, Path("ml/artifacts/minutes-sprint-v2")
                )
                write_json(path, result)
        print(f"Replayed original and challenger development GW{gw}", flush=True)
    runs = [json.loads(p.read_bytes()) for p in args.output_dir.glob(f"GW*-*-{args.samples}.json")]
    result = summarize(
        runs,
        membership=membership,
        scope="Counterfactual development replay of GW7-9 original timestamped official inputs with current fitted artifacts; not original challenger forecasts or untouched confirmation.",
    )
    result["original_forecast_verification"] = proofs
    write_json(args.output_dir / "results.json", result)
    write_json(Path("ml/reports/CAPTURED_MINUTES_SPRINT.json"), result)


if __name__ == "__main__":
    main()
