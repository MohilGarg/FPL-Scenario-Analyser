"""Counterfactual minutes diagnostics on genuinely timestamped GW7-9 inputs.

The new challenger did not exist at those deadlines. These are explicitly
retrospective development replays, not original prospective forecasts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from fpl_forfeit.api import parse_forecast_catalogue, parse_ml_history
from fpl_forfeit.assistant.minutes_model import MinutesModel
from ml.prediction_benchmark import probability_scores, write_json
from ml.recording.archive import read_bundle, stamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, default=Path("ml/artifacts/sprint-collection-audit/archive")
    )
    args = parser.parse_args()
    bundles = [read_bundle(p) for p in args.archive.glob("*/capture.zip")]
    captures = [(m, r) for m, r in bundles if m["stage"] != "actual"]
    actuals = {m["gameweek"]: r for m, r in bundles if m["stage"] == "actual"}
    chosen = {}
    for m, r in captures:
        gw = m["gameweek"]
        if gw not in actuals:
            continue
        if gw not in chosen or stamp(m["captured_at"]) > stamp(chosen[gw][0]["captured_at"]):
            chosen[gw] = (m, r)
    model = MinutesModel()
    rows = []
    for gw, (manifest, record) in sorted(chosen.items()):
        snapshot = record["official_snapshot"]
        catalogue = parse_forecast_catalogue(snapshot["bootstrap"], snapshot["fixtures"])
        cutoff = stamp(record["feature_asof_utc"])
        outcome = {int(e["id"]): e["stats"] for e in actuals[gw]["source"]["payload"]["elements"]}
        for pid, player in catalogue.players.items():
            if (
                pid not in outcome
                or len(record["statistical_predictions"][str(pid)][str(gw)]["fixture_ids"]) != 1
            ):
                continue
            source = record["official_histories"].get(str(pid))
            if source is None:
                continue
            prior = parse_ml_history(source["payload"], snapshot["fixtures"], pid, gw, cutoff)
            neutral = model.predict(
                replace(player, chance_of_playing=None, availability_status="a"), prior
            )
            capped = model.predict(player, prior)
            chance = (
                player.chance_of_playing / 100
                if player.chance_of_playing is not None
                else 0
                if player.availability_status in {"i", "s", "u", "n"}
                else 1
            )
            base_appearance = 1 - neutral.no_show_probability
            recent = prior[-5:]
            reliable = (
                len(recent) == 5
                and sum(r["starts"] for r in recent) / 5 >= 0.8
                and sum(r["minutes"] for r in recent) / 5 >= 60
            )
            rows.append(
                {
                    "gw": gw,
                    "uncertain": chance < 1,
                    "reliable": reliable,
                    "appearance": int(outcome[pid]["minutes"] > 0),
                    "start": int(outcome[pid]["starts"] > 0),
                    "cap_appearance": 1 - capped.no_show_probability,
                    "multiply_appearance": base_appearance * chance,
                    "cap_start": capped.start_probability,
                    "multiply_start": neutral.start_probability * chance,
                }
            )
    report = {
        "scope": "Retrospective development replay of original timestamped inputs; new fitted model was not an original GW7-9 forecast. Single-fixture GWs only; no historical injury or registration reconstruction.",
        "groups": {},
        "cutoffs": {gw: manifest["captured_at"] for gw, (manifest, _) in chosen.items()},
    }
    for name, subset in {
        "all": rows,
        "uncertain_availability": [r for r in rows if r["uncertain"]],
        "reliable_uncertain": [r for r in rows if r["uncertain"] and r["reliable"]],
    }.items():
        report["groups"][name] = {"n": len(subset), "methods": {}}
        for method in ("cap", "multiply"):
            report["groups"][name]["methods"][method] = {
                kind: probability_scores(
                    np.asarray([r[kind] for r in subset]),
                    np.asarray([r[method + "_" + kind] for r in subset]),
                )
                for kind in ("appearance", "start")
            }
    write_json(Path("ml/reports/AVAILABILITY_AUDIT_SPRINT.json"), report)
    print(
        json.dumps(
            {
                g: {
                    "n": v["n"],
                    **{m: vals["appearance"]["brier"] for m, vals in v["methods"].items()},
                }
                for g, v in report["groups"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
