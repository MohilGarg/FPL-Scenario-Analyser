"""Deterministic distribution/invariant audit, not a forecasting calibration result."""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path
from statistics import fmean, pvariance

from fpl_forfeit.forecast import FixtureModel, outlook
from fpl_forfeit.solver import solve_candidate
from fpl_forfeit.state import current_standings
from tests.test_forecast import fixture_state
from tests.validation_states import validation_state


def fixture_audit(count=10000):
    state = fixture_state(5)
    model, rng = FixtureModel(state), random.Random(441)
    fixture = state.fixtures[0]
    home, away, starts, clean, appearances = [], [], 0, 0, Counter()
    events = 0
    for _ in range(count):
        sample = model.sample_fixture(fixture, rng)
        home.append(sample.home_goals)
        away.append(sample.away_goals)
        for team, total in (
            (fixture.home_team_id, sample.home_goals),
            (fixture.away_team_id, sample.away_goals),
        ):
            assert total == sum(e[0] == team for e in sample.events)
        for team, scorer, assister, minute in sample.events:
            events += 1
            assert scorer is None or scorer != assister
            for pid in (scorer, assister):
                if pid is not None:
                    assert state.players[pid].team_id == team
                    a, b = sample.windows[pid]
                    assert a <= minute < b
        for pid, stats in sample.stats.items():
            assert 0 <= stats["minutes"] <= 90
            assert 0 <= stats["bonus"] <= 3
            assert stats["goals_scored"] == sum(e[1] == pid for e in sample.events)
            assert stats["assists"] == sum(e[2] == pid for e in sample.events)
            if stats["clean_sheets"]:
                assert stats["minutes"] >= 60 and stats["goals_conceded"] == 0
        assert sum(s["bonus"] for s in sample.stats.values()) <= 6
        appearances[sample.stats[1]["minutes"]] += 1
        if sample.windows[1][0] == 0:
            starts += 1
            clean += sample.stats[1]["clean_sheets"]
    rates = [model._goal_rate(1, 2, True), model._goal_rate(2, 1, False)]
    for values, rate in zip((home, away), rates, strict=True):
        assert abs(fmean(values) - rate) < 5 * math.sqrt(rate / count)
        assert abs(pvariance(values) - rate) < 0.15
    assert abs(starts / count - model.rates[1].start) < 0.02
    assert abs(clean / starts - math.exp(-rates[1])) < 0.025
    return {
        "draws": count,
        "events_checked": events,
        "invariant_violations": 0,
        "home": {
            "theoretical_mean": rates[0],
            "sample_mean": fmean(home),
            "sample_variance": pvariance(home),
        },
        "away": {
            "theoretical_mean": rates[1],
            "sample_mean": fmean(away),
            "sample_variance": pvariance(away),
        },
        "keeper_start_rate": starts / count,
        "keeper_model_start": model.rates[1].start,
        "keeper_clean_sheet_given_start": clean / starts,
        "theoretical_opponent_goalless": math.exp(-rates[1]),
        "keeper_minutes_counts": dict(appearances),
    }


def stability_audit():
    report = {}
    for remaining in (10, 5, 3, 1):
        state = validation_state(remaining)
        estimates = [outlook(state, seed=seed, samples=4000) for seed in (1, 29, 441)]
        maps = [{r["entry_id"]: r for r in result["managers"]} for result in estimates]
        spans = [
            max(m[e]["last_probability"] for m in maps)
            - min(m[e]["last_probability"] for m in maps)
            for e in maps[0]
        ]
        mean_spans = [
            max(m[e]["projected_score"] for m in maps) - min(m[e]["projected_score"] for m in maps)
            for e in maps[0]
        ]
        for result in estimates:
            assert sum(r["last_probability"] for r in result["managers"]) >= 0.999
            for r in result["managers"]:
                assert (
                    abs(
                        r["last_probability"]
                        - r["outright_last_probability"]
                        - r["tied_last_probability"]
                    )
                    <= 0.00011
                )
        strict = outlook(state, seed=1, samples=4000, allow_tied_last=False)
        assert sum(r["last_probability"] for r in strict["managers"]) <= 1.001
        for a, b in zip(estimates[0]["managers"], strict["managers"], strict=True):
            assert a["projected_score"] == b["projected_score"]
            assert a["outright_last_probability"] == b["last_probability"]
        candidate = current_standings(state)[0].manager
        exact = solve_candidate(state, candidate, max_nodes=3000)
        report[str(remaining)] = {
            "seeds": [1, 29, 441],
            "samples_per_seed": 4000,
            "max_manager_risk_span_percentage_points": round(100 * max(spans), 3),
            "max_manager_projected_score_span": round(max(mean_spans), 3),
            "include_probability_sum_seed_1": round(
                sum(r["last_probability"] for r in estimates[0]["managers"]), 4
            ),
            "strict_probability_sum_seed_1": round(
                sum(r["last_probability"] for r in strict["managers"]), 4
            ),
            "decisive_players": len(estimates[0]["decisive_players"]),
            "exact_candidate": candidate.entry_id,
            "exact_examples": len(exact.scenarios),
            "exact_exhausted": exact.exhausted,
            "exact_nodes": exact.nodes_checked,
        }
        print(remaining, json.dumps(report[str(remaining)]), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "note": "Synthetic consistency and Monte Carlo sampling stability, not predictive accuracy or model calibration.",
        "fixture_distribution": fixture_audit(),
        "stability": stability_audit(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Saved", args.output)


if __name__ == "__main__":
    main()
