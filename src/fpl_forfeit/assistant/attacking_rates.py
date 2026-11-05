"""Portable private calibration of goal/FPL-assist allocation weights."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from ..forecasting.statistics import PlayerRates, player_rates
from ..models import Player

FEATURES = ("log_goal", "log_assist", "log_exposure", "GK", "DEF", "MID", "FWD")


def features(player: Player) -> tuple[float, ...]:
    rate = player_rates(player)
    return (
        math.log(max(0.0001, rate.goal)),
        math.log(max(0.0001, rate.assist)),
        math.log1p((player.season_minutes or 0) / 90),
        *(float(player.position.value == p) for p in ("GK", "DEF", "MID", "FWD")),
    )


class AttackingRateModel:
    def __init__(self, path: Path | None = None, *, data: dict | None = None):
        self.data = (
            data
            if data is not None
            else json.loads((path or Path(__file__).with_name("attacking_v2.json")).read_bytes())
        )
        if self.data["version"] != "attacking-rates-v2" or self.data["features"] != list(FEATURES):
            raise ValueError("Unknown attacking-rate schema")
        for model in self.data["models"].values():
            if len(model["coefficients"]) != len(FEATURES) or not all(
                math.isfinite(v) for v in [model["intercept"], *model["coefficients"]]
            ):
                raise ValueError("Invalid attacking-rate coefficients")

    def rates(self, player: Player, original: PlayerRates) -> PlayerRates:
        x = features(player)
        proposals = {
            name: min(
                3.0,
                max(
                    0.00001,
                    math.exp(
                        model["intercept"] + sum(c * v for c, v in zip(model["coefficients"], x))
                    ),
                ),
            )
            for name, model in self.data["models"].items()
        }
        return replace(original, goal=proposals["goal"], assist=proposals["assist"])
