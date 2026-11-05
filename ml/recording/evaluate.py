"""Frozen, paired comparison of recorded predictions against official-final points."""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from random import Random
from statistics import fmean, median

from .archive import TAG, cutoff_policy, policy, stamp, validate_release
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


def probability_metrics(pairs: list[tuple[float, int]]) -> dict:
    if not pairs:
        return {"n": 0, "brier": None, "log_loss": None, "calibration": []}
    if any(not 0 <= p <= 1 or y not in (0, 1) for p, y in pairs):
        raise ValueError("Invalid probability/outcome")
    bins = []
    for index in range(10):
        group = [(p, y) for p, y in pairs if min(int(p * 10), 9) == index]
        if group:
            bins.append(
                {
                    "lower": index / 10,
                    "n": len(group),
                    "forecast": fmean(p for p, _ in group),
                    "observed": fmean(y for _, y in group),
                }
            )
    return {
        "n": len(pairs),
        "brier": fmean((p - y) ** 2 for p, y in pairs),
        "log_loss": -fmean(
            y * math.log(max(1e-12, p)) + (1 - y) * math.log(max(1e-12, 1 - p)) for p, y in pairs
        ),
        "calibration": bins,
    }


def distribution_metrics(records: list[tuple[dict, int]]) -> dict:
    intervals, quantiles = [], {str(q): [] for q in (0.1, 0.5, 0.9)}
    for predicted, actual in records:
        interval = predicted.get("points_interval_80")
        if interval is not None:
            low, high = interval
            if not math.isfinite(low) or not math.isfinite(high) or low > high:
                raise ValueError("Invalid forecast interval")
            width = high - low
            score = width + 10 * max(0, low - actual) + 10 * max(0, actual - high)
            intervals.append((int(low <= actual <= high), width, score))
        for q, losses in quantiles.items():
            value = (predicted.get("points_quantiles") or {}).get(q)
            if value is not None:
                if not math.isfinite(value):
                    raise ValueError("Invalid quantile")
                error = actual - value
                losses.append(max(float(q) * error, (float(q) - 1) * error))
    return {
        "interval_80": {
            "n": len(intervals),
            "coverage": fmean(v[0] for v in intervals) if intervals else None,
            "mean_width": fmean(v[1] for v in intervals) if intervals else None,
            "interval_score": fmean(v[2] for v in intervals) if intervals else None,
        },
        "quantile_pinball": {
            q: {"n": len(v), "loss": fmean(v) if v else None} for q, v in quantiles.items()
        },
        "definition": "Central80% interval score: width+10*tail miss; quantile pinball losses; inclusive coverage for discrete FPL points. Lower scores are better.",
    }


def paired_uncertainty(per_gw: list[dict], groups: dict) -> dict:
    """Resample entire deadlines; never pretend correlated player rows are IID."""
    output = {}
    rng = Random(20261105)
    for group, models in groups.items():
        baseline = next(iter(models))
        for model in list(models)[1:]:
            weeks = [g["metrics"][group] for g in per_gw if g["metrics"][group][model]["n"]]
            if not weeks:
                continue

            def difference(sample, metric, baseline=baseline, model=model):
                total = sum(w[baseline]["n"] for w in sample)

                def combined(name):
                    if metric == "rmse":
                        return math.sqrt(
                            sum(w[name]["n"] * w[name][metric] ** 2 for w in sample) / total
                        )
                    return sum(w[name]["n"] * w[name][metric] for w in sample) / total

                return combined(model) - combined(baseline)

            deltas = {m: [] for m in ("mae", "rmse")}
            if len(weeks) >= 2:
                for _ in range(2000):
                    sampled = rng.choices(weeks, k=len(weeks))
                    for metric, values in deltas.items():
                        values.append(difference(sampled, metric))
            output[f"{group}:{model}-minus-{baseline}"] = {
                "deadline_count": len(weeks),
                "metrics": {
                    metric: {
                        "difference": difference(weeks, metric),
                        "interval_95": [sorted(values)[49], sorted(values)[1949]]
                        if values
                        else None,
                        "deadlines_improved": sum(
                            w[model][metric] < w[baseline][metric] for w in weeks
                        ),
                    }
                    for metric, values in deltas.items()
                },
                "method": "2000 paired whole-Gameweek bootstrap draws; seed20261105; too few deadlines for final confirmation"
                if len(weeks) < 8
                else "2000 paired whole-Gameweek bootstrap draws; seed20261105",
            }
    return output


