"""Portable regularised component inference, independent of scientific dependencies."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .component_features import FEATURE_COLUMNS


class ComponentModel:
    def __init__(self, path: Path | None = None, *, data: dict | None = None):
        body = (
            (path or Path(__file__).with_name("component_v1.json")).read_bytes()
            if data is None
            else json.dumps(data).encode()
        )
        self.data = json.loads(body)
        if self.data["features"] != list(FEATURE_COLUMNS) or self.data["format_version"] != 1:
            raise ValueError("Component schema mismatch")
        self.version = self.data["version"]
        self.sha256 = hashlib.sha256(body).hexdigest()
        for model in self.data["models"].values():
            n = len(model["names"])
            vectors = [model[k] for k in ("median", "mean", "scale")]
            coefficients = model["coef"] if model["link"] == "softmax" else [model["coef"]]
            biases = model["intercept"] if model["link"] == "softmax" else [model["intercept"]]
            if (
                not n
                or len(set(model["names"])) != n
                or not set(model["names"]) <= set(FEATURE_COLUMNS)
                or any(len(v) != n for v in vectors + coefficients)
                or any(not math.isfinite(v) for vector in vectors + coefficients for v in vector)
                or any(not math.isfinite(v) for v in biases)
                or any(s <= 0 for s in model["scale"])
                or model["link"] not in ("identity", "log", "softmax")
                or model["link"] == "softmax"
                and (len(coefficients) != 4 or len(biases) != 4)
            ):
                raise ValueError("Invalid preprocessing")

    def predict(self, name: str, values: dict):
        m = self.data["models"][name]
        x = [
            (float(values[c]) if values[c] is not None else median)
            for c, median in zip(m["names"], m["median"])
        ]
        x = [(v - mu) / sigma for v, mu, sigma in zip(x, m["mean"], m["scale"])]
        if not all(math.isfinite(v) for v in x):
            raise ValueError("Nonfinite component input")
        if m["link"] == "softmax":
            logits = [
                bias + math.fsum(w * v for w, v in zip(weights, x))
                for bias, weights in zip(m["intercept"], m["coef"])
            ]
            exp = [math.exp(v - max(logits)) for v in logits]
            return [v / sum(exp) for v in exp]
        z = m["intercept"] + math.fsum(w * v for w, v in zip(m["coef"], x))
        return math.exp(min(20, max(-20, z))) if m["link"] == "log" else z

    def proposals(self, values: dict) -> dict:
        probabilities = self.predict("playing_state", values)
        durations = {}
        for state, low, high in ((1, 1, 89), (2, 1, 59), (3, 60, 90)):
            mu = self.predict(f"duration{state}", values)
            durations[state] = tuple(
                round(min(high, max(low, mu + r)))
                for r in self.data["models"][f"duration{state}"]["residual_quantiles"]
            )
        start = probabilities[2] + probabilities[3]
        long_count = round(100 * probabilities[3] / start) if start else 0
        starting = tuple(durations[3][i % len(durations[3])] for i in range(long_count)) + tuple(
            durations[2][i % len(durations[2])] for i in range(100 - long_count)
        )
        return {
            "start": start,
            "cameo_probability": probabilities[1] / (1 - start) if start < 1 else 0,
            "starting_minutes": starting,
            "cameo_minutes": durations[1],
            "goal": min(3, self.predict("goals", values)),
            "assist": min(3, self.predict("assists", values)),
            "yellow": min(1, self.predict("yellow", values)),
            "saves": min(10, self.predict("saves", values)) if values["position_GK"] else 0,
            "state_probabilities": probabilities,
            "expected_minutes_proposal": sum(
                probabilities[k] * sum(durations[k]) / len(durations[k]) for k in (1, 2, 3)
            ),
        }
