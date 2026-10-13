"""Frozen, paired comparison of recorded predictions against official-final points."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import fmean

from .archive import TAG, policy, stamp, validate_release
from .github import GitHubStore


def metrics(pairs: list[tuple[float, int]]) -> dict:
    if not pairs:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    errors = [predicted - actual for predicted, actual in pairs]
    return {
        "n": len(errors),
        "mae": fmean(abs(e) for e in errors),
        "rmse": math.sqrt(fmean(e * e for e in errors)),
        "bias": fmean(errors),
    }


def evaluate(captures: list[tuple[dict, dict]], actuals: dict[int, dict]) -> dict:
    # Selection is fixed by capture stage, never by target outcomes or measured errors.
    priority = {stage: rank for rank, stage in enumerate(policy()["evaluation_priority"])}
    selected = {}
    for manifest, record in captures:
        gw = manifest["gameweek"]
        if gw not in selected or priority[manifest["stage"]] < priority[selected[gw][0]["stage"]]:
            selected[gw] = (manifest, record)
    groups = {
        "all_roster_paired": {"statistical": [], "ml_with_fallbacks": []},
        "direct_ridge_paired": {"statistical": [], "ridge": []},
    }
    per_gw, exclusions = (
        [],
        {
            "not_officially_final": 0,
            "unknown_actual": 0,
            "unknown_prediction": 0,
            "final_deadline_changed_or_crossed": 0,
        },
    )
    for gw, (manifest, record) in sorted(selected.items()):
        actual = actuals.get(gw)
        if actual is None:
            exclusions["not_officially_final"] += 1
            continue
        event = actual["official_final_event"]
        if event.get("finished") is not True or event.get("data_checked") is not True:
            raise ValueError("Evaluation requires official-final actuals")
        # A subsequently changed deadline cannot silently legitimise a late capture.
        if stamp(manifest["captured_at"]) >= min(
            stamp(manifest["deadline_utc"]), stamp(event["deadline_time"])
        ):
            exclusions["final_deadline_changed_or_crossed"] += 1
            continue
        local = {k: {model: [] for model in rows} for k, rows in groups.items()}
        direct = 0
        for pid, weekly in record["statistical_predictions"].items():
            observed = actual["actual_points"].get(str(pid))
            if observed is None:
                exclusions["unknown_actual"] += 1
                continue
            statistical = weekly[str(gw)]["expected_points"]
            ml = record["ml_predictions"][str(pid)][str(gw)]
            hybrid = ml["comparison_points"]
            if statistical is None or hybrid is None:
                exclusions["unknown_prediction"] += 1
                continue
            for group, model, predicted in (
                ("all_roster_paired", "statistical", statistical),
                ("all_roster_paired", "ml_with_fallbacks", hybrid),
            ):
                local[group][model].append((predicted, observed))
            if ml["source"] == "ridge" and ml["expected_points"] is not None:
                direct += 1
                local["direct_ridge_paired"]["statistical"].append((statistical, observed))
                local["direct_ridge_paired"]["ridge"].append((ml["expected_points"], observed))
        for group, models in local.items():
            for model, pairs in models.items():
                groups[group][model].extend(pairs)
        per_gw.append(
            {
                "gameweek": gw,
                "stage": manifest["stage"],
                "captured_at": manifest["captured_at"],
                "lead_hours": (
                    stamp(manifest["deadline_utc"]) - stamp(manifest["captured_at"])
                ).total_seconds()
                / 3600,
                "direct_ridge_rows": direct,
                "metrics": {
                    g: {m: metrics(p) for m, p in models.items()} for g, models in local.items()
                },
            }
        )
    return {
        "selection_rule": "final > update > advance, primary GW only; never selected using outcomes",
        "bias_definition": "mean(prediction - actual), unmultiplied player points",
        "gameweeks": per_gw,
        "exclusions": exclusions,
        "metrics": {g: {m: metrics(p) for m, p in models.items()} for g, models in groups.items()},
        "note": "Paired observations at identical captured cutoffs. ML-with-fallbacks is a hybrid, not pure Ridge. No retraining or model selection.",
    }


def from_releases(store: GitHubStore, root: Path, *, gameweeks: list[int] | None = None) -> dict:
    if gameweeks == []:
        return evaluate([], {})
    releases = [r for r in store.releases() if not r["draft"]]
    if any(r.get("immutable") is not True for r in releases):
        raise ValueError("Mutable archive release is unsuitable for evaluation")
    finals = {
        int(TAG.fullmatch(r["tag_name"])[2]): r
        for r in releases
        if TAG.fullmatch(r["tag_name"])[3] == "actual"
    }
    chosen = {}
    priority = {s: i for i, s in enumerate(policy()["evaluation_priority"])}
    for release in releases:
        match = TAG.fullmatch(release["tag_name"])
        gw, stage = int(match[2]), match[3]
        if stage == "actual" or gw not in finals or gameweeks is not None and gw not in gameweeks:
            continue
        if gw not in chosen or priority[stage] < priority[TAG.fullmatch(chosen[gw]["tag_name"])[3]]:
            chosen[gw] = release
    captures, actuals = [], {}
    for gw, release in sorted(chosen.items()):
        bundle = store.fetch(release, root / "archive")
        captures.append(validate_release(release, bundle))
        bundle = store.fetch(finals[gw], root / "archive")
        _, actuals[gw] = validate_release(finals[gw], bundle)
    return evaluate(captures, actuals)


def markdown(result: dict) -> str:
    lines = [
        "## Frozen prospective comparison",
        "",
        result["selection_rule"],
        "",
        "| Population | Method | N | MAE | RMSE | Bias |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for group, models in result["metrics"].items():
        for name, values in models.items():
            cells = [
                "unknown" if values[k] is None else f"{values[k]:.4f}"
                for k in ("mae", "rmse", "bias")
            ]
            lines.append(f"| {group} | {name} | {values['n']} | " + " | ".join(cells) + " |")
    if not result["gameweeks"]:
        lines.extend(
            [
                "",
                "No matched official-final Gameweeks available yet. No historical scores were backfilled.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "| GW | Selected capture | Lead hours | Direct Ridge rows |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        lines.extend(
            f"| {g['gameweek']} | {g['stage']} | {g['lead_hours']:.1f} | {g['direct_ridge_rows']} |"
            for g in result["gameweeks"]
        )
    lines.extend(
        [
            "",
            result["bias_definition"],
            "",
            result["note"],
            "",
            f"Exclusions: {result['exclusions']}",
        ]
    )
    return "\n".join(lines) + "\n"
