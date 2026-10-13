"""Validation-only diagnostics and paired Gameweek-block uncertainty."""

from __future__ import annotations

import numpy as np

from ml.evaluation.metrics import evaluate, metrics
from ml.training.data import Population
from ml.training.two_stage_models import probabilities, regular_starters


def probability_metrics(actual: np.ndarray, p: np.ndarray, bins: int) -> dict:
    p = probabilities(p)
    if actual.shape != p.shape or not len(p) or not np.isin(actual, [0, 1]).all():
        raise ValueError("Binary probability targets must be nonempty and aligned")
    # Clip solely for finite log-loss arithmetic; original probabilities are retained.
    clipped = np.clip(p, np.finfo(float).eps, 1 - np.finfo(float).eps)
    assigned = np.minimum((p * bins).astype(int), bins - 1)
    calibration = []
    for i in range(bins):
        mask = assigned == i
        calibration.append({"lower": i / bins, "upper": (i + 1) / bins, "n": int(mask.sum()),
                            "prediction_mean": float(p[mask].mean()) if mask.any() else None,
                            "appearance_rate": float(actual[mask].mean()) if mask.any() else None})
    ece = sum(b["n"] * abs(b["prediction_mean"] - b["appearance_rate"])
              for b in calibration if b["n"]) / len(p)
    return {"n": len(p), "brier": float(np.mean((p - actual) ** 2)),
            "log_loss": float(-np.mean(actual * np.log(clipped) + (1 - actual) * np.log1p(-clipped))),
            "actual_appearance_rate": float(actual.mean()), "prediction_mean": float(p.mean()),
            "equal_width_ece": float(ece), "calibration": calibration}


def points_calibration(actual: np.ndarray, prediction: np.ndarray, edges: list[float]) -> dict:
    # Bands fixed in the versioned configuration before validation outcomes are read.
    bounds = np.array([-np.inf, *edges, np.inf])
    assigned = np.searchsorted(edges, prediction, side="right")
    bands = []
    for i in range(len(bounds) - 1):
        mask = assigned == i
        bands.append({"band": f"[{bounds[i]:g}, {bounds[i+1]:g})", "n": int(mask.sum()),
                      "prediction_mean": float(prediction[mask].mean()) if mask.any() else None,
                      "actual_mean": float(actual[mask].mean()) if mask.any() else None})
    return {"bands": bands, "weighted_absolute_band_gap": float(sum(
        b["n"] * abs(b["prediction_mean"] - b["actual_mean"]) for b in bands if b["n"]) / len(actual))}


def paired_uncertainty(actual: np.ndarray, reference: np.ndarray, contender: np.ndarray,
                       gameweek: np.ndarray, config: dict) -> dict:
    """Paired MAE/RMSE/bias deltas, retaining all player rows within sampled GW blocks."""
    if (actual.shape != reference.shape or actual.shape != contender.shape or actual.shape != gameweek.shape
            or not all(np.isfinite(a).all() for a in (actual, reference, contender))):
        raise ValueError("Paired uncertainty requires aligned finite arrays")
    length = config["uncertainty"]["gameweek_block_length"]
    draws = config["uncertainty"]["bootstrap_draws"]
    gws = np.unique(gameweek)
    if len(gws) < length or length < 1 or draws < 1:
        raise ValueError("Insufficient Gameweeks/bootstrap draws")
    # Numeric consecutive GWs, not an accidentally reordered observation sequence.
    if (np.diff(gws) != 1).any():
        raise ValueError("Moving blocks require contiguous Gameweeks")
    counts = np.array([(gameweek == g).sum() for g in gws])
    errors = [reference - actual, contender - actual]
    sums = np.array([[[f(e[gameweek == g]).sum() for g in gws] for f in (np.abs, np.square, lambda v: v)]
                     for e in errors])
    rng = np.random.default_rng(config["seed"])
    starts = rng.integers(0, len(gws) - length + 1, size=(draws, int(np.ceil(len(gws) / length))))
    indices = (starts[:, :, None] + np.arange(length)).reshape(draws, -1)[:, :len(gws)]
    sampled = sums[:, :, indices].sum(axis=-1) / counts[indices].sum(axis=-1)
    sampled[:, 1, :] = np.sqrt(sampled[:, 1, :])
    deltas = sampled[1] - sampled[0]
    point = [metrics(actual, p) for p in (reference, contender)]
    return {"differences_contender_minus_ridge": {name: {
                "estimate": point[1][name] - point[0][name],
                "ci95": np.quantile(deltas[i], [.025, .975]).tolist()}
            for i, name in enumerate(("mae", "rmse", "bias"))},
            "draws": draws, "gameweek_block_length": length, "gameweeks": len(gws),
            "method": "paired moving contiguous GW blocks, equal original GW count, row-weighted metrics"}


def evaluate_two_stage(population: Population, prediction: dict, model, config: dict) -> dict:
    points = {k: prediction[k] for k in ("ridge", "two_stage", "lagged_probability_conditional_ridge", "logistic_constant_return")}
    grouped = evaluate(population, points)
    starters = regular_starters(population.x, config)
    for name, values in points.items():
        grouped[name]["pre_deadline_regular_starters"] = metrics(population.y[starters], values[starters]) if starters.any() else {"n": 0}
    appeared = population.minutes > 0
    probability = {"logistic": prediction["appearance_probability"],
                   "lagged_last5_gws": prediction["lagged_appearance_probability"],
                   "training_prevalence": np.full(len(appeared), model.appearance_prevalence)}
    return {"points": grouped,
            "appearance": {name: probability_metrics(appeared, p, config["calibration"]["probability_bins"])
                           for name, p in probability.items()},
            "points_calibration": {name: points_calibration(population.y, points[name], config["calibration"]["points_band_edges"])
                                   for name in ("ridge", "two_stage")},
            "uncertainty": paired_uncertainty(population.y, points["ridge"], points["two_stage"], population.gameweek, config),
            "conditional_returns_retrospective": {
                "conditional_ridge": metrics(population.y[appeared], prediction["conditional_points"][appeared]),
                "training_appearance_mean": metrics(population.y[appeared], np.full(appeared.sum(), model.appearance_mean))},
            "nonappearance_retrospective": {"n": int((~appeared).sum()),
                 "nonzero_points_n": int(np.count_nonzero(population.y[~appeared])),
                 "actual_points_mean": float(population.y[~appeared].mean())},
            "zero_nonappearance_approximation_max_prediction_difference": float(
                np.max(np.abs((1 - prediction["appearance_probability"]) * model.nonappearance_mean)))}
