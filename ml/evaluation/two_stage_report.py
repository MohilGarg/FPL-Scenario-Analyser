"""Render the saved validation experiment; this module never fits or selects models."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from ml.sources import digest
from ml.training.experiment import write_json

COLORS = {"ridge": "#4063A8", "two_stage": "#26836E", "logistic": "#26836E", "lagged_last5_gws": "#B26D21"}
LABELS = {"ridge": "Ridge", "two_stage": "Two-stage", "logistic": "Logistic", "lagged_last5_gws": "Prior 5-GW rate"}


def render(output: Path) -> None:
    result = json.loads((output / "results.json").read_text(encoding="utf-8"))
    evaluation = result["evaluation"]
    text = ["# Fixed two-stage validation experiment", "", "2023/24 fitting; 2024/25 validation only. 2025/26 is consumed and excluded.", "",
            "Bias is prediction minus actual. Appearance/nonappearance and position groups are retrospective diagnostics.", "",
            "| Approach | Group | N | MAE | RMSE | Bias |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    with (output / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["approach", "group", "n", "mae", "rmse", "bias"])
        for name, groups in evaluation["points"].items():
            for group, m in groups.items():
                if not m["n"]:
                    continue
                writer.writerow([name, group, *[m[k] for k in ("n", "mae", "rmse", "bias")]])
                text.append(f"| {name} | {group} | {m['n']:,} | {m['mae']:.4f} | {m['rmse']:.4f} | {m['bias']:+.4f} |")
    text += ["", "| Appearance approach | Brier | Log loss | ECE (10 fixed bins) | Mean probability | Actual rate |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, m in evaluation["appearance"].items():
        text.append(f"| {name} | {m['brier']:.5f} | {m['log_loss']:.5f} | {m['equal_width_ece']:.5f} | {m['prediction_mean']:.5f} | {m['actual_appearance_rate']:.5f} |")
    for name, m in evaluation["appearance"].items():
        text += ["", f"### {name} appearance calibration", "", "| Probability band | N | Predicted | Observed |",
                 "| --- | ---: | ---: | ---: |"]
        for b in m["calibration"]:
            if b["n"]:
                text.append(f"| [{b['lower']:.1f}, {b['upper']:.1f}{']' if b['upper'] == 1 else ')'} | {b['n']:,} | {b['prediction_mean']:.4f} | {b['appearance_rate']:.4f} |")
        text.append("")
    text += ["", "## Expected-point calibration", "", "| Approach | Prediction band | N | Predicted | Actual |",
             "| --- | --- | ---: | ---: | ---: |"]
    for name, m in evaluation["points_calibration"].items():
        for b in m["bands"]:
            if b["n"]:
                text.append(f"| {name} | {b['band']} | {b['n']:,} | {b['prediction_mean']:.4f} | {b['actual_mean']:.4f} |")
    text += ["", "## Paired GW-block differences (two-stage minus Ridge)", "", "| Metric | Difference | 95% interval |", "| --- | ---: | --- |"]
    for name, m in evaluation["uncertainty"]["differences_contender_minus_ridge"].items():
        text.append(f"| {name} | {m['estimate']:+.5f} | [{m['ci95'][0]:+.5f}, {m['ci95'][1]:+.5f}] |")
    (output / "comparison.md").write_text("\n".join(text) + "\n", encoding="utf-8")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), constrained_layout=True)
    for name in ("logistic", "lagged_last5_gws"):
        bins = [b for b in evaluation["appearance"][name]["calibration"] if b["n"]]
        axes[0].plot([b["prediction_mean"] for b in bins], [b["appearance_rate"] for b in bins],
                     "o-", color=COLORS[name], label=LABELS[name])
    axes[0].plot([0, 1], [0, 1], "--", color="#666666", label="Perfect calibration")
    axes[0].set(xlim=(-.02, 1.02), ylim=(-.02, 1.02), xlabel="Mean predicted appearance probability",
                ylabel="Observed appearance rate", title="Appearance calibration (10 fixed bins)")
    axes[0].legend(fontsize=8, loc="upper left")
    maximum = 1.
    for name in ("ridge", "two_stage"):
        bins = [b for b in evaluation["points_calibration"][name]["bands"] if b["n"]]
        maximum = max(maximum, *[max(b["prediction_mean"], b["actual_mean"]) for b in bins])
        axes[1].plot([b["prediction_mean"] for b in bins], [b["actual_mean"] for b in bins],
                     "o-", color=COLORS[name], label=LABELS[name])
    axes[1].plot([-.2, maximum + .2], [-.2, maximum + .2], "--", color="#666666", label="Perfect calibration")
    axes[1].set(xlabel="Mean predicted points in fixed band", ylabel="Observed mean points",
                title="Expected-point calibration")
    axes[1].legend(fontsize=8, loc="upper left")
    for ax in axes:
        ax.grid(alpha=.18)
    fig.suptitle("2024/25 validation only; models fitted on 2023/24", fontsize=12)
    fig.savefig(output / "calibration.png", dpi=160)
    plt.close(fig)

    groups = ["all", "GK", "DEF", "MID", "FWD", "pre_deadline_regular_starters"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    for ax, metric in zip(axes, ("mae", "rmse")):
        indices = np.arange(len(groups))
        for j, name in enumerate(("ridge", "two_stage")):
            values = [evaluation["points"][name][group].get(metric, np.nan) for group in groups]
            ax.bar(indices + (j - .5) * .36, values, .36, color=COLORS[name], label=LABELS[name])
        ax.set_xticks(indices, ["All", "GK", "DEF", "MID", "FWD", "Regular\nstarters"])
        ax.set(ylabel="FPL points", title=metric.upper())
        ax.set_ylim(0, max(v for c in ax.containers for v in c.datavalues if np.isfinite(v)) * 1.3)
        ax.grid(axis="y", alpha=.18)
        ax.legend(loc="upper left", fontsize=8)
    fig.suptitle("Ridge vs two-stage: 2024/25 validation", fontsize=12)
    fig.savefig(output / "model_comparison.png", dpi=160)
    plt.close(fig)
    write_json(output / "render_metadata.json", {"renderer_sha256": digest(Path(__file__).read_bytes()),
                                               "results_sha256": digest((output / "results.json").read_bytes())})
