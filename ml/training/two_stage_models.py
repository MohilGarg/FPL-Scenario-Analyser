"""Fixed logistic/Ridge mixture; outcomes are used only by the fitting function."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge
from threadpoolctl import threadpool_limits

from ml.dataset import FEATURE_COLUMNS
from ml.training.models import Preprocessor


def probabilities(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("Appearance probabilities must be finite and within [0, 1]")
    return values


def combine_points(probability: np.ndarray, appearing_points: np.ndarray,
                   nonappearing_points: float) -> np.ndarray:
    """E[Y|X] = P(A|X) E[Y|A,X] + (1-P(A|X)) E[Y|not A,X]."""
    p = probabilities(probability)
    if appearing_points.shape != p.shape or not np.isfinite(appearing_points).all() or not np.isfinite(nonappearing_points):
        raise ValueError("Conditional contributions must be aligned and finite")
    return p * appearing_points + (1 - p) * nonappearing_points


@dataclass
class TwoStageModels:
    preprocessor: Preprocessor
    appearance: LogisticRegression
    conditional: Ridge
    reference: Ridge
    nonappearance_mean: float
    appearance_mean: float
    appearance_prevalence: float


def fit_two_stage(x: np.ndarray, y: np.ndarray, minutes: np.ndarray,
                  config: dict) -> tuple[TwoStageModels, dict]:
    if (y.ndim != 1 or y.shape != minutes.shape or len(x) != len(y)
            or not np.isfinite(y).all() or not np.isfinite(minutes).all() or (minutes < 0).any()):
        raise ValueError("Training outcomes/minutes must be aligned, finite and valid")
    appeared = minutes > 0
    if not appeared.any() or appeared.all():
        raise ValueError("Both appearance classes are required")
    started = perf_counter()
    pre = Preprocessor.fit(x, FEATURE_COLUMNS)
    transformed = pre.transform(x)
    timings = {"preprocessing_seconds": perf_counter() - started}
    with threadpool_limits(limits=config["threads"]), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        started = perf_counter()
        reference = Ridge(**config["reference_ridge"]).fit(transformed, y)
        timings["reference_ridge_fit_seconds"] = perf_counter() - started
        started = perf_counter()
        appearance = LogisticRegression(**config["appearance"], random_state=config["seed"]).fit(transformed, appeared)
        timings["appearance_fit_seconds"] = perf_counter() - started
        started = perf_counter()
        conditional = Ridge(**config["conditional_ridge"]).fit(transformed[appeared], y[appeared])
        timings["conditional_ridge_fit_seconds"] = perf_counter() - started
    model = TwoStageModels(pre, appearance, conditional, reference, float(y[~appeared].mean()),
                           float(y[appeared].mean()), float(appeared.mean()))
    timings.update({"training_rows": len(y), "appearance_training_rows": int(appeared.sum()),
                    "nonappearance_training_rows": int((~appeared).sum()),
                    "logistic_iterations": appearance.n_iter_.tolist(),
                    "nonappearance_nonzero_points_rows": int(np.count_nonzero(y[~appeared])),
                    "nonappearance_points_sum": float(y[~appeared].sum())})
    return model, timings


def predict_two_stage(model: TwoStageModels, x: np.ndarray, config: dict) -> tuple[dict, dict]:
    started = perf_counter()
    transformed = model.preprocessor.transform(x)
    timings = {"preprocessing_seconds": perf_counter() - started}
    with threadpool_limits(limits=config["threads"]):
        started = perf_counter()
        ridge = model.reference.predict(transformed)
        timings["reference_ridge_seconds"] = perf_counter() - started
        started = perf_counter()
        p = probabilities(model.appearance.predict_proba(transformed)[:, 1])
        conditional = model.conditional.predict(transformed)
        expected = combine_points(p, conditional, model.nonappearance_mean)
        timings["two_stage_seconds"] = perf_counter() - started
    lagged = x[:, FEATURE_COLUMNS.index(config["probability_reference_feature"])]
    # This diagnostic is also usable for missing history: train-only fallback,
    # never a validation-fitted prevalence. Prepared candidates currently have full coverage.
    lagged = probabilities(np.where(np.isnan(lagged), model.appearance_prevalence, lagged))
    predictions = {
        "ridge": ridge, "two_stage": expected, "appearance_probability": p,
        "conditional_points": conditional, "lagged_appearance_probability": lagged,
        # Predeclared diagnostics; no validation fitting or target-conditioned predictions.
        "lagged_probability_conditional_ridge": combine_points(lagged, conditional, model.nonappearance_mean),
        "logistic_constant_return": combine_points(p, np.full(len(p), model.appearance_mean), model.nonappearance_mean),
    }
    if any(not np.isfinite(v).all() for v in predictions.values()):
        raise ValueError("Nonfinite predictions")
    timings["prediction_rows"] = len(x)
    return predictions, timings


def regular_starters(x: np.ndarray, config: dict) -> np.ndarray:
    """Ex-ante subgroup: five observed prior GWs, starts >=80%, mean minutes >=60."""
    rule = config["regular_starters"]
    def column(suffix: str) -> np.ndarray:
        return x[:, FEATURE_COLUMNS.index(f"{rule['window']}_{suffix}")]
    return ((column("count") == rule["count"])
            & (column("start_rate") >= rule["minimum_start_rate"])
            & (column("minutes_mean") >= rule["minimum_minutes_mean"]))
