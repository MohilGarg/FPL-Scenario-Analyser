"""Public league projections, last-place estimates and forced-event comparisons.

Football rates, outcome distributions and player projections live in forecasting/.
This adapter alone reconciles official event totals and applies league scoring.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import asdict
from statistics import fmean, median
from typing import Any

from .forecasting.models import (
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    MODEL_VERSION,
    OVERVIEW_SAMPLES,
    Condition,
    DataSource,
    FixtureSample,
    ForecastInputs,
    PlayerFixtureProjection,
    ScenarioConflict,
    canonical_conditions,
)
from .forecasting.projections import ProjectionAccumulator
from .forecasting.simulation import FixtureSimulator
from .forecasting.simulation import fixture_clock as _fixture_clock
from .models import ElementScore, Fixture, LeagueState
from .points import event_points
from .scoring import final_manager_scores, last_ids
from .state import current_standings
from .substitutions import effective_multipliers

__all__ = [
    "DEFAULT_SAMPLES",
    "DEFAULT_SEED",
    "MODEL_VERSION",
    "OVERVIEW_SAMPLES",
    "Condition",
    "FixtureModel",
    "ScenarioConflict",
    "canonical_conditions",
    "catalog",
    "condition_payload",
    "fixture_clock",
    "football_inputs",
    "outlook",
    "projection_payload",
]

ASSUMPTIONS = [
    "Experimental internal model; probabilities have not yet been calibrated against saved live Gameweeks.",
    "Team goals use independent Poisson counts with observed season scoring/conceding rates shrunk by eight matches; no xG or paid data.",
    "Player goal/assist shares use observed season totals shrunk by 450 minutes to position priors; unowned teammates can score and assist.",
    "Starts and official availability inform appearances; starting minutes use season minutes/starts shrunk towards position defaults. Missing history uses explicit position/appearance priors, not verified lineups.",
    "Events share a scoreline and on-field windows. Goals conceded after an ordinary substitution do not remove earned clean sheets; red cards remain exposed.",
    "Bonus is a shared 3/2/1 performance approximation, not a reproduction of Opta BPS. Defensive actions and saves are Poisson approximations.",
    "Future penalties, own goals and red cards are omitted; already awarded events are preserved. Yellow cards use observed rates with a prior.",
    "Live substitution times are absent from the public feed: players whose minutes trail the observed match clock are held at their current minutes. This can understate live substitute involvement.",
    "Score intervals describe sampled football outcomes. Probability intervals describe sampling error only, and exclude model/calibration error.",
    "A zero sampled chance is never a safety proof. Safety uses the separate configured total remaining player-contribution envelope, not a theoretical football bound.",
]


def football_inputs(state: LeagueState) -> ForecastInputs:
    """Strip league identity, squads, multipliers, hits and official event totals."""
    return ForecastInputs(
        players=state.players,
        fixtures=() if state.event_final else state.fixtures,
        fetched_at=state.fetched_at,
        fixture_minutes=state.fixture_minutes,
        fixture_stats=state.fixture_stats,
        team_goal_rates=state.team_goal_rates,
        provenance=(DataSource(state.provenance),),
    )


def fixture_clock(state: LeagueState, fixture: Fixture) -> int:
    return _fixture_clock(football_inputs(state), fixture)


class FixtureModel(FixtureSimulator):
    """Public compatibility adapter over the shared, squad-independent simulator."""

    def __init__(self, state: LeagueState, conditions: tuple[Condition, ...] = ()) -> None:
        self.state = state
        self.owned = {p.player_id for m in state.managers for p in m.picks}
        super().__init__(football_inputs(state), conditions, player_ids=tuple(sorted(self.owned)))

    def draw(self, rng: random.Random) -> tuple[dict[int, ElementScore], tuple[FixtureSample, ...]]:
        scores = dict(self.state.live_scores)
        fixtures = self.sample(rng)
        for sample in fixtures:
            for pid, stats in sample.stats.items():
                old = self.state.fixture_stats.get((pid, sample.fixture_id), {})
                # Preserve official awarded totals, replacing only this unfinished fixture's
                # mutable categories. Completed DGW fixtures are never reconstructed.
                delta = event_points(self.players[pid].position, stats) - event_points(
                    self.players[pid].position, old
                )
                original = scores[pid]
                scores[pid] = ElementScore(
                    original.points + delta,
                    original.minutes + stats["minutes"] - old.get("minutes", 0),
                    carded=original.carded
                    or bool(stats.get("yellow_cards", 0) or stats.get("red_cards", 0)),
                )
        return scores, fixtures


def _wilson(count: int, n: int) -> list[float]:
    p, z = count / n, 1.96
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [round(max(0, centre - half), 4), round(min(1, centre + half), 4)]


def _quantile(values: list[int], probability: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * probability))]


def outlook(
    state: LeagueState,
    *,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    allow_tied_last: bool = True,
    conditions: tuple[Condition, ...] = (),
    include_risk: bool = True,
) -> dict[str, Any]:
    if samples < 1 or samples > 5000:
        raise ValueError("Simulation count must be between 1 and 5000.")
    model = FixtureModel(state, conditions)
    rng = random.Random(seed)
    projections = ProjectionAccumulator(model.inputs, model.players)
    current = {s.manager.entry_id: s.effective_score for s in current_standings(state)}
    totals: dict[int, list[int]] = {m.entry_id: [] for m in state.managers}
    counted = {m.entry_id: 0 for m in state.managers}
    outright, tied = counted.copy(), counted.copy()
    remaining = {
        pid for pid in model.owned if state.unfinished_fixtures_for_team(state.players[pid].team_id)
    }
    player_points: dict[int, list[int]] = {pid: [] for pid in remaining}
    fixture_points = {
        f.id: {pid: [] for pid in remaining if f.involves(state.players[pid].team_id)}
        for f in model.fixtures
    }
    exposures: dict[int, dict[int, float]] = {pid: defaultdict(float) for pid in model.owned}
    complete = {p.team_id: True for p in state.players.values()}
    multiplier_cache: dict[int, dict[tuple[int, int], dict[int, int]]] = {
        m.entry_id: {} for m in state.managers
    }
    bottom_samples: list[tuple[int, ...]] = []
    n = samples if model.fixtures else 1
    for _ in range(n):
        scores, fixtures = model.draw(rng)
        projections.add(fixtures)
        resolved = {}
        for m in state.managers:
            appearance = sum(1 << i for i, p in enumerate(m.picks) if scores[p.player_id].appeared)
            minutes = sum(1 << i for i, p in enumerate(m.picks) if scores[p.player_id].minutes > 0)
            key = (appearance, minutes)
            cached = multiplier_cache[m.entry_id].get(key)
            if cached is None:
                cached = effective_multipliers(m, state.players, scores, complete)
                multiplier_cache[m.entry_id][key] = cached
            resolved[m.entry_id] = cached
        final = final_manager_scores(state, scores, multipliers=resolved)
        bottom = last_ids(final)
        selected = last_ids(final, allow_tied_last)
        bottom_samples.append(selected)
        for entry, value in final.items():
            totals[entry].append(value)
            counted[entry] += entry in selected
            outright[entry] += entry in bottom and len(bottom) == 1
            tied[entry] += entry in bottom and len(bottom) > 1
        for pid in remaining:
            player_points[pid].append(scores[pid].points - state.live_scores[pid].points)
        for f in fixtures:
            for pid, values in fixture_points[f.fixture_id].items():
                stats = f.stats.get(pid)
                old = state.fixture_stats.get((pid, f.fixture_id), {})
                values.append(
                    event_points(state.players[pid].position, stats)
                    - event_points(state.players[pid].position, old)
                    if stats
                    else 0
                )
        for m in state.managers:
            multipliers = resolved[m.entry_id]
            for pid, multiplier in multipliers.items():
                exposures[pid][m.entry_id] += multiplier / n
    means = {entry: fmean(values) for entry, values in totals.items()}
    bottom_mean = min(means.values(), default=0)
    projected_bottom = [entry for entry, value in means.items() if abs(value - bottom_mean) < 1e-9]
    rows = []
    for m in state.managers:
        entry, values = m.entry_id, totals[m.entry_id]
        rows.append(
            {
                "entry_id": entry,
                "current_score": current[entry],
                "projected_score": round(means[entry], 2),
                "mean_score": round(means[entry], 2),
                "median_score": median(values),
                "expected_remaining": round(means[entry] - current[entry], 2),
                "score_interval_80": [_quantile(values, 0.1), _quantile(values, 0.9)],
                "projected_rank": 1 + sum(value > means[entry] for value in means.values()),
                "gap_from_projected_bottom": round(means[entry] - bottom_mean, 2),
                "last_probability": round(counted[entry] / n, 4) if include_risk else None,
                "outright_last_probability": round(outright[entry] / n, 4)
                if include_risk
                else None,
                "tied_last_probability": round(tied[entry] / n, 4) if include_risk else None,
                "probability_interval_95": _wilson(counted[entry], n)
                if include_risk and model.fixtures
                else None,
                "risk_label": (
                    "high"
                    if counted[entry] / n >= 0.25
                    else "moderate"
                    if counted[entry] / n >= 0.05
                    else "low"
                )
                if include_risk
                else "pending",
                "captaincy_exposure": [
                    {
                        "player_id": p.player_id,
                        "player_name": state.players[p.player_id].name,
                        "mean_multiplier": round(exposures[p.player_id][entry], 2),
                    }
                    for p in m.picks
                    if p.is_captain or p.is_vice_captain
                ],
            }
        )
    rows.sort(key=lambda row: (-row["projected_score"], row["entry_id"]))
    missing = sum(
        p.season_goals is None or not p.season_minutes
        for p in model.players.values()
        if state.unfinished_fixtures_for_team(p.team_id)
    )
    live_missing = sum(
        (p.id, f.id) not in state.fixture_stats
        for f in model.fixtures
        if f.started
        for p in model.players.values()
        if f.involves(p.team_id)
    )
    return {
        "source": state.fetched_at.isoformat(),
        "model_version": MODEL_VERSION,
        "state": "complete" if not model.fixtures else "ready" if include_risk else "projection",
        "simulations": n,
        "seed": seed,
        "estimated": bool(model.fixtures),
        "tie_convention": "Every tied bottom manager counts; probabilities may sum above 100%."
        if allow_tied_last
        else "Only outright last counts; bottom ties result in no forfeit.",
        "tie_rule": "include" if allow_tied_last else "strict",
        "managers": rows,
        "player_projections": [projection_payload(p) for p in projections.results()],
        "projected_bottom_entry_ids": projected_bottom,
        "projected_last_entry_ids": projected_bottom
        if allow_tied_last or len(projected_bottom) == 1
        else [],
        "decisive_players": _decisive(state, model, rows, player_points, exposures, bottom_samples),
        "important_fixtures": _important_fixtures(state, model, fixture_points, exposures, rows),
        "assumptions": ASSUMPTIONS,
        "coverage": {
            "remaining_owned_players_using_priors": missing,
            "live_player_breakdowns_missing_and_frozen": live_missing,
            "team_history_available": bool(state.team_goal_rates),
        },
        "conditions": [condition_payload(state, c) for c in model.conditions],
        "catalog": catalog(state),
    }


def _decisive(
    state: LeagueState,
    model: FixtureModel,
    rows: list[dict[str, Any]],
    points: dict[int, list[int]],
    exposure: dict[int, dict[int, float]],
    bottom: list[tuple[int, ...]],
) -> list[dict[str, Any]]:
    # Compare across the whole sampled bottom contest, independent of frontend filters.
    contenders = [
        r["entry_id"]
        for r in rows
        if r["gap_from_projected_bottom"] <= 15 or (r["last_probability"] or 0) >= 0.01
    ]
    if len(contenders) < 2:
        contenders = [r["entry_id"] for r in sorted(rows, key=lambda r: r["mean_score"])[:2]]
    names = {m.entry_id: m.manager_name for m in state.managers}
    result = []
    for pid, values in points.items():
        p = state.players[pid]
        if not state.unfinished_fixtures_for_team(p.team_id):
            continue
        multipliers = [exposure[pid].get(entry, 0) for entry in contenders]
        spread = max(multipliers, default=0) - min(multipliers, default=0)
        swing = (_quantile(values, 0.9) - _quantile(values, 0.1)) * spread
        if spread < 0.05 or swing < 0.5:
            continue
        mean = fmean(values)
        high = [i for i, value in enumerate(values) if value > mean]
        low = [i for i, value in enumerate(values) if value <= mean]
        sensitivity = (
            {
                entry: (
                    sum(entry in bottom[i] for i in high) / len(high)
                    - sum(entry in bottom[i] for i in low) / len(low)
                )
                for entry in contenders
            }
            if high and low
            else {}
        )
        average_exposure = fmean(multipliers)
        beneficiaries = [
            names[e] for e in contenders if exposure[pid].get(e, 0) > average_exposure + 1e-9
        ]
        blank_beneficiaries = [
            names[e] for e in contenders if exposure[pid].get(e, 0) < average_exposure - 1e-9
        ]
        result.append(
            {
                "player_id": pid,
                "player_name": p.name,
                "position": p.position.value,
                "team_id": p.team_id,
                "team_name": p.team_name,
                "swing_points": round(swing, 1),
                "expected_points_from_here": round(mean, 2),
                "exposure_spread": round(spread, 2),
                "strong_performance_helps": beneficiaries,
                "blank_helps": blank_beneficiaries,
                "strong_performance_helps_entry_ids": [
                    e for e in contenders if exposure[pid].get(e, 0) > average_exposure + 1e-9
                ],
                "blank_helps_entry_ids": [
                    e for e in contenders if exposure[pid].get(e, 0) < average_exposure - 1e-9
                ],
                "sampled_risk_association": {str(e): round(v, 3) for e, v in sensitivity.items()},
                "association_note": "Observed association within shared-fixture samples; not a causal or necessary condition.",
                "status": "live"
                if any(f.started for f in state.unfinished_fixtures_for_team(p.team_id))
                else "yet_to_play",
                "fixture_ids": [f.id for f in state.unfinished_fixtures_for_team(p.team_id)],
                "explanation": f"{spread:.1f}× effective exposure difference across the bottom contest; "
                f"about {swing:.1f} net points between the sampled 10th/90th percentiles. "
                "Higher/lower exposure groups are compared with that contest's average. "
                "Exposure averages include possible autosubs and vice-captain handover.",
            }
        )
    return sorted(result, key=lambda p: -p["swing_points"])[:8]


def _important_fixtures(
    state: LeagueState,
    model: FixtureModel,
    points: dict[int, dict[int, list[int]]],
    exposure: dict[int, dict[int, float]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    contenders = [
        r["entry_id"]
        for r in rows
        if r["gap_from_projected_bottom"] <= 15 or (r["last_probability"] or 0) >= 0.01
    ]
    if len(contenders) < 2:
        contenders = [r["entry_id"] for r in sorted(rows, key=lambda r: r["mean_score"])[:2]]
    result = []
    for f in model.fixtures:
        leverage = sum(
            (_quantile(values, 0.9) - _quantile(values, 0.1))
            * (
                max((exposure[pid].get(e, 0) for e in contenders), default=0)
                - min((exposure[pid].get(e, 0) for e in contenders), default=0)
            )
            for pid, values in points[f.id].items()
        )
        result.append(
            {
                "fixture_id": f.id,
                "label": f"{f.home_team_name} v {f.away_team_name}",
                "status": "live" if f.started else "upcoming",
                "kickoff_time": f.kickoff_time.isoformat() if f.kickoff_time else None,
                "relative_leverage": round(leverage, 1),
            }
        )
    return sorted(result, key=lambda f: -f["relative_leverage"])[:5]


def catalog(state: LeagueState) -> list[dict[str, Any]]:
    owned = {p.player_id for m in state.managers for p in m.picks}
    return [
        {
            "fixture_id": f.id,
            "label": f"{f.home_team_name} v {f.away_team_name}",
            "started": f.started,
            "home_team_id": f.home_team_id,
            "away_team_id": f.away_team_id,
            "home_team_name": f.home_team_name,
            "away_team_name": f.away_team_name,
            "home_score": f.home_score or 0,
            "away_score": f.away_score or 0,
            "remaining_minutes": 90 - fixture_clock(state, f),
            "players": [
                {
                    "player_id": pid,
                    "name": state.players[pid].name,
                    "team_id": state.players[pid].team_id,
                    "position": state.players[pid].position.value,
                    "minutes": state.fixture_minutes.get((pid, f.id), 0),
                    "maximum_final_minutes": state.fixture_minutes.get((pid, f.id), 0)
                    + 90
                    - fixture_clock(state, f),
                    "maximum_substitute_minutes": 0
                    if state.fixture_minutes.get((pid, f.id), 0)
                    else 90 - fixture_clock(state, f),
                }
                for pid in sorted(owned)
                if f.involves(state.players[pid].team_id)
            ],
        }
        for f in state.fixtures
        if not f.finished and not state.event_final
    ]


def condition_payload(state: LeagueState, c: Condition) -> dict[str, Any]:
    f = next(f for f in state.fixtures if f.id == c.fixture_id)
    who = (
        state.players[c.player_id].name
        if c.player_id
        else f.team_name(c.team_id)
        if c.team_id
        else ""
    )
    labels = {
        "goal": f"{who} scores {c.value} further goal(s)",
        "assist": f"{who} adds {c.value} assist(s)",
        "blank": f"{who} adds no goals or assists (appearance/other points still count)",
        "dnp": f"{who} does not appear in this fixture",
        "minutes": f"{who} finishes with {c.value} fixture minutes",
        "substitute": f"{who} comes on for the final {c.value} minutes",
        "clean_sheet": f"{who} keep a team clean sheet",
        "lose_clean_sheet": f"{who} concede at least once",
        "score": f"Final score {f.home_team_name} {c.home_goals}–{c.away_goals} {f.away_team_name}",
    }
    return {
        "fixture_id": c.fixture_id,
        "kind": c.kind,
        "player_id": c.player_id,
        "team_id": c.team_id,
        "value": c.value,
        "home_goals": c.home_goals,
        "away_goals": c.away_goals,
        "label": labels[c.kind],
    }


def projection_payload(projection: PlayerFixtureProjection) -> dict[str, Any]:
    payload = asdict(projection)
    payload["data_timestamp"] = projection.data_timestamp.isoformat()
    return payload
