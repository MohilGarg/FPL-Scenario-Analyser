"""Portable coherent playing-time inference; fitting lives in ml/train_minutes_model.py.

The state model learns historical nonappearance as an outcome. Official current
availability is a separate experimental cap, not an independently multiplied
injury probability. Its effect cannot be validated without historical vintages.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from ..forecasting.statistics import PlayerRates
from ..models import Player

STATES = ("absent", "sub_short", "sub_long", "start_short", "start_long")
FEATURES = (
    "position_GK",
    "position_DEF",
    "position_MID",
    "position_FWD",
    "history_log_count",
    "history_missing",
    "transfer_history",
    "last_minutes",
    "last_start",
    "absent_streak",
) + tuple(
    f"{name}{window}"
    for window in (3, 5, 10)
    for name in (
        "appearance",
        "start",
        "sixty",
        "sub",
        "minutes",
        "start_duration",
        "sub_duration",
        "short_start",
        "long_sub",
        "count",
    )
)


def playing_state(minutes: int, starts: int) -> int:
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, (int, float))
        or not math.isfinite(minutes)
        or int(minutes) != minutes
        or not 0 <= minutes <= 90
        or starts not in (0, 1)
        or (starts == 1 and minutes == 0)
    ):
        raise ValueError("Explicit valid fixture minutes/starts required")
    return (
        0
        if minutes == 0
        else 1
        if not starts and minutes < 60
        else 2
        if not starts
        else 3
        if minutes < 60
        else 4
    )


def history_features(player: Player, rows: list[dict]) -> dict[str, float]:
    for row in rows:
        playing_state(row.get("minutes"), row.get("starts"))
    n = len(rows)
    streak = 0
    for row in reversed(rows):
        if row["minutes"]:
            break
        streak += 1
    out = {
        **{f"position_{p}": float(player.position.value == p) for p in ("GK", "DEF", "MID", "FWD")},
        "history_log_count": math.log1p(n),
        "history_missing": float(not n),
        "transfer_history": float(
            bool(rows) and rows[-1].get("team_id", player.team_id) != player.team_id
        ),
        "last_minutes": rows[-1]["minutes"] / 90 if rows else 0,
        "last_start": float(rows[-1]["starts"]) if rows else 0,
        "absent_streak": min(10, streak) / 10,
    }
    for window in (3, 5, 10):
        tail = rows[-window:]
        length = max(1, len(tail))
        started = [r["minutes"] for r in tail if r["starts"]]
        substituted = [r["minutes"] for r in tail if not r["starts"] and r["minutes"]]
        values = {
            "appearance": sum(r["minutes"] > 0 for r in tail) / length,
            "start": sum(r["starts"] for r in tail) / length,
            "sixty": sum(r["minutes"] >= 60 for r in tail) / length,
            "sub": len(substituted) / length,
            "minutes": sum(r["minutes"] for r in tail) / (90 * length),
            "start_duration": sum(started) / (90 * max(1, len(started))),
            "sub_duration": sum(substituted) / (90 * max(1, len(substituted))),
            "short_start": sum(r["starts"] and r["minutes"] < 60 for r in tail) / length,
            "long_sub": sum(not r["starts"] and r["minutes"] >= 60 for r in tail) / length,
            "count": len(tail) / window,
        }
        out.update({f"{name}{window}": float(value) for name, value in values.items()})
    return out


def _quantiles(weighted: list[tuple[int, float]], size: int = 256) -> tuple[int, ...]:
    ordered = sorted((int(m), float(w)) for m, w in weighted if w > 0)
    if not ordered or any(not math.isfinite(w) for _, w in ordered):
        raise ValueError("Nonempty finite duration distribution required")
    total = sum(w for _, w in ordered)
    result, j, cumulative = [], 0, ordered[0][1]
    for i in range(size):
        target = (i + 0.5) * total / size
        while cumulative < target and j + 1 < len(ordered):
            j += 1
            cumulative += ordered[j][1]
        result.append(ordered[j][0])
    return tuple(result)


@dataclass(frozen=True, slots=True)
class FittedMinutesEstimate:
    states: tuple[float, float, float, float, float]
    availability: float
    starting_minutes: tuple[int, ...]
    substitute_minutes: tuple[int, ...]
    observations: int
    horizon_days: float
    availability_unknown: bool = False
    source: str = "fitted_minutes"

    @property
    def start_probability(self) -> float:
        return self.availability * sum(self.states[3:])

    @property
    def substitute_probability(self) -> float:
        return self.availability * sum(self.states[1:3])

    @property
    def no_show_probability(self) -> float:
        return 1 - self.start_probability - self.substitute_probability

    @property
    def sixty_probability(self) -> float:
        return self.start_probability * sum(m >= 60 for m in self.starting_minutes) / len(
            self.starting_minutes
        ) + self.substitute_probability * sum(m >= 60 for m in self.substitute_minutes) / len(
            self.substitute_minutes
        )

    @property
    def expected_minutes(self) -> float:
        return (
            self.start_probability * self.conditional_start_minutes
            + self.substitute_probability * self.conditional_substitute_minutes
        )

    @property
    def conditional_start_minutes(self) -> float:
        return sum(self.starting_minutes) / len(self.starting_minutes)

    @property
    def conditional_substitute_minutes(self) -> float:
        return sum(self.substitute_minutes) / len(self.substitute_minutes)

    def rates(self, original: PlayerRates) -> PlayerRates:
        start = sum(self.states[3:])
        return replace(
            original,
            start=start,
            cameo_probability=sum(self.states[1:3]) / (1 - start) if start < 1 else 0,
            starting_minutes=self.starting_minutes,
            cameo_minutes=self.substitute_minutes,
        )


class MinutesModel:
    def __init__(self, path: Path | None = None, *, data: dict | None = None) -> None:
        body = (
            (path or Path(__file__).with_name("minutes_v2.json")).read_bytes()
            if data is None
            else json.dumps(data, allow_nan=False).encode()
        )
        self.data = json.loads(body)
        self.version = self.data["version"]
        self.sha256 = hashlib.sha256(
            json.dumps(self.data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        if (
            self.data["format_version"] != 1
            or self.data["features"] != list(FEATURES)
            or self.data["states"] != list(STATES)
            or self.data["kind"] not in {"softmax", "empirical"}
        ):
            raise ValueError("Unknown coherent minutes schema")
        for key in ("temperature", "duration_strength", "prior_strength", "half_life"):
            value = self.data["config"][key]
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid minutes hyperparameter")
        if self.data["kind"] == "softmax":
            model = self.data["model"]
            if (
                any(len(model[k]) != len(FEATURES) for k in ("mean", "scale"))
                or len(model["coef"]) != 5
                or len(model["intercept"]) != 5
                or any(len(row) != len(FEATURES) for row in model["coef"])
                or any(s <= 0 or not math.isfinite(s) for s in model["scale"])
                or not all(
                    math.isfinite(v)
                    for row in model["coef"] + [model["mean"], model["intercept"]]
                    for v in row
                )
            ):
                raise ValueError("Invalid minutes coefficient/preprocessing contract")
        for position in ("GK", "DEF", "MID", "FWD"):
            prior = self.data["priors"][position]
            if (
                len(prior) != 5
                or not all(math.isfinite(p) and 0 <= p <= 1 for p in prior)
                or abs(sum(prior) - 1) > 1e-8
            ):
                raise ValueError("Invalid training-only state prior")
            cold = self.data.get("cold_priors", self.data["priors"])[position]
            if (
                len(cold) != 5
                or not all(math.isfinite(p) and 0 <= p <= 1 for p in cold)
                or abs(sum(cold) - 1) > 1e-8
            ):
                raise ValueError("Invalid cold-start state prior")
            for state in range(1, 5):
                distribution = self.data["durations"][position][str(state)]
                if not distribution or sum(w for _, w in distribution) <= 0:
                    raise ValueError("Empty conditional duration distribution")
                for minutes, weight in distribution:
                    if (
                        not isinstance(minutes, int)
                        or not 1 <= minutes <= 90
                        or not math.isfinite(weight)
                        or weight < 0
                        or (minutes >= 60) != (state in (2, 4))
                    ):
                        raise ValueError("Invalid conditional duration support")

    def state_probabilities(self, player: Player, rows: list[dict]) -> tuple[float, ...]:
        features = history_features(player, rows)
        prior = self.data["priors"][player.position.value]
        if not rows:
            return tuple(self.data.get("cold_priors", self.data["priors"])[player.position.value])
        if self.data["kind"] == "empirical":
            strength, half_life = (
                self.data["config"]["prior_strength"],
                self.data["config"]["half_life"],
            )
            counts = [strength * p for p in prior]
            for age, row in enumerate(reversed(rows[-20:])):
                counts[playing_state(row["minutes"], row["starts"])] += 2 ** (-age / half_life)
            return tuple(c / sum(counts) for c in counts)
        model = self.data["model"]
        x = [
            (features[name] - mu) / scale
            for name, mu, scale in zip(FEATURES, model["mean"], model["scale"])
        ]
        logits = [
            (bias + math.fsum(w * v for w, v in zip(coef, x))) / self.data["config"]["temperature"]
            for bias, coef in zip(model["intercept"], model["coef"])
        ]
        exp = [math.exp(value - max(logits)) for value in logits]
        return tuple(v / sum(exp) for v in exp)

    def predict(
        self, player: Player, rows: list[dict], *, horizon_days: float = 0
    ) -> FittedMinutesEstimate:
        if not math.isfinite(horizon_days) or horizon_days < 0:
            raise ValueError("Finite nonnegative minutes horizon required")
        probabilities = self.state_probabilities(player, rows)
        prior = self.data["priors"][player.position.value]
        retained = 2 ** (-horizon_days / 28)
        probabilities = tuple(
            retained * p + (1 - retained) * q for p, q in zip(probabilities, prior)
        )
        duration_distributions = {}
        for state in range(1, 5):
            trained = self.data["durations"][player.position.value][str(state)]
            strength = self.data["config"]["duration_strength"]
            total = sum(w for _, w in trained)
            weighted = [(m, strength * w / total) for m, w in trained]
            for age, row in enumerate(reversed(rows[-20:])):
                if playing_state(row["minutes"], row["starts"]) == state:
                    weighted.append((row["minutes"], 2 ** (-age / 4) * retained))
            duration_distributions[state] = weighted

        def mixed(states):
            result = []
            mass = sum(probabilities[s] for s in states)
            for state in states:
                total = sum(w for _, w in duration_distributions[state])
                fraction = probabilities[state] / mass if mass else 1 / len(states)
                result.extend((m, w / total * fraction) for m, w in duration_distributions[state])
            return _quantiles(result)

        starting, substitutes = mixed((3, 4)), mixed((1, 2))
        # Quantise the coherent conditional CDFs to the simulator's uniform tuples.
        # Reconcile state masses to these exact supports (<=1/256 conditional error).
        p_start, p_sub = sum(probabilities[3:]), sum(probabilities[1:3])
        p_long_start = sum(m >= 60 for m in starting) / len(starting)
        p_long_sub = sum(m >= 60 for m in substitutes) / len(substitutes)
        states = (
            probabilities[0],
            p_sub * (1 - p_long_sub),
            p_sub * p_long_sub,
            p_start * (1 - p_long_start),
            p_start * p_long_start,
        )
        chance = player.chance_of_playing
        if chance is not None and not 0 <= chance <= 100:
            raise ValueError("Invalid official availability")
        official = (
            chance / 100
            if chance is not None
            else 0
            if player.availability_status in {"i", "s", "u", "n"}
            else 1
        )
        unknown = horizon_days > 0 and official < 1
        cap = 1 if unknown else official
        availability = min(1, cap / max(1e-12, 1 - states[0]))
        return FittedMinutesEstimate(
            states,
            availability,
            starting,
            substitutes,
            len(rows),
            horizon_days,
            unknown,
            "fitted_minutes" if self.data["kind"] == "softmax" else "tuned_empirical_minutes",
        )
