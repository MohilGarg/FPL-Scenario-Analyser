"""Public event associations and checkpoints from existing joint football draws."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict
from datetime import timedelta
from statistics import fmean, pstdev
from typing import Any

from .forecasting.models import Condition, FixtureSample
from .models import ElementScore, Fixture, LeagueState
from .points import event_points
from .scoring import last_ids
from .substitutions import effective_multipliers


def fixture_groups(fixtures: tuple[Fixture, ...]) -> tuple[tuple[Fixture, ...], ...]:
    """Group simultaneous/overlapping two-hour match windows; never invent missing times."""
    if not fixtures:
        return ()
    if any(f.kickoff_time is None for f in fixtures):
        return (tuple(sorted(fixtures, key=lambda f: f.id)),)
    groups: list[list[Fixture]] = []
    end = None
    for fixture in sorted(fixtures, key=lambda f: (f.kickoff_time, f.id)):
        if end is None or fixture.kickoff_time >= end:
            groups.append([])
        groups[-1].append(fixture)
        end = max(end or fixture.kickoff_time, fixture.kickoff_time + timedelta(hours=2))
    return tuple(tuple(group) for group in groups)


def _interval(values: list[int]) -> list[int]:
    values = sorted(values)
    return [values[int((len(values) - 1) * 0.1)], values[int((len(values) - 1) * 0.9)]]


class LiveIntelligence:
    def __init__(
        self, state: LeagueState, fixtures: tuple[Fixture, ...], current: dict[int, int], ties: bool
    ) -> None:
        self.state, self.fixtures, self.current, self.ties = state, fixtures, current, ties
        self.groups = fixture_groups(fixtures)
        self.contenders = tuple(sorted(current, key=lambda e: (current[e], e))[:8])
        self.managers = {m.entry_id: m for m in state.managers}
        self.owners: dict[int, list[int]] = defaultdict(list)
        weights: dict[int, dict[int, float]] = defaultdict(dict)
        for m in state.managers:
            for p in m.picks:
                self.owners[p.player_id].append(m.entry_id)
                weights[p.player_id][m.entry_id] = (
                    (3 if m.active_chip == "3xc" else 2)
                    if p.is_captain
                    else 1
                    if p.is_starter or m.active_chip == "bboost"
                    else 0.25
                )
        ranked = sorted(
            weights,
            key=lambda pid: (
                -(
                    max(weights[pid].get(e, 0) for e in self.contenders)
                    - min(weights[pid].get(e, 0) for e in self.contenders)
                ),
                pid,
            ),
        )
        candidates = []
        for pid in ranked[:12]:
            spread = max(weights[pid].get(e, 0) for e in self.contenders) - min(
                weights[pid].get(e, 0) for e in self.contenders
            )
            if spread < 0.1:
                continue
            p = state.players[pid]
            for f in fixtures:
                if not f.involves(p.team_id) or (f.elapsed_minutes or 0) >= 90:
                    continue
                old = state.fixture_stats.get((pid, f.id), {})
                if f.started and (pid, f.id) not in state.fixture_stats:
                    continue
                candidates.extend(
                    (
                        Condition(f.id, "goal", player_id=pid),
                        Condition(f.id, "blank", player_id=pid),
                    )
                )
                if (
                    not old.get("minutes")
                    and not old.get("yellow_cards")
                    and not old.get("red_cards")
                ):
                    candidates.append(Condition(f.id, "substitute", player_id=pid, value=15))
        for f in fixtures:
            if (f.elapsed_minutes or 0) >= 90:
                continue
            for team in (f.home_team_id, f.away_team_id):
                if not (f.goals_against(team) or 0) and any(
                    state.players[pid].team_id == team for pid in ranked[:12]
                ):
                    candidates.append(Condition(f.id, "clean_sheet", team_id=team))
        clean_sheets = [c for c in candidates if c.kind == "clean_sheet"]
        player_events = [c for c in candidates if c.kind != "clean_sheet"]
        self.candidates = tuple((clean_sheets[:8] + player_events)[:32])
        self.masks = {c: [] for c in self.candidates}
        self.fixture_totals = {f.id: {e: [] for e in current} for f in fixtures}
        self.checkpoints = [{e: [] for e in current} for _ in self.groups]
        self.checkpoint_bottom = [{e: 0 for e in current} for _ in self.groups]
        self.residuals = [{e: [] for e in current} for _ in self.groups]
        self.multiplier_cache = [{e: {} for e in current} for _ in self.groups]
        completed = {f.id for f in state.fixtures if f.finished}
        self.completed_teams = []
        for group in self.groups:
            completed.update(f.id for f in group)
            self.completed_teams.append(
                {
                    team: all(f.id in completed for f in state.fixtures if f.involves(team))
                    for team in {p.team_id for p in state.players.values()}
                }
            )
        self.timeline_samples = 0

    def add(
        self,
        fixtures: tuple[FixtureSample, ...],
        final: dict[int, int],
        resolved: dict[int, dict[int, int]],
    ) -> None:
        by_id = {f.fixture_id: f for f in fixtures}
        for condition in self.candidates:
            sample = by_id[condition.fixture_id]
            stats = sample.stats.get(condition.player_id)
            old = self.state.fixture_stats.get((condition.player_id, condition.fixture_id), {})
            if condition.kind == "clean_sheet":
                fixture = next(f for f in self.fixtures if f.id == condition.fixture_id)
                matched = (
                    sample.away_goals
                    if condition.team_id == fixture.home_team_id
                    else sample.home_goals
                ) == 0
            elif stats is None:
                matched = False
            elif condition.kind == "goal":
                matched = stats.get("goals_scored", 0) > old.get("goals_scored", 0)
            elif condition.kind == "blank":
                matched = stats.get("goals_scored", 0) == old.get("goals_scored", 0) and stats.get(
                    "assists", 0
                ) == old.get("assists", 0)
            else:
                matched = stats.get("minutes", 0) > 0 and sample.windows[condition.player_id][0] > 0
            self.masks[condition].append(matched)
        deltas = {}
        for sample in fixtures:
            contributions = {e: 0 for e in self.current}
            for pid, stats in sample.stats.items():
                old = self.state.fixture_stats.get((pid, sample.fixture_id), {})
                delta = event_points(self.state.players[pid].position, stats) - event_points(
                    self.state.players[pid].position, old
                )
                deltas[(pid, sample.fixture_id)] = (delta, stats)
                for entry in self.owners[pid]:
                    contributions[entry] += delta * resolved[entry].get(pid, 0)
            for entry, value in contributions.items():
                self.fixture_totals[sample.fixture_id][entry].append(value)
        # A labelled 400-draw prefix bounds checkpoint scoring cost for 50 managers.
        if self.timeline_samples >= 400:
            return
        self.timeline_samples += 1
        scores = dict(self.state.live_scores)
        for index, group in enumerate(self.groups):
            if index < len(self.groups) - 1:
                for f in group:
                    for pid, stats in by_id[f.id].stats.items():
                        old = self.state.fixture_stats.get((pid, f.id), {})
                        delta = deltas[(pid, f.id)][0]
                        score = scores[pid]
                        scores[pid] = ElementScore(
                            score.points + delta,
                            score.minutes + stats["minutes"] - old.get("minutes", 0),
                            score.carded
                            or bool(stats.get("yellow_cards") or stats.get("red_cards")),
                        )
            complete = self.completed_teams[index]
            checkpoint = {}
            last_group = index == len(self.groups) - 1
            for entry, m in self.managers.items():
                if last_group:
                    value = final[entry]
                else:
                    key = (
                        sum(1 << i for i, p in enumerate(m.picks) if scores[p.player_id].appeared),
                        sum(1 << i for i, p in enumerate(m.picks) if scores[p.player_id].minutes),
                    )
                    cache = self.multiplier_cache[index][entry]
                    if key not in cache:
                        cache[key] = effective_multipliers(m, self.state.players, scores, complete)
                    value = (
                        sum(scores[pid].points * mult for pid, mult in cache[key].items())
                        - m.transfer_cost
                    )
                checkpoint[entry] = value
                self.checkpoints[index][entry].append(value)
                self.residuals[index][entry].append(final[entry] - value)
            for entry in last_ids(checkpoint, self.ties):
                self.checkpoint_bottom[index][entry] += 1

    def results(
        self, totals: dict[int, list[int]], bottom: list[tuple[int, ...]]
    ) -> dict[str, Any]:
        n = len(bottom)
        if not n or not totals:
            return {"dangerous_events": [], "fixture_forecasts": [], "fixture_timeline": []}
        baseline = {e: sum(e in ids for ids in bottom) / n for e in totals}
        means = {e: fmean(values) for e, values in totals.items()}
        contenders = {
            e for e in totals if baseline[e] >= 0.01 or means[e] <= min(means.values()) + 15
        }
        events = []
        projected = set(last_ids(means, self.ties))
        for c, mask in self.masks.items():
            indices = [i for i, matched in enumerate(mask) if matched]
            count = len(indices)
            if count < 40 or n - count < 40:
                continue
            conditional_means = {
                e: fmean(values[i] for i in indices) for e, values in totals.items()
            }
            impacts = []
            for entry in contenders:
                matches = sum(entry in bottom[i] for i in indices)
                p = matches / count
                other = (baseline[entry] * n - matches) / (n - count)
                delta = p - baseline[entry]
                se = (1 - count / n) * math.sqrt(
                    p * (1 - p) / count + other * (1 - other) / (n - count)
                )
                if abs(delta) < max(0.02, 3 * se):
                    continue
                impacts.append(
                    {
                        "entry_id": entry,
                        "baseline_risk": round(baseline[entry], 4),
                        "event_risk": round(p, 4),
                        "risk_change": round(delta, 4),
                        "sampling_band": [round(delta - 3 * se, 4), round(delta + 3 * se, 4)],
                        "expected_points_swing": round(conditional_means[entry] - means[entry], 2),
                    }
                )
            if not impacts:
                continue
            winner = set(last_ids(conditional_means, self.ties))
            event = {
                "condition": asdict(c),
                "interpretation": "sample_conditional",
                "occurrence_probability": round(count / n, 4),
                "matching_samples": count,
                "samples": n,
                "managers": sorted(impacts, key=lambda r: -abs(r["risk_change"])),
                "benefits_entry_ids": [r["entry_id"] for r in impacts if r["risk_change"] < 0],
                "vulnerable_entry_ids": [r["entry_id"] for r in impacts if r["risk_change"] > 0],
                "projected_loser_changes": winner != projected,
                "projected_last_entry_ids": sorted(winner),
                "uncertainty_note": "Association within shared model draws, not causation or a forced-event forecast. Screened 3-SE sampling bands exclude model error; selected events need independent validation.",
            }
            events.append(event)
        events.sort(key=lambda event: -max(abs(r["risk_change"]) for r in event["managers"]))
        # Keep diverse event cards, rather than three labels for the same player.
        selected, seen = [], set()
        for event in events:
            c = event["condition"]
            identity = (c["fixture_id"], c["player_id"] or -c["team_id"])
            if identity not in seen:
                selected.append(event)
                seen.add(identity)
            if len(selected) == 5:
                break
        fixtures = []
        for f in self.fixtures:
            values = self.fixture_totals[f.id]
            spreads = [
                max(values[e][i] for e in contenders) - min(values[e][i] for e in contenders)
                for i in range(n)
            ]
            association = 0.0
            minimums = [min(values[e][i] for e in contenders) for i in range(n)]
            for entry in contenders:
                relative = [v - minimums[i] for i, v in enumerate(values[entry])]
                sd = pstdev(relative)
                if sd:
                    covariance = fmean(
                        v * (int(entry in bottom[i]) - baseline[entry])
                        for i, v in enumerate(relative)
                    )
                    association = max(association, abs(covariance) / sd)
            fixtures.append(
                {
                    "fixture_id": f.id,
                    "label": f"{f.home_team_name} v {f.away_team_name}",
                    "kickoff_time": f.kickoff_time.isoformat() if f.kickoff_time else None,
                    "live": f.started,
                    "relative_influence": round(fmean(spreads), 2),
                    "relative_swing_interval_80": _interval(spreads),
                    "final_risk_association": round(association, 4),
                    "players": [
                        {
                            "player_id": pid,
                            "name": self.state.players[pid].name,
                            "captained_by_entry_ids": [
                                m.entry_id
                                for m in self.state.managers
                                if any(p.player_id == pid and p.is_captain for p in m.picks)
                            ],
                        }
                        for pid in sorted(self.owners)
                        if f.involves(self.state.players[pid].team_id)
                    ],
                    "managers": [
                        {
                            "entry_id": e,
                            "expected_effective_contribution": round(fmean(values[e]), 2),
                            "contribution_interval_80": _interval(values[e]),
                        }
                        for e in sorted(contenders)
                    ],
                    "note": "Remaining fixture category deltas under sampled final effective multipliers, including uncertain captaincy/autosubs; not a standalone safety proof.",
                }
            )
        rank = sorted(
            fixtures, key=lambda f: (-f["final_risk_association"], -f["relative_influence"])
        )
        for i, f in enumerate(rank):
            f["importance_rank"] = i + 1
        timeline = []
        previous_uncertainty = fmean(pstdev(totals[e]) for e in contenders)
        for index, group in enumerate(self.groups):
            uncertainty = fmean(pstdev(self.residuals[index][e]) for e in contenders)
            timeline.append(
                {
                    "fixture_ids": [f.id for f in group],
                    "samples": self.timeline_samples,
                    "label": " · ".join(f"{f.home_team_name} v {f.away_team_name}" for f in group),
                    "kickoff_time": group[0].kickoff_time.isoformat()
                    if group[0].kickoff_time
                    else None,
                    "timing_unknown": any(f.kickoff_time is None for f in group),
                    "remaining_uncertainty_sd": round(uncertainty, 2),
                    "expected_to_clarify": uncertainty < previous_uncertainty,
                    "managers": [
                        {
                            "entry_id": e,
                            "projected_checkpoint_score": round(
                                fmean(self.checkpoints[index][e]), 2
                            ),
                            "checkpoint_bottom_probability": round(
                                self.checkpoint_bottom[index][e] / self.timeline_samples, 4
                            ),
                            "final_last_probability": round(baseline[e], 4),
                            "remaining_contribution_interval_80": _interval(
                                self.residuals[index][e]
                            ),
                        }
                        for e in sorted(contenders)
                    ],
                    "note": "Chance of being bottom after this group, not ultimate forfeit risk. Pending autosubs/captaincy are resolved only for completed teams. Unknown kickoff times are grouped without ordering.",
                }
            )
            previous_uncertainty = uncertainty
        return {
            "dangerous_events": selected,
            "fixture_forecasts": fixtures,
            "fixture_timeline": timeline,
            "event_screening": {
                "candidate_count": len(self.candidates),
                "minimum_group_samples": 40,
                "minimum_risk_swing": 0.02,
                "sampling_standard_errors": 3,
            },
        }
