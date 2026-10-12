"""Fixed small models; preprocessing is fitted exclusively on supplied training X."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import lightgbm as lgb
import numpy as np
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits

from ml.dataset import FEATURE_COLUMNS


@dataclass
class Preprocessor:
    names: tuple[str, ...]
    kept: np.ndarray
    median: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    dropped_empty: list[str]
    dropped_constant: list[str]

    @classmethod
    def fit(cls, x: np.ndarray, names: tuple[str, ...]) -> Preprocessor:
        if tuple(names) != FEATURE_COLUMNS or x.ndim != 2 or x.shape[1] != len(names) or len(x) == 0:
            raise ValueError("Training must use the exact approved feature list and nonempty X")
        if np.isinf(x).any():
            raise ValueError("Infinite training inputs")
        present = ~np.isnan(x).all(axis=0)
        indices = np.flatnonzero(present)
        medians = np.nanmedian(x[:, indices], axis=0)
        imputed = np.where(np.isnan(x[:, indices]), medians, x[:, indices])
        scales = imputed.std(axis=0)
        varying = scales > 0
        if not varying.any():
            raise ValueError("Insufficient varying training features")
        return cls(names, indices[varying], medians[varying], imputed.mean(axis=0)[varying],
                   scales[varying], [names[i] for i in np.flatnonzero(~present)],
                   [names[i] for i in indices[~varying]])

    def transform(self, x: np.ndarray) -> np.ndarray:
        if x.ndim != 2 or x.shape[1] != len(self.names) or np.isinf(x).any():
            raise ValueError("Predictive input schema mismatch/nonfinite values")
        retained = x[:, self.kept]
        imputed = np.where(np.isnan(retained), self.median, retained)
        return (imputed - self.mean) / self.scale

    def metadata(self) -> dict:
        return {"approved_inputs": list(self.names), "kept_indices": self.kept.tolist(),
                "kept_features": [self.names[i] for i in self.kept],
                "training_medians": self.median.tolist(), "training_means": self.mean.tolist(),
                "training_scales": self.scale.tolist(), "dropped_training_empty": self.dropped_empty,
                "dropped_training_constant": self.dropped_constant,
                "future_only_values_of_dropped_features": "ignored",
                "missing_indicators": "existing approved coverage indicators only"}


def fit_models(x: np.ndarray, y: np.ndarray, config: dict) -> tuple[Preprocessor, dict, dict]:
    started = perf_counter()
    preprocessor = Preprocessor.fit(x, FEATURE_COLUMNS)
    processed = preprocessor.transform(x)
    preprocessing_seconds = perf_counter() - started
    models = {"recent_average": None}
    timings = {"recent_average": 0.0}
    with threadpool_limits(limits=config["threads"]):
        started = perf_counter()
        models["ridge"] = Ridge(**config["ridge"]).fit(processed, y)
        timings["ridge"] = perf_counter() - started
        params = {**config["lightgbm"]["params"], "num_threads": config["threads"],
                  "seed": config["seed"], "feature_fraction_seed": config["seed"],
                  "data_random_seed": config["seed"]}
        started = perf_counter()
        training = lgb.Dataset(processed, label=y,
                               feature_name=[FEATURE_COLUMNS[i] for i in preprocessor.kept])
        models["lightgbm"] = lgb.train(params, training, num_boost_round=config["lightgbm"]["num_boost_round"])
        timings["lightgbm"] = perf_counter() - started
    return preprocessor, models, {"preprocessing_fit_transform_seconds": preprocessing_seconds,
                                  "model_fit_seconds": timings, "training_rows": len(y)}


def predict_models(preprocessor: Preprocessor, models: dict, x: np.ndarray,
                   config: dict) -> tuple[dict[str, np.ndarray], dict]:
    started = perf_counter()
    processed = preprocessor.transform(x)
    preprocessing_seconds = perf_counter() - started
    predictions, timings = {}, {}
    with threadpool_limits(limits=config["threads"]):
        for name, model in models.items():
            started = perf_counter()
            if name == "recent_average":
                prediction = x[:, FEATURE_COLUMNS.index(config["recent_average"]["feature"])].copy()
            elif name == "lightgbm":
                prediction = model.predict(processed, num_threads=config["threads"])
            else:
                prediction = model.predict(processed)
            if not np.isfinite(prediction).all():
                raise ValueError(f"Nonfinite {name} predictions")
            predictions[name] = prediction
            timings[name] = perf_counter() - started
    return predictions, {"preprocessing_transform_seconds": preprocessing_seconds,
                         "model_inference_seconds": timings, "prediction_rows": len(x)}
