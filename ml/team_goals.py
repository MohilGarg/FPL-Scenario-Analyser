"""Private portable team-goal challenger; rates use buffered completed results only."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path


def team_features(
    fixtures: list[dict], gw: int, cutoff: datetime, *, strength: float = 8, half_life: float = 6
) -> dict[int, tuple[float, float]]:
    eligible = sorted(
        (
            f
            for f in fixtures
            if f.get("finished")
            and f.get("event") is not None
            and f["event"] < gw
            and f.get("kickoff_time")
            and datetime.fromisoformat(f["kickoff_time"]) + timedelta(hours=24) < cutoff
            and f.get("team_h_score") is not None
            and f.get("team_a_score") is not None
        ),
        key=lambda f: f["kickoff_time"],
    )
    teams = {int(f[k]) for f in fixtures for k in ("team_h", "team_a")}
    result = {}
    for team in teams:
        matches = [
            (f["team_h_score"], f["team_a_score"])
            if f["team_h"] == team
            else (f["team_a_score"], f["team_h_score"])
            for f in eligible
            if team in (f["team_h"], f["team_a"])
        ]
        weights = [2 ** (-age / half_life) for age in range(len(matches))]
        matches = list(reversed(matches))
        result[team] = tuple(
            (strength * 1.4 + sum(w * score[k] for w, score in zip(weights, matches)))
            / (strength + sum(weights))
            for k in (0, 1)
        )
    return result


def covariates(strengths: dict, own: int, opponent: int, home: bool) -> tuple[float, float, float]:
    return math.log(strengths[own][0]), math.log(strengths[opponent][1]), float(home)


class TeamGoalModel:
    def __init__(self, path: Path | None = None, *, data: dict | None = None):
        self.data = (
            data
            if data is not None
            else json.loads((path or Path(__file__).with_name("team_goals_v2.json")).read_bytes())
        )
        if (
            self.data["version"] != "team-goals-v2"
            or len(self.data["coefficients"]) != 3
            or not all(
                math.isfinite(v) for v in [self.data["intercept"], *self.data["coefficients"]]
            )
        ):
            raise ValueError("Invalid team-goal model")

    def rates(
        self, fixtures: list[dict], gw: int, cutoff: datetime
    ) -> dict[tuple[int, int], float]:
        features = team_features(
            fixtures, gw, cutoff, strength=self.data["strength"], half_life=self.data["half_life"]
        )
        result = {}
        for fixture in fixtures:
            if fixture.get("event") != gw:
                continue
            for own, opponent, home in (
                (fixture["team_h"], fixture["team_a"], True),
                (fixture["team_a"], fixture["team_h"], False),
            ):
                x = covariates(features, own, opponent, home)
                result[fixture["id"], own] = min(
                    4.5,
                    max(
                        0.15,
                        math.exp(
                            self.data["intercept"]
                            + sum(c * v for c, v in zip(self.data["coefficients"], x))
                        ),
                    ),
                )
        return result