def evaluate(
    captures: list[tuple[dict, dict]], actuals: dict[int, dict], *, cutoff_name: str = "final"
) -> dict:
    if cutoff_name not in cutoff_policy()["cutoffs"]:
        raise ValueError("Unknown forecast cutoff")
    # Select before reading outcomes. Completion AND independent durable publication must
    # precede the cutoff; nominal cron times and newly obtained historical values never count.
    priority = {stage: rank for rank, stage in enumerate(policy()["evaluation_priority"])}
    selected = {}
    for manifest, record in captures:
        gw = manifest["gameweek"]
        cutoff = stamp(manifest["deadline_utc"]) - timedelta(
            hours=cutoff_policy()["cutoffs"][cutoff_name]
        )
        captured = stamp(manifest["captured_at"])
        published = stamp(manifest.get("durably_published_at", manifest["captured_at"]))
        if captured > cutoff or published > cutoff:
            continue

        def order(m):
            return (stamp(m["captured_at"]), -priority.get(m["stage"], -1))

        if gw not in selected or order(manifest) > order(selected[gw][0]):
            selected[gw] = (manifest, record)
    groups = {
        "all_roster_paired": {"statistical": [], "ml_with_fallbacks": []},
        "predeadline_minutes_ge45_paired": {"statistical": [], "ml_with_fallbacks": []},
        "direct_ridge_paired": {"statistical": [], "ridge": []},
        "fixture_component_paired": {
            "statistical": [],
            "ridge_with_fallbacks": [],
            "fixture_components": [],
        },
        "minutes_model_paired": {"statistical": [], "minutes_model": []},
    }
    challenger_names = sorted(
        {name for _, record in captures for name in record.get("challengers", {})}
    )
    for name in challenger_names:
        groups[f"challenger_{name}_paired"] = {"statistical": [], name: []}
        groups[f"challenger_{name}_selection_paired"] = {"statistical": [], name: []}
    source_names = sorted(
        {
            b["source"]
            for _, r in captures
            for b in r.get("external_forecasts", {}).get("batches", [])
        }
    )
    if any(
        any(
            e.get("ep_next") is not None
            for e in record.get("official_snapshot", {}).get("bootstrap", {}).get("elements", [])
        )
        for _, record in captures
    ):
        source_names = sorted(set(source_names) | {"official_fpl"})
    for source in source_names:
        groups[f"external_{source}_paired"] = {"statistical": [], source: []}
        groups[f"external_{source}_selection_paired"] = {
            "statistical": [],
            source: [],
            "internal_source_mean": [],
        }
    if source_names:
        groups["external_all_source_common"] = {
            name: []
            for name in (
                "statistical",
                *source_names,
                "external_mean",
                "external_median",
                "internal_external_mean",
                *(f"internal_external_without_{source}" for source in source_names),
            )
        }
    calibration = {
        model: {kind: [] for kind in ("appearance", "start", "sixty_minutes")}
        for model in ("statistical", "minutes_model", *challenger_names)
    }
    distributions = {
        model: []
        for model in ("statistical", "fixture_components", "minutes_model", *challenger_names)
    }
    per_gw, exclusions = (
        [],
        {
            "not_officially_final": 0,
            "unknown_actual": 0,
            "unknown_prediction": 0,
            "final_deadline_changed_or_crossed": 0,
            "stale_external_batches": 0,
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
        external_rows = {}
        official_source = record.get("official_snapshot", {})
        if any(
            e.get("ep_next") is not None
            for e in official_source.get("bootstrap", {}).get("elements", [])
        ):
            from fpl_forfeit.assistant.external_forecasts import gameweek_rows, parse_official_fpl

            batch = parse_official_fpl(
                official_source["bootstrap"], stamp(official_source["source_finished_utc"])
            )
            external_rows["official_fpl"] = gameweek_rows(
                batch, gw, official_source["fixtures"], official_source["bootstrap"]
            )
        if record.get("external_forecasts", {}).get("batches"):
            from fpl_forfeit.assistant.external_forecasts import batch_from_dict, gameweek_rows

            official = record["official_snapshot"]
            for batch in record["external_forecasts"]["batches"]:
                normalized = batch_from_dict(batch)
                if not normalized.available_at(stamp(record["feature_asof_utc"])):
                    exclusions["stale_external_batches"] += 1
                    continue
                external_rows[batch["source"]] = gameweek_rows(
                    normalized, gw, official["fixtures"], official["bootstrap"]
                )
        actual_elements = {
            str(e["id"]): e for e in actual.get("source", {}).get("payload", {}).get("elements", [])
        }
        direct = 0
        for pid, weekly in record["statistical_predictions"].items():
            observed = actual["actual_points"].get(str(pid))
            if observed is None:
                exclusions["unknown_actual"] += 1
                continue
            statistical = weekly[str(gw)]["expected_points"]
            ml = record.get("ml_predictions", {}).get(str(pid), {}).get(str(gw), {})
            hybrid = ml.get("comparison_points")
            if statistical is None:
                exclusions["unknown_prediction"] += 1
                continue
            if hybrid is not None:
                for group, model, predicted in (
                    ("all_roster_paired", "statistical", statistical),
                    ("all_roster_paired", "ml_with_fallbacks", hybrid),
                ):
                    local[group][model].append((predicted, observed))
                expected_minutes = weekly[str(gw)].get("expected_minutes")
                if expected_minutes is not None and expected_minutes >= 45:
                    local["predeadline_minutes_ge45_paired"]["statistical"].append(
                        (statistical, observed)
                    )
                    local["predeadline_minutes_ge45_paired"]["ml_with_fallbacks"].append(
                        (hybrid, observed)
                    )
            else:
                exclusions["unknown_prediction"] += 1
            if ml.get("source") == "ridge" and ml.get("expected_points") is not None:
                direct += 1
                local["direct_ridge_paired"]["statistical"].append((statistical, observed))
                local["direct_ridge_paired"]["ridge"].append((ml["expected_points"], observed))
            component = (
                record.get("fixture_component", {})
                .get("predictions", {})
                .get(str(pid), {})
                .get(str(gw), {})
            )
            if component.get("expected_points") is not None and hybrid is not None:
                for name, value in (
                    ("statistical", statistical),
                    ("ridge_with_fallbacks", hybrid),
                    ("fixture_components", component["expected_points"]),
                ):
                    local["fixture_component_paired"][name].append((value, observed))
            minute = (
                record.get("minute_model", {})
                .get("predictions", {})
                .get(str(pid), {})
                .get(str(gw), {})
            )
            if minute.get("expected_points") is not None:
                local["minutes_model_paired"]["statistical"].append((statistical, observed))
                local["minutes_model_paired"]["minutes_model"].append(
                    (minute["expected_points"], observed)
                )
            challenger_rows = {
                name: record.get("challengers", {})
                .get(name, {})
                .get("predictions", {})
                .get(str(pid), {})
                .get(str(gw), {})
                for name in challenger_names
            }
            for name, predicted in challenger_rows.items():
                if predicted.get("expected_points") is None:
                    continue
                local[f"challenger_{name}_paired"]["statistical"].append((statistical, observed))
                local[f"challenger_{name}_paired"][name].append(
                    (predicted["expected_points"], observed)
                )
                if (weekly[str(gw)].get("expected_minutes") or 0) >= 45:
                    local[f"challenger_{name}_selection_paired"]["statistical"].append(
                        (statistical, observed)
                    )
                    local[f"challenger_{name}_selection_paired"][name].append(
                        (predicted["expected_points"], observed)
                    )
            for name, rows in external_rows.items():
                row = rows.get(int(pid))
                if row is not None:
                    local[f"external_{name}_paired"]["statistical"].append((statistical, observed))
                    local[f"external_{name}_paired"][name].append((row.expected_points, observed))
                    if (weekly[str(gw)].get("expected_minutes") or 0) >= 45:
                        local[f"external_{name}_selection_paired"]["statistical"].append(
                            (statistical, observed)
                        )
                        local[f"external_{name}_selection_paired"][name].append(
                            (row.expected_points, observed)
                        )
                        local[f"external_{name}_selection_paired"]["internal_source_mean"].append(
                            ((row.expected_points + statistical) / 2, observed)
                        )
            if source_names and all(
                int(pid) in external_rows.get(name, {}) for name in source_names
            ):
                values = {
                    name: external_rows[name][int(pid)].expected_points for name in source_names
                }
                arms = {
                    "statistical": statistical,
                    **values,
                    "external_mean": fmean(values.values()),
                    "external_median": median(values.values()),
                    "internal_external_mean": fmean([statistical, *values.values()]),
                    **{
                        f"internal_external_without_{name}": fmean(
                            [
                                statistical,
                                *(value for source, value in values.items() if source != name),
                            ]
                        )
                        for name in source_names
                    },
                }
                for name, value in arms.items():
                    local["external_all_source_common"][name].append((value, observed))
            element = actual_elements.get(str(pid), {})
            stats = element.get("stats", {})
            fixture_minutes = [
                s["value"]
                for leg in element.get("explain", [])
                for s in leg.get("stats", [])
                if s.get("identifier") == "minutes" and s.get("value") is not None
            ]
            actual_sixty = (
                int(any(m >= 60 for m in fixture_minutes))
                if fixture_minutes
                else (0 if stats.get("minutes") == 0 else None)
            )
            for model, predicted in (
                ("statistical", weekly[str(gw)]),
                ("minutes_model", minute),
                *challenger_rows.items(),
            ):
                for kind, field, actual_field in (
                    ("appearance", "appearance_probability", "minutes"),
                    ("start", "starting_probability", "starts"),
                ):
                    probability = predicted.get(field)
                    if probability is not None and stats.get(actual_field) is not None:
                        calibration[model][kind].append((probability, int(stats[actual_field] > 0)))
                if predicted.get("probability_60_minutes") is not None and actual_sixty is not None:
                    calibration[model]["sixty_minutes"].append(
                        (predicted["probability_60_minutes"], actual_sixty)
                    )
            for model, predicted in (
                ("statistical", weekly[str(gw)]),
                ("fixture_components", component),
                ("minutes_model", minute),
                *challenger_rows.items(),
            ):
                if predicted.get("expected_points") is not None:
                    distributions[model].append((predicted, observed))
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
                "cutoff": cutoff_name,
                "cutoff_age_hours": (
                    stamp(manifest["deadline_utc"])
                    - timedelta(hours=cutoff_policy()["cutoffs"][cutoff_name])
                    - stamp(manifest["captured_at"])
                ).total_seconds()
                / 3600,
                "direct_ridge_rows": direct,
                "metrics": {
                    g: {m: metrics(p) for m, p in models.items()} for g, models in local.items()
                },
            }
        )
    return {
        "selection_rule": f"Latest completed and durably published capture <= {cutoff_name} cutoff; primary GW only; stage priority breaks equal-time ties; never selected using outcomes",
        "cutoff": cutoff_name,
        "cutoff_policy": cutoff_policy(),
        "eligible_capture_gameweeks": sorted(selected),
        "missing_cutoff_gameweeks": sorted({m["gameweek"] for m, _ in captures} - set(selected)),
        "bias_definition": "mean(prediction - actual), unmultiplied player points",
        "selection_pool_definition": "Original pre-deadline statistical expected GW minutes >=45; same observed rows for each paired method; never selected using actual minutes/points",
        "gameweeks": per_gw,
        "exclusions": exclusions,
        "metrics": {g: {m: metrics(p) for m, p in models.items()} for g, models in groups.items()},
        "probability_metrics": {
            model: {kind: probability_metrics(pairs) for kind, pairs in outcomes.items()}
            for model, outcomes in calibration.items()
        },
        "probability_note": "Appearance/start means any in GW. P60 uses preserved official per-fixture explain minutes, or known total0; never aggregated DGW minutes because30+30 is not a qualifying60-minute fixture. Missing fixture detail stays unknown.",
        "distribution_metrics": {
            model: distribution_metrics(rows) for model, rows in distributions.items()
        },
        "paired_uncertainty": paired_uncertainty(per_gw, groups),
        "note": "Paired observations at identical captured cutoffs. ML-with-fallbacks is a hybrid, not pure Ridge. No retraining or model selection.",
    }


