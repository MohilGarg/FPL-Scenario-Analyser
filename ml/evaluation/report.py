"""Render saved results only: tables and two static scientific charts, no fitting."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq

LABELS = {"recent_average": "Recent average", "ridge": "Ridge", "lightgbm": "LightGBM"}
COLORS = {"recent_average": "#667085", "ridge": "#2563eb", "lightgbm": "#bd5b13"}


def render_report(directory: Path) -> None:
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    test = json.loads((directory / "test.json").read_text(encoding="utf-8"))
    phases = {"validation": selection, "test": test}
    lines = ["# Generated baseline experiment results", "",
             f"Selected before holdout: **{LABELS[selection['selected_approach']]}**, by validation MAE.", "",
             "Bias = mean(prediction − actual). Validation fits 2023/24 only; frozen test models refit "
             "2023/24 + 2024/25. Test outcomes never select settings/features. No prediction clipping.", "",
             "| Approach | Validation MAE | RMSE | Bias | Test MAE | RMSE | Bias |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    csv_rows = []
    for model in LABELS:
        v, t = selection["metrics"][model]["all"], test["metrics"][model]["all"]
        lines.append(f"| {LABELS[model]} | {v['mae']:.4f} | {v['rmse']:.4f} | {v['bias']:+.4f} | "
                     f"{t['mae']:.4f} | {t['rmse']:.4f} | {t['bias']:+.4f} |")
    for phase, result in phases.items():
        lines += ["", f"## {phase.title()} groups", "",
                  "Position is a retrospective, archived-official-season label check, never a model input. "
                  "Zero/nonzero minutes are observed outcome groups, never predictive masks.", "",
                  "| Group | Approach | N | MAE | RMSE | Bias |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        for group in ("GK", "DEF", "MID", "FWD", "UNKNOWN", "zero_minutes", "nonzero_minutes"):
            for model in LABELS:
                m = result["metrics"][model][group]
                if m["n"]:
                    lines.append(f"| {group} | {LABELS[model]} | {m['n']:,} | {m['mae']:.4f} | "
                                 f"{m['rmse']:.4f} | {m['bias']:+.4f} |")
        for model in LABELS:
            for group, m in result["metrics"][model].items():
                csv_rows.append({"phase": phase, "model": model, "group": group, **{
                    k: m.get(k) for k in ("n", "mae", "rmse", "bias")}})
        lines += ["", f"## {phase.title()} error distributions", "",
                  "| Approach | Signed p05 | Signed p50 | Signed p95 | Absolute p50 | Absolute p90 | Absolute p95 | ≤1 point | ≤2 points |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for model in LABELS:
            m = result["metrics"][model]["all"]
            s, a = m["signed_error_quantiles"], m["absolute_error_quantiles"]
            lines.append(f"| {LABELS[model]} | {s['p05']:.3f} | {s['p50']:.3f} | {s['p95']:.3f} | "
                         f"{a['p50']:.3f} | {a['p90']:.3f} | {a['p95']:.3f} | "
                         f"{m['fraction_abs_error_le_1']:.1%} | {m['fraction_abs_error_le_2']:.1%} |")
        lines += ["", f"## {phase.title()} paired uncertainty", "",
                  "Negative MAE differences favour the named contender. Four observed consecutive GW blocks; "
                  "1,000 draws. This covers some temporal/shared-fixture dependence, not every dependence source.", "",
                  "| Comparison | MAE difference | 95% moving-block interval |",
                  "| --- | ---: | --- |"]
        for comparison, u in result["uncertainty"].items():
            lines.append(f"| {comparison} | {u['mae_difference_contender_minus_reference']:+.4f} | "
                         f"[{u['ci95'][0]:+.4f}, {u['ci95'][1]:+.4f}] |")
        lines += ["", f"## {phase.title()} runtime", "",
                  "| Approach | Model fit seconds | Model inference seconds |",
                  "| --- | ---: | ---: |"]
        for model in LABELS:
            lines.append(f"| {LABELS[model]} | {result['fit_timing']['model_fit_seconds'][model]:.6f} | "
                         f"{result['inference_timing']['model_inference_seconds'][model]:.6f} |")
        lines += [f"\nShared preprocessing fit/transform: {result['fit_timing']['preprocessing_fit_transform_seconds']:.4f}s; "
                  f"prediction transform: {result['inference_timing']['preprocessing_transform_seconds']:.4f}s. "
                  f"Fit rows: {result['fit_timing']['training_rows']:,}; scored rows: {result['inference_timing']['prediction_rows']:,}."]
    lines += ["", "## Training-derived model interpretation", ""]
    with (directory / "final_models/ridge_coefficients.csv").open(encoding="utf-8", newline="") as stream:
        coefficients = list(csv.DictReader(stream))
    with (directory / "final_models/lightgbm_importance.csv").open(encoding="utf-8", newline="") as stream:
        importance = list(csv.DictReader(stream))
    lines += ["Ridge coefficients are points per training-standard-deviation, conditional on correlated "
              "predictors; they are not causal effects. LightGBM gain importance is also training-derived.", "",
              "| Ridge feature | Standardised coefficient |", "| --- | ---: |"]
    for row in sorted(coefficients, key=lambda r: abs(float(r["coefficient_per_training_std"])), reverse=True)[:10]:
        lines.append(f"| `{row['feature']}` | {float(row['coefficient_per_training_std']):+.4f} |")
    lines += ["", "| LightGBM feature | Gain importance |", "| --- | ---: |"]
    for row in sorted(importance, key=lambda r: float(r["gain"]), reverse=True)[:10]:
        lines.append(f"| `{row['feature']}` | {float(row['gain_percent']):.2f}% |")
    lines += ["", "See the reviewed `ml/reports/BASELINE_EXPERIMENTS.md` for exclusions, limitations, "
              "frozen configurations and the next proposed experiment. Full metrics/coefficients/importances, "
              "models, preprocessing and predictions remain local under this ignored directory.", ""]
    (directory / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    with (directory / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["phase", "model", "group", "n", "mae", "rmse", "bias"])
        writer.writeheader()
        writer.writerows(csv_rows)
    plot_metrics(directory, phases)
    plot_errors(directory)
    print(f"Reports/charts: {directory}")


def plot_metrics(directory: Path, phases: dict) -> None:
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    figure, axes = plt.subplots(1, 2, figsize=(10.8, 4.4), constrained_layout=True)
    for axis, metric in zip(axes, ("mae", "rmse")):
        for index, model in enumerate(LABELS):
            values = [r["metrics"][model]["all"][metric] for r in phases.values()]
            bars = axis.bar(np.arange(2) + (index - 1) * .24, values, width=.23,
                            label=LABELS[model], color=COLORS[model])
            axis.bar_label(bars, fmt="%.3f", padding=3, fontsize=9)
        axis.set_xticks([0, 1], ["Validation\n2024/25", "Holdout test\n2025/26"])
        axis.set_ylabel("FPL points")
        axis.set_title(metric.upper() + " — lower is better")
        axis.set_ylim(0, axis.get_ylim()[1] * 1.35)
        axis.grid(axis="y", alpha=.2)
        axis.set_axisbelow(True)
    axes[0].legend(loc="upper left", fontsize=9)
    figure.suptitle("Fixed baseline approaches · historical player-Gameweek points")
    figure.savefig(directory / "comparison.png", dpi=160)
    plt.close(figure)


def plot_errors(directory: Path) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(10.8, 4.4), constrained_layout=True)
    grid = np.linspace(0, 12, 241)
    for axis, phase in zip(axes, ("validation", "test")):
        table = pq.read_table(directory / f"{phase}_predictions.parquet")
        actual = table["actual_points"].to_numpy()
        for model in LABELS:
            error = np.sort(np.abs(table[model].to_numpy() - actual))
            axis.plot(grid, np.searchsorted(error, grid, side="right") / len(error),
                      color=COLORS[model], label=LABELS[model], linewidth=2)
        axis.set(xlim=(0, 12), ylim=(0, 1), xlabel="Absolute prediction error (FPL points)",
                 ylabel="Fraction of observations at or below error", title=phase.title() + " · higher is better")
        axis.grid(alpha=.2)
        axis.legend(loc="lower right", fontsize=9)
    figure.suptitle("Error distributions · zero-minute observations retained")
    figure.savefig(directory / "error_distributions.png", dpi=160)
    plt.close(figure)
