"""Paired error metrics, retrospective groups and bounded GW-block uncertainty."""

from __future__ import annotations

import numpy as np

from ml.training.data import Population


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    if actual.shape != predicted.shape or not len(actual) or not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Metrics require nonempty aligned finite predictions and targets")
    error = predicted - actual
    absolute = np.abs(error)
    return {"n": len(actual), "mae": float(absolute.mean()),
            "rmse": float(np.sqrt(np.mean(error ** 2))), "bias": float(error.mean()),
            "signed_error_quantiles": dict(zip(("p05", "p50", "p95"), np.quantile(error, [.05, .5, .95]).tolist())),
            "absolute_error_quantiles": dict(zip(("p50", "p90", "p95"), np.quantile(absolute, [.5, .9, .95]).tolist())),
            "fraction_abs_error_le_1": float(np.mean(absolute <= 1)),
            "fraction_abs_error_le_2": float(np.mean(absolute <= 2)),
            "fraction_negative_predictions": float(np.mean(predicted < 0))}


def evaluate(population: Population, predictions: dict[str, np.ndarray]) -> dict:
    groups = {"all": np.ones(len(population.y), dtype=bool),
              "zero_minutes": population.minutes == 0, "nonzero_minutes": population.minutes > 0}
    groups.update({p: population.position == p for p in ("GK", "DEF", "MID", "FWD", "UNKNOWN")})
    return {name: {group: metrics(population.y[mask], values[mask]) if mask.any() else {"n": 0}
                   for group, mask in groups.items()} for name, values in predictions.items()}


def choose_model(validation: dict) -> str:
    return min(validation, key=lambda name: (validation[name]["all"]["mae"],
                                            validation[name]["all"]["rmse"], name))


def paired_mae_uncertainty(actual: np.ndarray, reference: np.ndarray, contender: np.ndarray,
                           gameweek: np.ndarray, *, seed: int, draws: int, block_length: int) -> dict:
    """Negative difference favours contender. Resample contiguous GW blocks, not rows."""
    delta = np.abs(contender - actual) - np.abs(reference - actual)
    gws = np.unique(gameweek)
    sums = np.array([delta[gameweek == g].sum() for g in gws])
    counts = np.array([(gameweek == g).sum() for g in gws])
    if len(gws) < block_length or draws < 1:
        raise ValueError("Insufficient Gameweeks/bootstrap draws")
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(len(gws) / block_length))
    starts = rng.integers(0, len(gws) - block_length + 1, size=(draws, blocks))
    indices = (starts[:, :, None] + np.arange(block_length)).reshape(draws, -1)[:, :len(gws)]
    sampled = sums[indices].sum(axis=1) / counts[indices].sum(axis=1)
    return {"mae_difference_contender_minus_reference": float(delta.mean()),
            "ci95": np.quantile(sampled, [.025, .975]).tolist(),
            "draws": draws, "gameweek_block_length": block_length,
            "gameweeks": len(gws), "method": "paired moving-block GW bootstrap; not a full dependence model"}