def from_releases(
    store: GitHubStore,
    root: Path,
    *,
    gameweeks: list[int] | None = None,
    cutoff_name: str = "final",
) -> dict:
    if gameweeks == []:
        return evaluate([], {}, cutoff_name=cutoff_name)
    releases = [r for r in store.releases() if not r["draft"]]
    if any(r.get("immutable") is not True for r in releases):
        raise ValueError("Mutable archive release is unsuitable for evaluation")
    finals = {
        int(TAG.fullmatch(r["tag_name"])[2]): r
        for r in releases
        if TAG.fullmatch(r["tag_name"])[3] == "actual"
    }
    chosen = []
    for release in releases:
        match = TAG.fullmatch(release["tag_name"])
        gw, stage = int(match[2]), match[3]
        if stage == "actual" or gw not in finals or gameweeks is not None and gw not in gameweeks:
            continue
        chosen.append((gw, release))
    captures, actuals = [], {}
    for gw, release in sorted(chosen, key=lambda item: (item[0], item[1]["tag_name"])):
        bundle = store.fetch(release, root / "archive")
        captures.append(validate_release(release, bundle))
        if gw not in actuals:
            bundle = store.fetch(finals[gw], root / "archive")
            _, actuals[gw] = validate_release(finals[gw], bundle)
    return evaluate(captures, actuals, cutoff_name=cutoff_name)


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
            f"Missing requested cutoff Gameweeks: {result.get('missing_cutoff_gameweeks', [])}. Coverage is N per paired population; absent source predictions are never zero.",
            "",
            result["note"],
            "",
            f"Exclusions: {result['exclusions']}",
        ]
    )
    if any(
        v["n"]
        for outcomes in result.get("probability_metrics", {}).values()
        for v in outcomes.values()
    ):
        lines.extend(
            ["", "| Method | Event | N | Brier | Log loss |", "| --- | --- | ---: | ---: | ---: |"]
        )
        for model, outcomes in result["probability_metrics"].items():
            for kind, values in outcomes.items():
                if values["n"]:
                    lines.append(
                        f"| {model} | {kind} | {values['n']} | {values['brier']:.4f} | {values['log_loss']:.4f} |"
                    )
        lines.extend(["", result["probability_note"]])
    if result.get("paired_uncertainty"):
        lines.extend(
            [
                "",
                "Paired differences (challenger minus baseline; lower is better):",
                "",
                "| Pair | Deadlines | RMSE difference | 95% GW bootstrap interval | Improved GWs |",
                "| --- | ---: | ---: | --- | ---: |",
            ]
        )
        for name, value in result["paired_uncertainty"].items():
            rmse = value["metrics"]["rmse"]
            interval = rmse["interval_95"]
            text = (
                "insufficient deadlines"
                if interval is None
                else f"[{interval[0]:.4f}, {interval[1]:.4f}]"
            )
            lines.append(
                f"| {name} | {value['deadline_count']} | {rmse['difference']:.4f} | {text} | {rmse['deadlines_improved']} |"
            )
        lines.extend(
            [
                "",
                "Resampling keeps each deadline together. Three observed deadlines cannot establish reliable promotion evidence; intervals are approximate.",
            ]
        )
    if result.get("distribution_metrics"):
        lines.extend(
            [
                "",
                "| Method | Interval N | 80% coverage | Mean width | Interval score |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for model, result_ in result["distribution_metrics"].items():
            values = result_["interval_80"]
            if values["n"]:
                lines.append(
                    f"| {model} | {values['n']} | {values['coverage']:.4f} | {values['mean_width']:.4f} | {values['interval_score']:.4f} |"
                )
        lines.extend(
            [
                "",
                "Intervals use a proper interval score at alpha0.2; quantile pinball losses and per-method coverage are in JSON. External point-only ensembles have no invented distribution.",
            ]
        )
    return "\n".join(lines) + "\n"
