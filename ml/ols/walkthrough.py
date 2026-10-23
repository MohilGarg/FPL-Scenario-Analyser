"""Read-only learning walkthrough: python -m ml.ols.walkthrough (no files written)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LinearRegression

from ml.ols.data import FEATURES, PROTOCOL, ROOT, load_rows, matrix, split_rows
from ml.ols.run import explain_prediction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT.parent / "data/processed")
    args = parser.parse_args()

    # 1. Reuse the exact deadline gates, features and chronological split.
    # load_rows calls features_before: sum eligible DGW legs, then average up to
    # three observed earlier GWs; same-GW/future results never become regressors.
    seasons = [PROTOCOL["training_season"], *PROTOCOL["evaluation_seasons"]]
    rows, _, _ = load_rows(args.data_dir, seasons)
    training, evaluation, _ = split_rows(rows, "evaluate")

    # 2. One row per player-GW; one column per regressor in FEATURES order.
    # y is actual points in the GW being predicted, not a regressor in X.
    X = matrix(training)
    y = np.asarray([row["actual"] for row in training])
    print(f"X: {X.shape[0]:,} observations x {X.shape[1]} regressors; y: {y.shape}")
    print("Column order:", ", ".join(FEATURES))
    i = next(i for i, row in enumerate(training) if row["values"]["recent_minutes"] >= 60)
    row = training[i]
    print(f"\nTraining observation: {row['name']}, {row['season']} GW{row['gameweek']}")
    print("Feature dictionary:", row["values"])
    print("The same values as a row of X:", X[i])
    print("Its separate target y:", y[i])

    # 3. Reproduce the EXISTING estimate in memory, on the original training data.
    # OLS jointly chooses intercept/weights to minimise sum((y - Xb - a)**2).
    # For full rank, beta = (Z.T Z)^-1 Z.T y, with Z = [1, X]. sklearn solves
    # least squares numerically; do not explicitly invert Z.T Z.
    model = LinearRegression().fit(X, y)
    saved = json.loads((ROOT / "reports/model.json").read_text(encoding="utf-8"))
    assert saved["feature_names"] == list(FEATURES)
    np.testing.assert_allclose(model.intercept_, saved["intercept"], atol=1e-10, rtol=1e-10)
    np.testing.assert_allclose(model.coef_, saved["coefficients"], atol=1e-10, rtol=1e-10)
    print("\nReproduced the saved coefficients; no model/report files are written.")
    print(f"Intercept: {model.intercept_:+.6f}")

    # 4. Reuse the first historical example already fixed in the protocol.
    spec = PROTOCOL["examples"][0]
    player = next(row for row in evaluation if all(row[k] == v for k, v in spec.items()))
    explanation = explain_prediction(model, player)
    print(f"\n{player['name']}, {player['season']} GW{player['gameweek']}")
    print("Deadline:", player["deadline_utc"].isoformat())
    print("Latest eligible result-ready time:", player["history_latest_ready_utc"].isoformat())
    print("Prediction = intercept + sum(input value * coefficient):")
    for term in explanation["contributions"]:
        print(
            f"  {term['feature']:24} {term['value']:9.4f} * "
            f"{term['coefficient']:+9.4f} = {term['contribution']:+9.4f}"
        )
    print(f"Predicted: {explanation['prediction']:.4f}; actual: {player['actual']:.0f}")
    print("Coefficients are conditional associations, holding other regressors fixed.")
    print("This reproduces one existing OLS fit, without tuning or evaluation-season fitting.")


if __name__ == "__main__":
    main()
