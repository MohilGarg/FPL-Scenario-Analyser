"""Describe forecast disagreements from an original verified capture, without outcomes.

This command never trains, fetches providers or invents historical forecasts. The
minutes-v1 proposal replay is labelled separately from original archived draws.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean

from fpl_forfeit.api import parse_forecast_catalogue, parse_ml_history
from fpl_forfeit.assistant.external_forecasts import batch_from_dict, gameweek_rows
from fpl_forfeit.assistant.playing_time import estimate_minutes
from fpl_forfeit.forecasting.statistics import player_rates
from ml.recording.archive import policy, read_bundle, stamp, tag_for

COMPONENTS = {
    "appearance": "xp_appearance",
    "goals": "xp_goals",
    "assists": "xp_assists",
    "clean_sheets": "xp_clean_sheet",
    "conceded": "xp_conceded",
    "saves": "xp_saves",
    "defensive": "xp_defcon",
    "cards": "xp_cards",
    "own_goals": "xp_own_goal",
    "bonus": "bonus_mean",
}


def compare(first: dict[int, float | None], second: dict[int, float | None]) -> dict:
    """Prediction distance only: neither member is a realised outcome."""
    pairs = [
        (first[pid], second[pid])
        for pid in sorted(set(first) & set(second))
        if first[pid] is not None and second[pid] is not None
    ]
    if not pairs:
        return {"n": 0}
    if any(not math.isfinite(v) for pair in pairs for v in pair):
        raise ValueError("Forecast disagreements require finite observations")
    x, y = zip(*pairs)
    delta = [b - a for a, b in pairs]
    mean_x, mean_y = fmean(x), fmean(y)
    covariance = sum((a - mean_x) * (b - mean_y) for a, b in pairs)
    scale = math.sqrt(sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y))
    return {
        "n": len(pairs),
        "first_mean": mean_x,
        "second_mean": mean_y,
        "mean_second_minus_first": fmean(delta),
        "mean_absolute_disagreement": fmean(abs(d) for d in delta),
        "root_mean_squared_disagreement": math.sqrt(fmean(d * d for d in delta)),
        "pearson_correlation": covariance / scale if scale else None,
        "share_abs_difference_ge2": fmean(abs(d) >= 2 for d in delta),
    }


def raw_minutes(player, rows: list[dict]) -> tuple[dict, bool]:
    """Reproduce the v1 proposal/fallback without using a realised target."""
    try:
        if not rows or rows[-1]["team_id"] != player.team_id:
            raise ValueError("Missing or transferred history")
        estimate = estimate_minutes(player, rows)
        return {
            "minutes": estimate.expected_minutes,
            "start": estimate.start_probability,
            "start60": estimate.start_probability
            * sum(m >= 60 for m in estimate.starting_minutes)
            / len(estimate.starting_minutes),
            "sixty": estimate.sixty_probability,
            "appearance": 1 - estimate.no_show_probability,
        }, False
    except (ValueError, KeyError, TypeError):
        rate = player_rates(player)
        substitute = (1 - rate.start) * rate.cameo_probability
        return {
            "minutes": rate.start * fmean(rate.starting_minutes)
            + substitute * fmean(rate.cameo_minutes),
            "start": rate.start,
            "start60": rate.start * fmean(m >= 60 for m in rate.starting_minutes),
            "sixty": rate.start * fmean(m >= 60 for m in rate.starting_minutes)
            + substitute * fmean(m >= 60 for m in rate.cameo_minutes),
            "appearance": rate.start + substitute,
        }, True


def analyse(bundle: Path) -> dict:
    """Validate original bundle hashes and compare only its common coverage."""
    manifest, record = read_bundle(bundle)
    if manifest["stage"] == "actual" or "minute_model" not in record:
        raise ValueError("An original forecast containing archived minutes-v1 is required")
    gw = manifest["gameweek"]
    snapshot = record["official_snapshot"]
    cutoff = stamp(record["feature_asof_utc"])
    catalogue = parse_forecast_catalogue(snapshot["bootstrap"], snapshot["fixtures"])
    external = {
        batch["source"]: gameweek_rows(
            batch_from_dict(batch), gw, snapshot["fixtures"], snapshot["bootstrap"]
        )
        for batch in record.get("external_forecasts", {}).get("batches", [])
        if batch["source"] in {"fpl_cat", "sideline_quant"}
    }
    if not external:
        raise ValueError("The original capture has no supported external forecast evidence")
    statistical = {
        int(pid): weekly[str(gw)] for pid, weekly in record["statistical_predictions"].items()
    }
    minutes = {
        int(pid): weekly[str(gw)] for pid, weekly in record["minute_model"]["predictions"].items()
    }
    common = set(statistical) & set(minutes)
    for rows in external.values():
        common &= set(rows)
    if not common:
        raise ValueError("No identical player coverage across the available methods")
    single_fixture = {pid for pid in common if len(statistical[pid]["fixture_ids"]) == 1}
    raw, candidate, fallbacks = {}, set(), 0
    for pid in sorted(common):
        player = catalogue.players[pid]
        history = record["official_histories"].get(str(pid))
        rows = (
            parse_ml_history(history["payload"], snapshot["fixtures"], pid, gw, cutoff)
            if history
            else []
        )
        recent = rows[-5:]
        if (
            len(recent) == 5
            and sum(r["starts"] for r in recent) / 5 >= 0.8
            and sum(r["minutes"] for r in recent) / 5 >= 60
        ):
            candidate.add(pid)
        if pid in single_fixture:
            raw[pid], used_fallback = raw_minutes(player, rows)
            fallbacks += used_fallback

    result = {
        "schema_version": 1,
        "scope": f"Descriptive pre-deadline GW{gw} disagreement, not forecast accuracy. No outcomes are read. Original archived internal simulations and source rows only, except explicitly named replayed raw-v1 proposals.",
        "release_url": f"https://github.com/{policy()['repository']}/releases/tag/{tag_for(gw, manifest['stage'])}",
        "record_sha256": manifest["record_sha256"],
        "captured_at": record["captured_at"],
        "feature_asof_utc": record["feature_asof_utc"],
        "deadline_utc": manifest["deadline_utc"],
        "generation_commit": manifest["context"].get("code_commit"),
        "common_rows": len(common),
        "selection_candidate_rows": len(candidate),
        "selection_candidate_definition": "Exactly five preceding cutoff-safe fixture records, mean minutes>=60 and start rate>=.8; independent of target outcome/current forecast.",
        "source_times": {
            batch["source"]: {
                "computed_at": batch["computed_at"],
                "retrieved_at": batch["retrieved_at"],
            }
            for batch in record["external_forecasts"]["batches"]
            if batch["source"] in external
        },
        "missing_sources": sorted({"fpl_cat", "sideline_quant"} - set(external)),
        "raw_minutes_v1_replay_rows": len(raw),
        "raw_minutes_v1_replay_fallbacks": fallbacks,
        "raw_minutes_v1_code_sha256": hashlib.sha256(
            Path("src/fpl_forfeit/assistant/playing_time.py").read_bytes()
        ).hexdigest(),
        "analysis_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "groups": {},
    }
    for name, ids in (("all_common", common), ("fixed_selection_candidates", candidate)):
        points = {
            "statistical": {pid: statistical[pid]["expected_points"] for pid in ids},
            "minutes_v1": {pid: minutes[pid]["expected_points"] for pid in ids},
        } | {
            source: {pid: rows[pid].expected_points for pid in ids}
            for source, rows in external.items()
        }
        point_comparison = {
            f"{second}_minus_{first}": compare(points[first], points[second])
            for first, second in (
                ("statistical", "fpl_cat"),
                ("statistical", "sideline_quant"),
                ("minutes_v1", "fpl_cat"),
                ("minutes_v1", "sideline_quant"),
                ("fpl_cat", "sideline_quant"),
            )
            if first in points and second in points
        }
        selection, components = {}, {}
        sideline = external.get("sideline_quant")
        if sideline is not None:
            for field, provider_field, internal_field in (
                ("expected_minutes", "expected_minutes", "expected_minutes"),
                ("start_probability", "start_probability", "starting_probability"),
                ("appearance_probability", "appearance_probability", "appearance_probability"),
            ):
                selection[f"sideline_minus_archived_minutes_v1_{field}"] = compare(
                    {pid: minutes[pid][internal_field] for pid in ids},
                    {pid: getattr(sideline[pid], provider_field) for pid in ids},
                )
            selection["sideline_minus_replayed_raw_v1_start60"] = compare(
                {pid: raw[pid]["start60"] for pid in ids if pid in raw},
                {pid: sideline[pid].source_probabilities.get("p_start60") for pid in ids},
            )
            components = {
                component: compare(
                    {
                        pid: (minutes[pid].get("expected_points_components") or {}).get(component)
                        for pid in ids
                    },
                    {pid: sideline[pid].components.get(provider_field) for pid in ids},
                )
                for component, provider_field in COMPONENTS.items()
            }
        for field, raw_field, simulated_field in (
            ("minutes", "minutes", "expected_minutes"),
            ("start", "start", "starting_probability"),
            ("sixty", "sixty", "probability_60_minutes"),
            ("appearance", "appearance", "appearance_probability"),
        ):
            selection[f"archived_simulation_minus_replayed_raw_v1_{field}"] = compare(
                {pid: raw[pid][raw_field] for pid in ids if pid in raw},
                {pid: minutes[pid][simulated_field] for pid in ids},
            )
        result["groups"][name] = {
            "point_disagreement": point_comparison,
            "selection_disagreement": selection,
            "sideline_minus_archived_minutes_v1_components": components,
        }
    result["limitations"] = [
        "Different computation vintages but all retrieved before common feature cutoff; source age is explicit.",
        "Root-mean-squared disagreement is distance between predictions, not RMSE against outcomes.",
        f"Internal archived metrics use {record['statistical_samples']} samples; source disagreement and internal constraint shifts contain Monte Carlo noise.",
        "Raw-v1 proposal replay is restricted to single-fixture rows; no DGW probabilities are summed or invented.",
        "Sideline p_start60 is P(start and >=60), not all-player P(>=60); compare only replayed raw start-and60 mass.",
        "Points differences are not uniquely attributable to minutes; component and minutes differences are diagnostics, not causal ablations.",
        "No fitted-model forecast was retroactively inserted into this original release; new challenger evaluation is separate.",
    ]
    return result


def write_result(result: dict, output: Path, *, report: bool = False) -> None:
    if output.resolve().is_relative_to(Path("ml/reports").resolve()) and not report:
        raise ValueError("Writing tracked reports requires explicit --report")
    if output.exists() and not report:
        raise FileExistsError("Choose a fresh output path; existing evidence is not overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Original hash-verified forecast capture.zip")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--report", action="store_true", help="Explicitly permit writing the tracked report"
    )
    args = parser.parse_args()
    output = args.output or Path(
        "ml/reports/SOURCE_DISAGREEMENTS_SPRINT.json"
        if args.report
        else "ml/artifacts/source-disagreements/result.json"
    )
    try:
        result = analyse(args.capture)
        write_result(result, output, report=args.report)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "output": str(output),
                "common_rows": result["common_rows"],
                "selection_candidate_rows": result["selection_candidate_rows"],
            }
        )
    )


if __name__ == "__main__":
    main()
