"""Describe saved predictions only: python -m ml.ols.diagnostics. Never fit a model."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from ml.ols.data import PROTOCOL, ROOT, Row
from ml.ols.run import fmt, metrics, write_csv, write_json
from ml.sources import digest

# Fixed descriptive choices, not searched for good evaluation scores.
HIGH_MINUTES = 60
BAND_EDGES = (0, 1, 2, 3, 4, 5, 6, 8)
BAND_LABELS = ("<0", "0-<1", "1-<2", "2-<3", "3-<4", "4-<5", "5-<6", "6-<8", "8+")


def load_predictions(path: Path) -> list[Row]:
    """The existing export already contains the pre-deadline minutes regressor."""
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    keys = {(r["season"], r["player_key"], r["gameweek"]) for r in rows}
    if not rows or len(keys) != len(rows):
        raise ValueError("Need nonempty, unique saved player-Gameweek predictions")
    for row in rows:
        for name in ("actual", "prediction", "baseline", "recent_minutes"):
            row[name] = float(row[name])
            if not np.isfinite(row[name]):
                raise ValueError("Diagnostic values must be finite")
    if {r["season"] for r in rows} != set(PROTOCOL["evaluation_seasons"]):
        raise ValueError("Saved prediction seasons differ from the existing protocol")
    return rows


def summarise(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    """Compare identical samples for OLS/baseline; retain empty prediction bands."""
    accuracy, calibration = [], []
    for season in sorted({r["season"] for r in rows}):
        group = [r for r in rows if r["season"] == season]
        for cohort in ("all", "high historical minutes"):
            subset = [r for r in group if cohort == "all" or r["recent_minutes"] >= HIGH_MINUTES]
            actual = np.asarray([r["actual"] for r in subset])
            for model, key in (("OLS", "prediction"), ("Recent average", "baseline")):
                accuracy.append(
                    {
                        "season": season,
                        "cohort": cohort,
                        "model": model,
                        **metrics(actual, np.asarray([r[key] for r in subset])),
                    }
                )
        band_ids = np.searchsorted(BAND_EDGES, [r["prediction"] for r in group], side="right")
        for band, label in enumerate(BAND_LABELS):
            subset = [r for r, identity in zip(group, band_ids) if identity == band]
            mean_pred = float(np.mean([r["prediction"] for r in subset])) if subset else None
            mean_actual = float(np.mean([r["actual"] for r in subset])) if subset else None
            calibration.append(
                {
                    "season": season,
                    "predicted_band": label,
                    "n": len(subset),
                    "mean_predicted": mean_pred,
                    "mean_actual": mean_actual,
                    "actual_minus_predicted": mean_actual - mean_pred if subset else None,
                }
            )
    return accuracy, calibration


def plot(output: Path, accuracy: list[Row], calibration: list[Row]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    ols, baseline = accuracy[::2], accuracy[1::2]
    x = np.arange(len(ols))
    labels = [
        f"{r['season']}\n{'All' if r['cohort'] == 'all' else 'Historical minutes >=60'}\nn={r['n']:,}"
        for r in ols
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), layout="constrained")
    for ax, key, title in zip(
        axes,
        ("mae", "rmse", "r2"),
        ("MAE (lower is better)", "RMSE (lower is better)", "R-squared (higher is better)"),
    ):
        for values, offset, color, name in (
            (ols, -0.18, "#175a8a", "OLS"),
            (baseline, 0.18, "#b34b2d", "Recent average"),
        ):
            bars = ax.bar(
                x + offset,
                [r[key] if r[key] is not None else np.nan for r in values],
                width=0.36,
                color=color,
                label=name,
            )
            ax.bar_label(bars, fmt="%.2f", padding=3, fontsize=8)
        ax.axhline(0, color="#777777", linewidth=0.7)
        ax.set(title=title, xticks=x, xticklabels=labels)
        ax.tick_params(axis="x", labelsize=8)
        ax.margins(y=0.18)
    axes[0].legend(frameon=False)
    fig.suptitle(
        "Diagnostic subsamples of saved predictions: no refit; future non-appearances retained"
    )
    fig.savefig(output / "high-minutes.png", dpi=150)
    plt.close(fig)

    seasons = sorted({r["season"] for r in calibration})
    fig, axes = plt.subplots(1, len(seasons), figsize=(13, 5), layout="constrained", squeeze=False)
    for ax, season in zip(axes[0], seasons):
        group = [r for r in calibration if r["season"] == season]
        x = np.arange(len(group))
        actual = [r["mean_actual"] if r["n"] else np.nan for r in group]
        predicted = [r["mean_predicted"] if r["n"] else np.nan for r in group]
        ax.bar(x, actual, color="#175a8a", alpha=0.85, label="Mean actual points")
        ax.plot(x, predicted, "o--", color="#b34b2d", label="Mean predicted points")
        ax.axhline(0, color="#777777", linewidth=0.7)
        ax.set(
            title=season,
            ylabel="Average FPL points",
            xlabel="Fixed predicted-points band",
            xticks=x,
            xticklabels=[f"{r['predicted_band']}\nn={r['n']:,}" for r in group],
        )
        ax.tick_params(axis="x", labelsize=8)
        ax.legend(frameon=False)
    fig.suptitle(
        "Grouped calibration of existing predictions: descriptive averages, no recalibration"
    )
    fig.savefig(output / "calibration-bands.png", dpi=150)
    plt.close(fig)


def report(output: Path, accuracy: list[Row], calibration: list[Row]) -> None:
    n = sum(r["n"] for r in accuracy if r["cohort"] == "all" and r["model"] == "OLS")
    lines = [
        "# Additional OLS evaluation diagnostics",
        "",
        (
            f"These analyses describe the existing {n:,} saved predictions. They do not fit, tune or recalibrate "
            "any model. The original model, protocol, coefficients and historical results are unchanged."
        ),
        "",
        "## Accuracy conditional on high historical minutes",
        "",
        (
            "High minutes means `recent_minutes >= 60`: the existing pre-deadline mean minutes across "
            "up to three observed prior Gameweeks. The threshold is a fixed descriptive choice, not selected "
            "by testing alternatives. It includes short windows and summed DGW legs; it is not a per-match "
            "minutes measure or a guarantee of an upcoming start. All future non-appearances remain in this cohort."
        ),
        "",
        "| Season | Sample | Model | n | MAE | RMSE | R² |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r in accuracy:
        lines.append(
            f"| {r['season']} | {r['cohort']} | {r['model']} | {r['n']:,} | {fmt(r['mae'])} | {fmt(r['rmse'])} | {fmt(r['r2'])} |"
        )
    lines += [
        "",
        "![Accuracy by historical minutes](high-minutes.png)",
        "",
        (
            "These are conditional forecast-error statistics: MAE is average absolute error and RMSE "
            "gives large errors more weight. R² compares squared errors with this subsample's own mean "
            "outcome; restricting the sample changes both the residual sum of squares and outcome variance. "
            "Therefore R² across samples is not a clean comparison of model quality. Compare OLS and the "
            "baseline within the same sample. Higher errors here do not establish a causal effect of minutes."
        ),
        "",
        "## Average outcomes by predicted-points band",
        "",
        (
            "Bands are fixed before this diagnostic calculation: <0, [0,1), [1,2), [2,3), [3,4), [4,5), "
            "[5,6), [6,8), [8,infinity). Boundaries are lower-inclusive and upper-exclusive. Negative "
            "predictions are retained; empty bands have n=0 and undefined means, not zero points."
        ),
        "",
        "| Season | Predicted band | n | Mean predicted | Mean actual | Actual minus predicted |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r in calibration:
        lines.append(
            f"| {r['season']} | {r['predicted_band']} | {r['n']:,} | {fmt(r['mean_predicted'])} | {fmt(r['mean_actual'])} | {fmt(r['actual_minus_predicted'])} |"
        )
    lines += [
        "",
        "![Grouped calibration](calibration-bands.png)",
        "",
        (
            "This is a grouped calibration check: in a well-calibrated forecast, mean actual points "
            "should be close to mean predicted points within each band. A positive gap means "
            "underprediction on average; a negative gap means overprediction. A rising actual-points "
            "profile shows discrimination (sorting ability), which can coexist with imperfect calibration. "
            "Compare with each band's mean prediction, not its midpoint. These are sample averages, "
            "not fitted adjustments, causal effects or confidence intervals. Sparse tail bands are "
            "especially uncertain; repeated players and shared matches make observations dependent."
        ),
        "",
        (
            "Both seasons remain retrospective comparisons, with the original coverage and publication-vintage "
            "limitations. These diagnostics do not make either season an untouched holdout. "
            "CSV files retain full precision; the manifest records the input prediction file's hash."
        ),
        "",
    ]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predictions", type=Path, default=ROOT / "outputs/evaluate/predictions.csv"
    )
    args = parser.parse_args()
    rows = load_predictions(args.predictions)
    accuracy, calibration = summarise(rows)
    output = ROOT / "reports/additional-diagnostics"
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "high_minutes_metrics.csv", accuracy)
    write_csv(output / "calibration_bands.csv", calibration)
    write_json(
        output / "manifest.json",
        {
            "purpose": "diagnostics_only_no_refit",
            "diagnostics_code_sha256": digest(Path(__file__).read_bytes()),
            "prediction_sha256": digest(args.predictions.read_bytes()),
            "n": len(rows),
            "high_minutes_threshold": HIGH_MINUTES,
            "band_edges": BAND_EDGES,
            "existing_artifact_sha256": {
                f: digest((ROOT / f).read_bytes())
                for f in ("protocol.json", "reports/model.json", "reports/overall_metrics.csv")
            },
        },
    )
    plot(output, accuracy, calibration)
    report(output, accuracy, calibration)
    print(f"Wrote {output / 'README.md'} from saved predictions only; no model fitted.")


if __name__ == "__main__":
    main()
