"""Run with: python -m ml.ols.run develop (or evaluate). No model selection."""

from __future__ import annotations

import argparse
import csv
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np
import sklearn
from sklearn.linear_model import LinearRegression

from ml.ols.data import (
    DEFINITIONS,
    FEATURES,
    PROTOCOL,
    ROOT,
    Row,
    load_rows,
    matrix,
    split_rows,
)
from ml.sources import digest


def metrics(actual: np.ndarray, predicted: np.ndarray) -> Row:
    """Point errors; R-squared is undefined for fewer than two/constant outcomes."""
    if len(actual) != len(predicted):
        raise ValueError("Prediction/target lengths differ")
    if not len(actual):
        return {"n": 0, "mae": None, "rmse": None, "r2": None}
    residual = actual - predicted
    sst = float(np.sum((actual - actual.mean()) ** 2))
    return {
        "n": len(actual),
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual**2))),
        "r2": 1 - float(np.sum(residual**2)) / sst if len(actual) > 1 and sst else None,
    }


def explain_prediction(model: LinearRegression, row: Row) -> Row:
    x = matrix([row])[0]
    contributions = x * model.coef_
    manual = float(model.intercept_ + contributions.sum())
    predicted = float(model.predict(x.reshape(1, -1))[0])
    if not np.isclose(manual, predicted, atol=1e-12, rtol=1e-12):
        raise ValueError("Contribution sum does not reproduce model prediction")
    return {
        "intercept": float(model.intercept_),
        "prediction": predicted,
        "contributions": [
            {"feature": f, "value": float(v), "coefficient": float(b), "contribution": float(c)}
            for f, v, b, c in zip(FEATURES, x, model.coef_, contributions)
        ],
    }


def write_csv(path: Path, rows: list[Row]) -> None:
    if not rows:
        raise ValueError("Cannot write an empty CSV without a schema")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )


def score_groups(
    rows: list[Row],
    actual: np.ndarray,
    predictions: np.ndarray,
    baseline: np.ndarray,
    seasons: list[str],
) -> tuple[list[Row], list[Row]]:
    overall, weekly = [], []
    for season in [*seasons, "pooled"]:
        mask = np.asarray([season == "pooled" or r["season"] == season for r in rows])
        for model, values in (("OLS", predictions), ("Recent average", baseline)):
            overall.append(
                {"season": season, "model": model, **metrics(actual[mask], values[mask])}
            )
    for season in seasons:
        for gw in range(1, 39):
            mask = np.asarray([r["season"] == season and r["gameweek"] == gw for r in rows])
            for model, values in (("OLS", predictions), ("Recent average", baseline)):
                weekly.append(
                    {
                        "season": season,
                        "gameweek": gw,
                        "model": model,
                        **metrics(actual[mask], values[mask]),
                    }
                )
    return overall, weekly


def plot_results(
    output: Path, rows: list[Row], actual: np.ndarray, predicted: np.ndarray, seasons: list[str]
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    for season in seasons:
        mask = np.asarray([r["season"] == season for r in rows])
        y, p = actual[mask], predicted[mask]
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), layout="constrained")
        axes[0].scatter(y, p, s=9, alpha=0.12, color="#175a8a", edgecolors="none", rasterized=True)
        low, high = min(y.min(), p.min(), 0) - 1, max(y.max(), p.max()) + 1
        axes[0].plot(
            [low, high], [low, high], color="#9d3b21", linestyle="--", label="Perfect prediction"
        )
        axes[0].set(
            xlim=(low, high),
            ylim=(low, high),
            xlabel="Actual FPL points",
            ylabel="OLS predicted points",
            title=f"{season}: actual versus predicted",
        )
        axes[0].legend(frameon=False)
        axes[1].hist(y - p, bins=45, color="#175a8a", edgecolor="white")
        axes[1].axvline(0, color="#9d3b21", linestyle="--")
        axes[1].set(
            xlabel="Residual = actual minus predicted points",
            ylabel="Player-Gameweek observations",
            title=f"Residual distribution (n = {len(y):,})",
        )
        fig.suptitle(
            "Fixed 2023/24 training; retrospective evaluation; zero-minute rows included",
            fontsize=11,
        )
        fig.savefig(
            output / f"diagnostics-{season}.png",
            dpi=150,
            metadata={"Software": "Educational OLS experiment"},
        )
        plt.close(fig)


def fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def make_report(
    output: Path,
    phase: str,
    model: LinearRegression,
    train: list[Row],
    rows: list[Row],
    coverage: list[Row],
    overall: list[Row],
    weekly: list[Row],
    examples: list[Row],
    diagnostics: Row,
    excluded_train: int,
    manifest: Row,
) -> None:
    title = "OLS development check" if phase == "develop" else "Educational OLS historical backtest"
    lines = [
        f"# {title}",
        "",
        "This is a standalone learning experiment, with no website integration.",
        "",
        f"Protocol: `{PROTOCOL['version']}`; SHA-256 `{manifest['protocol_sha256']}`.",
        "",
        "## Chronology and evaluation status",
        "",
        (
            "Development uses only 2023/24: GW2-28 for training and GW29-38 for validation."
            if phase == "develop"
            else "Fit once on 2023/24, then predict every eligible 2024/25 and 2025/26 player-Gameweek. "
            "Coefficients are frozen throughout both evaluation seasons; only pre-deadline player history updates."
        ),
        (
            f"Training rows: **{len(train):,}**. Evaluation rows: **{len(rows):,}**. "
            f"Training labels excluded because they were unavailable before evaluation: {excluded_train}."
        ),
        "",
        (
            "The feature list, three-GW window and examples were fixed before the first OLS fit. "
            "No regularisation, hyperparameter search, feature selection, outcome-based example selection, "
            "prediction clipping, weighting or evaluation-season refitting is used. Development is a separate command/output folder."
        ),
        "",
        (
            "**Neither 2024/25 nor 2025/26 is an untouched final holdout:** both were evaluated in "
            "earlier repository experiments. 2024/25 is the requested held-out-from-fitting validation; "
            "2025/26 is an additional retrospective comparison. These results are not used to revise this model. "
            "A future untouched evaluation must be predeclared separately before its outcomes are inspected."
        ),
        "",
        "## Accuracy",
        "",
        (
            "Lower MAE/RMSE is better; higher R² is better. MAE is the average absolute points error. "
            "RMSE squares errors before averaging, so big misses matter more. R² compares squared error "
            "against predicting the evaluation sample's mean: 1 is perfect, 0 matches that reference, "
            "and a negative value is worse. It is not an accuracy percentage. Pooled scores weight every "
            "player-GW equally, rather than averaging Gameweek metrics."
        ),
        "",
        "| Season | Model | Observations | MAE | RMSE | R² |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r in overall:
        lines.append(
            f"| {r['season']} | {r['model']} | {r['n']:,} | {fmt(r['mae'])} | {fmt(r['rmse'])} | {fmt(r['r2'])} |"
        )
    lines += [
        "",
        (
            "The baseline predicts exactly the same `recent_total_points` regressor, "
            "on exactly the same rows. Unrounded predictions and negative predictions are retained."
        ),
        "",
        (
            f"Training-only (in-sample) OLS: MAE {fmt(diagnostics['training_metrics']['mae'])}, "
            f"RMSE {fmt(diagnostics['training_metrics']['rmse'])}, "
            f"R² {fmt(diagnostics['training_metrics']['r2'])}. "
            "These describe fit, not held-out accuracy."
        ),
        "",
        "### Observed participation diagnostic",
        "",
        (
            "These groups are identified **after** outcomes only to explain aggregate scores; "
            "they never select training examples or enter predictions. Zero-minute outcomes are "
            "almost constant, so tiny outcome variance makes their R² extremely negative; "
            "MAE/RMSE are more useful summaries for that group."
        ),
        "",
        "| Season | Observed minutes | Model | n | MAE | RMSE | R² |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r in diagnostics["participation"]:
        lines.append(
            f"| {r['season']} | {r['group']} | {r['model']} | {r['n']:,} | {fmt(r['mae'])} | {fmt(r['rmse'])} | {fmt(r['r2'])} |"
        )
    lines += [
        "",
        "## Regressors and fitted coefficients",
        "",
        (
            "The first seven regressors are arithmetic means over the latest **up to three observed "
            "eligible Gameweeks** in the same season. Sum all eligible DGW legs within a GW before "
            "averaging. Include recorded zero-minute fixtures. Do not fill missing/blank GWs or "
            "short windows with zeros. Each fixture must be in an earlier numbered GW and its "
            "kickoff + 24 hours must be strictly before the prediction deadline."
        ),
        "",
        f"Intercept: **{model.intercept_:.10f}** points (goalkeeper reference).",
        "",
        "| Regressor | Definition | Reason | Coefficient (points per unit) |",
        "| --- | --- | --- | ---: |",
    ]
    for f, (definition, reason), coefficient in zip(FEATURES, DEFINITIONS, model.coef_):
        lines.append(f"| `{f}` | {definition} | {reason} | {coefficient:+.10f} |")
    lines += [
        "",
        (
            "A coefficient is the change in predicted points for one extra unit, **holding the "
            "other regressors fixed**. One minute is not one goal: compare units before comparing "
            "coefficient sizes. Position offsets are relative to GK; three indicators avoid a "
            "redundant fourth indicator alongside the intercept. The intercept is the fitted "
            "prediction when every regressor is zero, not a typical player's forecast."
        ),
        "",
        "### Estimated equation",
        "",
        "```text",
        f"predicted_points = {model.intercept_:.10f}",
    ]
    lines += [f"  {b:+.10f} * {f}" for f, b in zip(FEATURES, model.coef_)]
    lines += [
        "```",
        "",
        "[model.json](model.json) contains full-precision coefficients in the exact input order.",
        "",
        "## Three preselected real historical examples",
        "",
        (
            "The players and 2024/25 GW20 were chosen in the protocol before fitting, independently "
            "of their prediction errors. Contributions use full precision internally; displayed terms are rounded."
        ),
    ]
    if not examples:
        lines += [
            "",
            "Examples belong to the separate historical evaluation and are not opened during development.",
        ]
    for example in examples:
        lines += [
            "",
            f"### {example['name']}, {example['season']} GW{example['gameweek']}",
            "",
            (
                f"Deadline: {example['deadline_utc']}. Latest eligible result-ready time: "
                f"{example['history_latest_ready_utc']}. Observed prior-GW window: {example['history_gws']}."
            ),
            "",
            (
                f"Predicted **{example['prediction']:.4f}**, actual **{example['actual']:.0f}**, "
                f"baseline **{example['baseline']:.4f}** points."
            ),
            "",
            "| Term | Input value | Coefficient | Contribution (points) |",
            "| --- | ---: | ---: | ---: |",
            f"| Intercept | 1 | {example['intercept']:+.6f} | {example['intercept']:+.6f} |",
        ]
        for c in example["contributions"]:
            lines.append(
                f"| `{c['feature']}` | {c['value']:.6f} | {c['coefficient']:+.6f} | {c['contribution']:+.6f} |"
            )
        lines += [f"| **Sum** | | | **{example['prediction']:.6f}** |"]
    lines += [
        "",
        "## Prediction plots",
        "",
        (
            "Every evaluated observation is plotted; overlapping dots are transparent. "
            "Residual = actual minus predicted, so positive residuals are underpredictions. "
            "The dashed diagonal is perfect prediction; the dashed vertical line is zero residual."
        ),
    ]
    for season in sorted({r["season"] for r in rows}):
        lines += [
            "",
            f"![Actual versus predicted and residuals for {season}](diagnostics-{season}.png)",
        ]
    lines += [
        "",
        "## Multicollinearity, overfitting and limitations",
        "",
        (
            f"Training design rank including intercept: {diagnostics['rank']}/{len(FEATURES) + 1}. "
            f"Condition number of centred, unit-standard-deviation regressors: {diagnostics['condition_number']:.2f}. "
            "This scaling is diagnostic only; the model fits original units."
        ),
        "",
        "Largest absolute training correlations:",
        "",
    ]
    lines += [
        f"- `{r['left']}` and `{r['right']}`: {r['correlation']:+.4f}."
        for r in diagnostics["correlations"]
    ]
    lines += [
        "",
        (
            "**Multicollinearity:** minutes/starts and goals/xG/points overlap. They are not "
            "identical regressors, but correlated inputs can share credit unstably and produce "
            "counterintuitive signs. The model is descriptive, not causal. Avoid adding xG+xA "
            "alongside both components, duplicated sums/means with fixed denominators, or all four "
            "position dummies. There are no p-values or causal claims here."
        ),
        "",
        (
            "**Overfitting:** OLS chooses the coefficients that minimise training squared errors; "
            "this does not guarantee future accuracy. Chronological evaluation tests season-to-season "
            "generalisation, while the separate within-2023/24 development split avoids developing on "
            "these evaluation results. Repeatedly revising features after reading this report would "
            "turn these results into development data. Do not do that and still claim a final test."
        ),
        "",
        (
            "**OLS limits:** a single additive line cannot describe all position-specific scoring, "
            "minutes uncertainty, interactions, rare hauls, injuries or schedule effects. Predictions "
            "may be negative or fractional. Most archive rows have zero minutes; aggregate errors "
            "can hide weaker performance on actual appearances. Players recur across GWs and "
            "share matches, so errors are dependent and not guaranteed normal or constant-variance. "
            "OLS fitting itself does not require normal errors; textbook inference would need extra assumptions. "
            "2025/26 scoring changes further limit cross-season comparability."
        ),
        "",
        (
            "**Information limits:** verified deadlines do not prove original stat publication or "
            "revision times. The final historical stats use a 24-hour result-readiness proxy; revised "
            "xG/xA and archived position labels may differ from their original vintages. This is a "
            "deadline-gated retrospective backtest, not a certified original-vintage replay. "
            "Names, actual minutes and final schedule are evaluation metadata/target checks only. "
            "Position features use the last eligible historical record, never the target row or "
            "end-of-season player snapshot."
        ),
        "",
        (
            "Opponent strength and home/away were deliberately omitted because verified historical "
            "pre-deadline fixture schedules are unavailable. Final opponents, dates, difficulty, "
            "team strength, availability, price, source xP and target-GW stats never enter X. "
            "The task's feature examples are suggestions; these 11 inputs prioritise verifiable chronology."
        ),
        "",
        (
            "**Coverage:** rows require at least one usable same-season prior result; GW1 and other "
            "cold starts are unpredicted, with no invented cross-season identity. Missing roster/blank-GW "
            "rows are not reconstructed. Therefore the metrics describe the covered archive population, "
            "not every theoretically selectable player-GW. Existing retrospective quality quarantine "
            "(including 2024/25 Evan Ferguson's inconsistent total) affects evaluation eligibility only, "
            "never X. Targets are individual unmultiplied FPL event points, with DGWs aggregated; "
            "no manager captain multipliers or transfer hits."
        ),
        "",
        "| Season | Source player-GWs | Included | Excluded, by first applicable reason |",
        "| --- | ---: | ---: | --- |",
    ]
    for season in sorted({r["season"] for r in coverage}):
        subset = [r for r in coverage if r["season"] == season]
        counts = {
            reason: sum(r["reason"] == reason for r in subset)
            for reason in sorted({r["reason"] for r in subset})
        }
        excluded = "; ".join(f"{k}: {v:,}" for k, v in counts.items() if k != "included")
        lines.append(f"| {season} | {len(subset):,} | {counts.get('included', 0):,} | {excluded} |")
    lines += [
        "",
        "## Accuracy by Gameweek",
        "",
        (
            "GW1 has no same-season history and therefore n=0 / undefined metrics. "
            "[gameweek_metrics.csv](gameweek_metrics.csv) includes both models and all 38 GWs per season."
        ),
        "",
        "| Season | GW | n | OLS MAE | Baseline MAE | OLS RMSE | Baseline RMSE | OLS R² | Baseline R² |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for a, b in zip(weekly[::2], weekly[1::2]):
        lines.append(
            f"| {a['season']} | {a['gameweek']} | {a['n']} | {fmt(a['mae'])} | {fmt(b['mae'])} | "
            f"{fmt(a['rmse'])} | {fmt(b['rmse'])} | {fmt(a['r2'])} | {fmt(b['r2'])} |"
        )
    lines += [
        "",
        "## Reproduce and learn",
        "",
        (
            "See [the experiment README](../README.md) for commands, file descriptions and a plain-English "
            "walkthrough. The [manifest](manifest.json) records source hashes, the frozen protocol, "
            "implementation hashes, exact splits and Python/library versions. No data was fetched in this run."
        ),
        "",
        (
            "OLS fits an intercept and one weight per regressor jointly, choosing values that minimise "
            "the sum of squared (actual minus predicted) training errors. With an intercept column in X, "
            "the familiar full-rank equation is beta = (XᵀX)⁻¹Xᵀy. This implementation calls "
            "`LinearRegression().fit(X_train, y_train)`; the library solves least squares numerically "
            "rather than explicitly inverting XᵀX. Prediction is intercept plus the sum of coefficient "
            "times feature value, exactly as shown in the contribution tables. There is no penalty term."
        ),
        "",
        (
            "References: [scikit-learn 1.7 LinearRegression](https://scikit-learn.org/1.7/modules/generated/sklearn.linear_model.LinearRegression.html), "
            "[OLS guide](https://scikit-learn.org/1.7/modules/linear_model.html#ordinary-least-squares), "
            "[pinned FPL archive](https://github.com/vaastav/Fantasy-Premier-League/tree/9779cdbc0c07f6c900c2d0c181ddf6bb9c800f88)."
        ),
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(phase: str, data_dir: Path) -> list[Row]:
    seasons = [PROTOCOL["training_season"]]
    if phase == "evaluate":
        seasons += PROTOCOL["evaluation_seasons"]
    rows, coverage, provenance = load_rows(data_dir, seasons)
    train, evaluation, excluded_train = split_rows(rows, phase)
    x_train, x_eval = matrix(train), matrix(evaluation)
    y_train = np.asarray([r["actual"] for r in train])
    y_eval = np.asarray([r["actual"] for r in evaluation])
    # The entire estimator. No preprocessing, tuning, refit, clipping or randomness.
    model = LinearRegression().fit(x_train, y_train)
    predicted = model.predict(x_eval)
    baseline = x_eval[:, FEATURES.index("recent_total_points")]
    eval_seasons = sorted({r["season"] for r in evaluation})
    overall, weekly = score_groups(evaluation, y_eval, predicted, baseline, eval_seasons)
    output = ROOT / ("reports" if phase == "evaluate" else "development")
    output.mkdir(parents=True, exist_ok=True)
    local_output = ROOT / "outputs" / phase
    local_output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "phase": phase,
        "protocol": PROTOCOL,
        "protocol_sha256": digest((ROOT / "protocol.json").read_bytes()),
        "code_sha256": {f: digest((ROOT / f).read_bytes()) for f in ("data.py", "run.py")},
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "sklearn": sklearn.__version__,
        },
        "train_n": len(train),
        "evaluation_n": len(evaluation),
        "train_last_label_ready": max(r["target_ready_utc"] for r in train).isoformat(),
        "evaluation_first_deadline": min(r["deadline_utc"] for r in evaluation).isoformat(),
        "evaluation_status": PROTOCOL["evaluation_status"],
        "provenance": provenance,
    }
    write_json(output / "manifest.json", manifest)
    write_json(
        output / "model.json",
        {
            "feature_names": FEATURES,
            "intercept": float(model.intercept_),
            "coefficients": model.coef_.tolist(),
            "protocol_sha256": manifest["protocol_sha256"],
        },
    )
    write_csv(
        output / "coefficients.csv",
        [
            {"feature": f, "coefficient": float(b), "definition": d, "reason": why}
            for f, b, (d, why) in zip(FEATURES, model.coef_, DEFINITIONS)
        ],
    )
    write_csv(output / "overall_metrics.csv", overall)
    write_csv(output / "gameweek_metrics.csv", weekly)
    prediction_rows = [
        {
            "season": r["season"],
            "gameweek": r["gameweek"],
            "player_key": r["player_key"],
            "name": r["name"],
            "deadline_utc": r["deadline_utc"].isoformat(),
            "history_latest_ready_utc": r["history_latest_ready_utc"].isoformat(),
            "history_gws": r["history_gws"],
            "actual": float(y),
            "prediction": float(p),
            "baseline": float(b),
            "residual": float(y - p),
            **r["values"],
        }
        for r, y, p, b in zip(evaluation, y_eval, predicted, baseline)
    ]
    write_csv(local_output / "predictions.csv", prediction_rows)
    # Fixed historical examples are opened only in evaluate, never during development.
    examples = []
    if phase == "evaluate":
        for spec in PROTOCOL["examples"]:
            selected = [r for r in evaluation if all(r[k] == v for k, v in spec.items())]
            if len(selected) != 1:
                raise ValueError(f"Expected one predefined example: {spec}")
            row = selected[0]
            examples.append(
                {
                    **spec,
                    "actual": row["actual"],
                    "baseline": row["values"]["recent_total_points"],
                    "deadline_utc": row["deadline_utc"].isoformat(),
                    "history_latest_ready_utc": row["history_latest_ready_utc"].isoformat(),
                    "history_gws": row["history_gws"],
                    **explain_prediction(model, row),
                }
            )
        write_json(output / "examples.json", examples)
    z = (x_train - x_train.mean(axis=0)) / x_train.std(axis=0)
    correlation = np.corrcoef(x_train, rowvar=False)
    pairs = sorted(
        [
            {"left": FEATURES[i], "right": FEATURES[j], "correlation": float(correlation[i, j])}
            for i in range(len(FEATURES))
            for j in range(i + 1, len(FEATURES))
        ],
        key=lambda r: abs(r["correlation"]),
        reverse=True,
    )[:3]
    diagnostics = {
        "rank": int(np.linalg.matrix_rank(np.column_stack((np.ones(len(train)), x_train)))),
        "condition_number": float(np.linalg.cond(z)),
        "correlations": pairs,
        "training_metrics": metrics(y_train, model.predict(x_train)),
        "participation": [],
    }
    for season in eval_seasons:
        for group, is_zero in (("zero", True), ("positive", False)):
            mask = np.asarray(
                [
                    r["season"] == season and (r["observed_minutes"] == 0) == is_zero
                    for r in evaluation
                ]
            )
            for name, values in (("OLS", predicted), ("Recent average", baseline)):
                diagnostics["participation"].append(
                    {
                        "season": season,
                        "group": group,
                        "model": name,
                        **metrics(y_eval[mask], values[mask]),
                    }
                )
    write_json(output / "diagnostics.json", diagnostics)
    write_csv(local_output / "coverage.csv", coverage)
    plot_results(output, evaluation, y_eval, predicted, eval_seasons)
    make_report(
        output,
        phase,
        model,
        train,
        evaluation,
        coverage,
        overall,
        weekly,
        examples,
        diagnostics,
        excluded_train,
        manifest,
    )
    print(f"Wrote {output / 'REPORT.md'}")
    for r in overall:
        print(
            f"{r['season']:8} {r['model']:14} n={r['n']:6} MAE={fmt(r['mae'])} RMSE={fmt(r['rmse'])} R2={fmt(r['r2'])}"
        )
    return overall


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("develop", "evaluate"))
    parser.add_argument("--data-dir", type=Path, default=ROOT.parent / "data/processed")
    args = parser.parse_args()
    run(args.phase, args.data_dir)


if __name__ == "__main__":
    main()
