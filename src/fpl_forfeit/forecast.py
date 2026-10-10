"""Shared-fixture sampling for projections, last-place estimates and conditional what-ifs.

This is a transparent initial model, not a calibrated commercial forecast. Domain
inputs are parsed in api.py; no network calls or raw API parsing belong here.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from statistics import fmean, median
from typing import Any

from .models import ElementScore, Fixture, LeagueState, Player, Position
from .scoring import event_points, final_manager_scores, last_ids
from .state import current_standings
from .substitutions import effective_multipliers

MODEL_VERSION = "shared-fixtures-v1"
DEFAULT_SEED = 20261010
OVERVIEW_SAMPLES = 96
DEFAULT_SAMPLES = 1600

ASSUMPTIONS = [
    "Experimental internal model; probabilities have not yet been calibrated against saved live Gameweeks.",
    "Team goals use independent Poisson counts with observed season scoring/conceding rates shrunk by eight matches; no xG or paid data.",
    "Player goal/assist shares use observed season totals shrunk by 450 minutes to position priors; unowned teammates can score and assist.",
    "Starts/minutes and official availability inform appearances. Missing history uses explicit position/appearance priors, not verified lineups.",
    "Events share a scoreline and on-field windows. Goals conceded after an ordinary substitution do not remove earned clean sheets; red cards remain exposed.",
    "Bonus is a shared 3/2/1 performance approximation, not a reproduction of Opta BPS. Defensive actions and saves are Poisson approximations.",
    "Future penalties, own goals and red cards are omitted; already awarded events are preserved. Yellow cards use observed rates with a prior.",
    "Live substitution times are absent from the public feed: players whose minutes trail the observed match clock are held at their current minutes. This can understate live substitute involvement.",
    "Score intervals describe sampled football outcomes. Probability intervals describe sampling error only, and exclude model/calibration error.",
    "A zero sampled chance is never a safety proof. Safety uses the separate configured total remaining player-contribution envelope, not a theoretical football bound.",
]


class ScenarioConflict(ValueError):
    """Hypothetical events cannot coexist with each other or the saved live state."""


@dataclass(frozen=True, slots=True)
class Condition:
    fixture_id: int
    kind: str
    player_id: int | None = None
    team_id: int | None = None
    value: int = 1
    home_goals: int | None = None
    away_goals: int | None = None


@dataclass(frozen=True, slots=True)
class PlayerRates:
    start: float
    goal: float
    assist: float
    defensive: float
    saves: float
    yellow: float


@dataclass(slots=True)
class FixtureSample:
    fixture_id: int
    home_goals: int
    away_goals: int
    stats: dict[int, dict[str, int]]
    # New goals only: (team, scorer, assister, minute); None means unowned.
    events: list[tuple[int, int | None, int | None, int]]
    windows: dict[int, tuple[int, int]]


def poisson(rng: random.Random, rate: float) -> int:
    threshold = math.exp(-max(0, rate))
    product, count = 1.0, 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return max(0, count - 1)


def _rates(player: Player) -> PlayerRates:
    # Explicit priors only when season evidence is sparse/missing.
    goal = {
        Position.GOALKEEPER: 0.003,
        Position.DEFENDER: 0.06,
        Position.MIDFIELDER: 0.20,
        Position.FORWARD: 0.35,
    }[player.position]
    assist = {
        Position.GOALKEEPER: 0.005,
        Position.DEFENDER: 0.07,
        Position.MIDFIELDER: 0.15,
        Position.FORWARD: 0.10,
    }[player.position]
    minutes = player.season_minutes or 0

    def rate(total: int | None, prior: float) -> float:
        return prior if total is None else (total + 5 * prior) / (minutes / 90 + 5)

    start = 0.8
    if player.season_team_matches is not None and player.season_starts is not None:
        start = (player.season_starts + 4 * 0.8) / (player.season_team_matches + 4)
    if player.availability_status in {"i", "s", "u", "n"}:
        start *= (player.chance_of_playing or 0) / 100
    elif player.chance_of_playing is not None:
        start *= player.chance_of_playing / 100
    return PlayerRates(
        min(0.98, max(0, start)),
        rate(player.season_goals, goal),
        rate(player.season_assists, assist),
        rate(
            player.season_defensive_contribution, 7 if player.position == Position.DEFENDER else 5
        ),
        rate(player.season_saves, 3),
        rate(player.season_yellow_cards, 0.14),
    )


class FixtureModel:
    def __init__(self, state: LeagueState, conditions: tuple[Condition, ...] = ()) -> None:
        self.state = state
        self.owned = {p.player_id for m in state.managers for p in m.picks}
        self.players = {pid: state.players[pid] for pid in self.owned}
        self.rates = {pid: _rates(p) for pid, p in state.players.items()}
        self.fixtures = tuple(f for f in state.fixtures if not f.finished and not state.event_final)
        self.by_fixture = {f.id: f for f in self.fixtures}
        self.rules: dict[int, dict[tuple[str, int], Condition]] = defaultdict(dict)
        self.conditions = conditions
        for c in conditions:
            self._add(c)
        self._validate()
        self.clock = {f.id: self._clock(f) for f in self.fixtures}
        self.team_players = {
            (f.id, team): tuple(p for p in self.players.values() if p.team_id == team)
            for f in self.fixtures
            for team in (f.home_team_id, f.away_team_id)
        }
        # All roster players contribute to the anonymous allocation weight.
        self.unowned_weights = {
            (f.id, team, kind): sum(
                getattr(self.rates[p.id], kind) * self.rates[p.id].start
                for p in state.players.values()
                if p.team_id == team and p.id not in self.owned
            )
            for f in self.fixtures
            for team in (f.home_team_id, f.away_team_id)
            for kind in ("goal", "assist")
        }

    def _clock(self, f: Fixture) -> int:
        if not f.started:
            return 0
        observed = max(
            (
                self.state.fixture_minutes.get((p.id, f.id), 0)
                for p in self.state.players.values()
                if f.involves(p.team_id)
            ),
            default=0,
        )
        return min(90, max(f.elapsed_minutes or 0, observed))

    def _add(self, c: Condition) -> None:
        f = self.by_fixture.get(c.fixture_id)
        if f is None:
            raise ScenarioConflict(
                "Choose an unfinished fixture; completed events cannot be changed."
            )
        player_kinds = {"goal", "assist", "blank", "dnp", "minutes", "substitute"}
        if c.kind in player_kinds:
            p = self.players.get(c.player_id or -1)
            if p is None or not f.involves(p.team_id):
                raise ScenarioConflict("Choose a league-owned player involved in that fixture.")
            target = p.id
        elif c.kind in {"clean_sheet", "lose_clean_sheet"}:
            if c.team_id not in (f.home_team_id, f.away_team_id):
                raise ScenarioConflict("Choose a team in that fixture.")
            target = c.team_id
        elif c.kind == "score":
            target = 0
        else:
            raise ScenarioConflict("Unknown football condition.")
        key = (c.kind, target)
        if key in self.rules[f.id]:
            raise ScenarioConflict(
                "This condition is already set. Remove it before changing its value."
            )
        if c.kind in {"goal", "assist"} and not 1 <= c.value <= 6:
            raise ScenarioConflict("Choose between one and six further goals or assists.")
        if c.kind in {"minutes", "substitute"} and not 1 <= c.value <= 90:
            raise ScenarioConflict("Final fixture minutes must be between 1 and 90.")
        if c.kind == "score" and (
            c.home_goals is None
            or c.away_goals is None
            or not 0 <= c.home_goals <= 15
            or not 0 <= c.away_goals <= 15
        ):
            raise ScenarioConflict("Choose a final score between 0 and 15 goals per side.")
        self.rules[f.id][key] = c

    def _validate(self) -> None:
        for f in self.fixtures:
            rules = self.rules[f.id]
            clock = self._clock(f)
            for p in self.players.values():
                if not f.involves(p.team_id):
                    continue
                old = self.state.fixture_stats.get((p.id, f.id), {})
                minutes = self.state.fixture_minutes.get((p.id, f.id), 0)
                if ("dnp", p.id) in rules and (
                    minutes
                    or any(
                        (kind, p.id) in rules
                        for kind in ("goal", "assist", "minutes", "substitute")
                    )
                ):
                    raise ScenarioConflict(f"{p.name} cannot both participate and not appear.")
                if ("blank", p.id) in rules and any(
                    (kind, p.id) in rules for kind in ("goal", "assist")
                ):
                    raise ScenarioConflict(f"{p.name} cannot blank and add an attacking return.")
                forced = rules.get(("minutes", p.id))
                if forced and (forced.value < minutes or forced.value > minutes + 90 - clock):
                    raise ScenarioConflict(
                        f"There is insufficient time for {p.name}'s selected minutes."
                    )
                cameo = rules.get(("substitute", p.id))
                if cameo and (minutes or ("minutes", p.id) in rules or cameo.value > 90 - clock):
                    raise ScenarioConflict(
                        f"{p.name}'s substitute appearance conflicts with current or selected minutes."
                    )
                if f.started and any(
                    (kind, p.id) in rules for kind in ("goal", "assist", "minutes", "substitute")
                ):
                    if old.get("red_cards") or (minutes and minutes < clock - 2):
                        raise ScenarioConflict(
                            f"{p.name} is sent off or has no confirmed future involvement in this feed."
                        )
                    if clock >= 90:
                        raise ScenarioConflict("No regulation time remains for additional events.")
                if (
                    f.started
                    and (p.id, f.id) not in self.state.fixture_stats
                    and any(
                        (kind, p.id) in rules
                        for kind in ("goal", "assist", "minutes", "dnp", "substitute")
                    )
                ):
                    raise ScenarioConflict(
                        "Per-fixture live statistics are unavailable for this player. Refresh before applying events."
                    )
            score = rules.get(("score", 0))
            for team, current, fixed in (
                (f.home_team_id, f.home_score or 0, score.home_goals if score else None),
                (f.away_team_id, f.away_score or 0, score.away_goals if score else None),
            ):
                opponent = f.opponent_of(team)
                cs = ("clean_sheet", opponent) in rules
                lose = ("lose_clean_sheet", opponent) in rules
                goals = sum(
                    c.value
                    for (kind, pid), c in rules.items()
                    if kind == "goal" and self.players[pid].team_id == team
                )
                assists = sum(
                    c.value
                    for (kind, pid), c in rules.items()
                    if kind == "assist" and self.players[pid].team_id == team
                )
                if cs and (
                    current or goals or assists or lose or (fixed is not None and fixed > 0)
                ):
                    raise ScenarioConflict("A clean sheet conflicts with goals for the opposition.")
                if fixed is not None and (
                    fixed < current + max(goals, assists) or (lose and fixed == 0)
                ):
                    raise ScenarioConflict(
                        "The final score cannot erase existing goals or omit selected attacking events."
                    )
                required = {
                    pid
                    for (kind, pid) in rules
                    if kind in {"goal", "assist", "minutes", "substitute"}
                    and self.players[pid].team_id == team
                }
                if (
                    len(required) > 11
                    or sum(self.players[pid].position == Position.GOALKEEPER for pid in required)
                    > 1
                ):
                    raise ScenarioConflict(
                        "Selected players cannot all participate in the modelled lineup."
                    )
            if sum(kind == "substitute" for kind, _ in rules) > 5:
                raise ScenarioConflict(
                    "This model allows at most five specified substitute appearances per fixture."
                )

    def _windows(self, f: Fixture, team: int, rng: random.Random) -> dict[int, tuple[int, int]]:
        rules, clock = self.rules[f.id], self.clock[f.id]
        windows: dict[int, tuple[int, int]] = {}
        starters: list[Player] = []
        reserves: list[Player] = []
        required = {
            pid
            for (kind, pid) in rules
            if kind in {"goal", "assist", "minutes", "substitute"}
            and self.players[pid].team_id == team
        }
        for p in self.team_players[(f.id, team)]:
            old = self.state.fixture_stats.get((p.id, f.id), {})
            minutes = self.state.fixture_minutes.get((p.id, f.id), 0)
            if ("dnp", p.id) in rules:
                windows[p.id] = (90, 90)
                continue
            forced = rules.get(("minutes", p.id))
            cameo = rules.get(("substitute", p.id))
            if f.started:
                # Missing breakdown: freeze this player's awarded total, explicitly reported.
                if (p.id, f.id) not in self.state.fixture_stats:
                    windows[p.id] = (clock, clock)
                    continue
                start = max(0, clock - minutes)
                end = clock if old.get("red_cards") or (minutes and minutes < clock - 2) else 90
                if minutes == 0 and not old.get("red_cards"):
                    end = (
                        90
                        if p.id in required or rng.random() < 0.18 * self.rates[p.id].start
                        else clock
                    )
                    start = (
                        min(89, clock + rng.randint(1, max(1, 90 - clock)))
                        if end > clock
                        else clock
                    )
                elif end > clock and p.id not in required and rng.random() < 0.2:
                    end = rng.randint(clock, 90)
                if forced:
                    end = start + forced.value if minutes else 90
                    start = start if minutes else 90 - forced.value
                if cameo:
                    start, end = 90 - cameo.value, 90
                windows[p.id] = (start, end)
            elif p.id in required or rng.random() < self.rates[p.id].start:
                starters.append(p)
            else:
                reserves.append(p)
        if not f.started:
            starters.sort(key=lambda p: (p.id not in required, -self.rates[p.id].start, p.id))
            keepers = [p for p in starters if p.position == Position.GOALKEEPER]
            reserves.extend(keepers[1:])
            starters = [p for p in starters if p.position != Position.GOALKEEPER] + keepers[:1]
            starters.sort(key=lambda p: (p.id not in required, -self.rates[p.id].start, p.id))
            reserves.extend(starters[11:])
            starters = starters[:11]
            for p in starters:
                forced = rules.get(("minutes", p.id))
                end = forced.value if forced else rng.choice((90, 90, 90, 80, 70, 60, 55))
                cameo = rules.get(("substitute", p.id))
                windows[p.id] = (90 - cameo.value, 90) if cameo else (0, end)
            # Anonymous teammates occupy the remaining eleven slots. A reserve replaces
            # an anonymous teammate or an owned player whose window has already ended.
            substitutes = 0
            for p in reserves:
                if substitutes < 5 and rng.random() < 0.25 * self.rates[p.id].start:
                    start = rng.randint(65, 89)
                    active = [pid for pid, (a, b) in windows.items() if a <= start < b]
                    keeper_active = any(
                        self.players[pid].position == Position.GOALKEEPER for pid in active
                    )
                    if len(active) < 11 and not (
                        p.position == Position.GOALKEEPER and keeper_active
                    ):
                        windows[p.id] = (start, 90)
                        substitutes += 1
                        continue
                windows[p.id] = (90, 90)
        # Resolve uncertain live windows conservatively so at most eleven overlap.
        # Occupancy changes only at a window boundary; checking every minute would
        # repeat the same lineup up to ninety times in every fixture/sample.
        boundaries = sorted({clock} | {t for window in windows.values() for t in window})
        for minute in boundaries:
            if minute < clock or minute >= 90:
                continue
            active = [pid for pid, (a, b) in windows.items() if a <= minute < b]
            active.sort(key=lambda pid: (pid not in required, -self.rates[pid].start, pid))
            retained: list[int] = []
            for pid in active:
                duplicate_gk = self.players[pid].position == Position.GOALKEEPER and any(
                    self.players[k].position == Position.GOALKEEPER for k in retained
                )
                if len(retained) >= 11 or duplicate_gk:
                    if pid in required:
                        raise ScenarioConflict("Selected minutes cannot coexist in this lineup.")
                    a, _ = windows[pid]
                    windows[pid] = (a, max(a, minute))
                else:
                    retained.append(pid)
        return windows

    def _goal_rate(self, team: int, opponent: int, home: bool) -> float:
        own = self.state.team_goal_rates.get(team, (1.4, 1.4))[0]
        against = self.state.team_goal_rates.get(opponent, (1.4, 1.4))[1]
        return min(4.0, max(0.25, (own + against) / 2 * (1.1 if home else 0.9)))

    def _allocate(
        self,
        f: Fixture,
        team: int,
        kind: str,
        minute: int,
        windows: dict[int, tuple[int, int]],
        rng: random.Random,
        forbidden: set[int],
        scorer: int | None = None,
    ) -> int | None:
        ids = [
            p.id
            for p in self.team_players[(f.id, team)]
            if p.id not in forbidden
            and p.id != scorer
            and windows[p.id][0] <= minute < windows[p.id][1]
        ]
        weights = [getattr(self.rates[pid], kind) for pid in ids]
        # In synthetic/sparse rosters missing players still account for goals/assists.
        anonymous = max(
            0.05,
            self.unowned_weights[(f.id, team, kind)],
            (11 - len(ids)) * (0.10 if kind == "goal" else 0.07),
        )
        return rng.choices(ids + [None], weights=weights + [anonymous], k=1)[0]

    def sample_fixture(self, f: Fixture, rng: random.Random) -> FixtureSample:
        rules, clock = self.rules[f.id], self.clock[f.id]
        windows = self._windows(f, f.home_team_id, rng) | self._windows(f, f.away_team_id, rng)
        events: list[tuple[int, int | None, int | None, int]] = []
        totals: list[int] = []
        for team, opponent, current, home in (
            (f.home_team_id, f.away_team_id, f.home_score or 0, True),
            (f.away_team_id, f.home_team_id, f.away_score or 0, False),
        ):
            forced_goals = [
                pid
                for (kind, pid), c in rules.items()
                if kind == "goal" and self.players[pid].team_id == team
                for _ in range(c.value)
            ]
            forced_assists = [
                pid
                for (kind, pid), c in rules.items()
                if kind == "assist" and self.players[pid].team_id == team
                for _ in range(c.value)
            ]
            # Pair returns with distinct teammates when possible; an anonymous teammate
            # can always supply a goal/assist when the only selected player is excluded.
            pairs: list[tuple[int | None, int | None]] = []
            for scorer in forced_goals:
                assist = next(
                    (
                        pid
                        for pid in forced_assists
                        if pid != scorer
                        and max(windows[pid][0], windows[scorer][0], clock)
                        < min(windows[pid][1], windows[scorer][1])
                    ),
                    None,
                )
                if assist is not None:
                    forced_assists.remove(assist)
                pairs.append((scorer, assist))
            pairs.extend((None, pid) for pid in forced_assists)
            score = rules.get(("score", 0))
            fixed = (score.home_goals if home else score.away_goals) if score else None
            cs = ("clean_sheet", opponent) in rules
            minimum = max(len(pairs), int(("lose_clean_sheet", opponent) in rules and current == 0))
            if fixed is not None and fixed - current < minimum:
                raise ScenarioConflict(
                    "This score needs more goals to support distinct goalscorer/assist events."
                )
            count = (
                fixed - current
                if fixed is not None
                else 0
                if cs
                else max(
                    minimum, poisson(rng, self._goal_rate(team, opponent, home) * (90 - clock) / 90)
                )
            )
            if clock >= 90 and count:
                raise ScenarioConflict("No time remains to change this score.")
            blank = {pid for (kind, pid) in rules if kind == "blank"}
            exact_goals = {pid for (kind, pid) in rules if kind == "goal"}
            exact_assists = {pid for (kind, pid) in rules if kind == "assist"}
            for i in range(count):
                scorer, assister = pairs[i] if i < len(pairs) else (None, None)
                required = [pid for pid in (scorer, assister) if pid is not None]
                start = max([clock] + [windows[pid][0] for pid in required])
                end = min([90] + [windows[pid][1] for pid in required])
                if start >= end:
                    raise ScenarioConflict(
                        "Selected player events have no shared time on the pitch."
                    )
                minute = rng.randrange(start, end)
                if scorer is None:
                    scorer = self._allocate(
                        f, team, "goal", minute, windows, rng, blank | exact_goals, scorer=assister
                    )
                if assister is None and rng.random() < 0.75:
                    assister = self._allocate(
                        f,
                        team,
                        "assist",
                        minute,
                        windows,
                        rng,
                        blank | exact_assists,
                        scorer=scorer,
                    )
                events.append((team, scorer, assister, minute))
            totals.append(current + count)
        stats: dict[int, dict[str, int]] = {}
        for p in self.players.values():
            if not f.involves(p.team_id):
                continue
            old = self.state.fixture_stats.get((p.id, f.id), {})
            if f.started and (p.id, f.id) not in self.state.fixture_stats:
                continue
            a, b = windows[p.id]
            old_minutes = old.get("minutes", self.state.fixture_minutes.get((p.id, f.id), 0))
            minutes = max(old_minutes, b - a)
            value = dict(old)
            value["minutes"] = minutes
            value["goals_scored"] = old.get("goals_scored", 0) + sum(e[1] == p.id for e in events)
            value["assists"] = old.get("assists", 0) + sum(e[2] == p.id for e in events)
            conceded = old.get("goals_conceded", 0) + sum(
                e[0] != p.team_id and (a <= e[3] < b or old.get("red_cards", 0)) for e in events
            )
            value["goals_conceded"] = conceded
            value["clean_sheets"] = int(minutes >= 60 and conceded == 0)
            future = max(0, minutes - old_minutes) / 90
            value["defensive_contribution"] = old.get("defensive_contribution", 0) + poisson(
                rng, self.rates[p.id].defensive * future
            )
            value["saves"] = old.get("saves", 0) + (
                poisson(rng, self.rates[p.id].saves * future)
                if p.position == Position.GOALKEEPER
                else 0
            )
            value["yellow_cards"] = min(
                1, old.get("yellow_cards", 0) + int(rng.random() < self.rates[p.id].yellow * future)
            )
            value["bonus"] = 0
            stats[p.id] = value
        # Approximate BPS competition is shared across the fixture, including unowned
        # competitors. Only participating players can receive modelled bonus.
        merit = [
            (
                pid,
                8 * s.get("goals_scored", 0)
                + 5 * s.get("assists", 0)
                + 4 * s.get("clean_sheets", 0)
                + rng.random() * 8,
            )
            for pid, s in stats.items()
            if s["minutes"] > 0
        ]
        merit.extend((None, rng.random() * 12 + 3) for _ in range(4))
        for bonus, (pid, _) in zip((3, 2, 1), sorted(merit, key=lambda x: -x[1])):
            if pid is not None:
                stats[pid]["bonus"] = bonus
        return FixtureSample(f.id, *totals, stats, events, windows)

    def draw(self, rng: random.Random) -> tuple[dict[int, ElementScore], tuple[FixtureSample, ...]]:
        scores = dict(self.state.live_scores)
        fixtures = tuple(self.sample_fixture(f, rng) for f in self.fixtures)
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
    current = {s.manager.entry_id: s.effective_score for s in current_standings(state)}
    totals: dict[int, list[int]] = {m.entry_id: [] for m in state.managers}
    counted = {m.entry_id: 0 for m in state.managers}
    outright, tied = counted.copy(), counted.copy()
    player_points: dict[int, list[int]] = {pid: [] for pid in model.owned}
    exposures: dict[int, dict[int, float]] = {pid: defaultdict(float) for pid in model.owned}
    bottom_samples: list[tuple[int, ...]] = []
    n = samples if model.fixtures else 1
    for _ in range(n):
        scores, _fixtures = model.draw(rng)
        final = final_manager_scores(state, scores)
        bottom = last_ids(final)
        selected = last_ids(final, allow_tied_last)
        bottom_samples.append(selected)
        for entry, value in final.items():
            totals[entry].append(value)
            counted[entry] += entry in selected
            outright[entry] += entry in bottom and len(bottom) == 1
            tied[entry] += entry in bottom and len(bottom) > 1
        for pid in model.owned:
            player_points[pid].append(scores[pid].points - state.live_scores[pid].points)
        complete = {p.team_id: True for p in state.players.values()}
        for m in state.managers:
            multipliers = effective_multipliers(m, state.players, scores, complete)
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
        "projected_bottom_entry_ids": projected_bottom,
        "projected_last_entry_ids": projected_bottom
        if allow_tied_last or len(projected_bottom) == 1
        else [],
        "decisive_players": _decisive(state, model, rows, player_points, exposures, bottom_samples),
        "important_fixtures": _important_fixtures(state, model, player_points, exposures),
        "assumptions": ASSUMPTIONS,
        "coverage": {
            "remaining_owned_players_using_priors": missing,
            "live_player_breakdowns_missing_and_frozen": live_missing,
            "team_history_available": bool(state.team_goal_rates),
        },
        "conditions": [condition_payload(state, c) for c in conditions],
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
        max_exposure = max(multipliers)
        beneficiaries = [
            names[e] for e in contenders if exposure[pid].get(e, 0) > min(multipliers) + 0.05
        ]
        blank_beneficiaries = [
            names[e] for e in contenders if exposure[pid].get(e, 0) < max_exposure - 0.05
        ]
        result.append(
            {
                "player_id": pid,
                "player_name": p.name,
                "team_name": p.team_name,
                "swing_points": round(swing, 1),
                "expected_points_from_here": round(mean, 2),
                "exposure_spread": round(spread, 2),
                "strong_performance_helps": beneficiaries,
                "blank_helps": blank_beneficiaries,
                "sampled_risk_association": {str(e): round(v, 3) for e, v in sensitivity.items()},
                "association_note": "Observed association within shared-fixture samples; not a causal or necessary condition.",
                "status": "live"
                if any(f.started for f in state.unfinished_fixtures_for_team(p.team_id))
                else "yet_to_play",
                "fixture_ids": [f.id for f in state.unfinished_fixtures_for_team(p.team_id)],
                "explanation": f"{spread:.1f}× effective exposure difference across the bottom contest; "
                f"about {swing:.1f} net points between the sampled 10th/90th percentiles. "
                "Exposure averages include possible autosubs and vice-captain handover.",
            }
        )
    return sorted(result, key=lambda p: -p["swing_points"])[:8]


def _important_fixtures(
    state: LeagueState,
    model: FixtureModel,
    points: dict[int, list[int]],
    exposure: dict[int, dict[int, float]],
) -> list[dict[str, Any]]:
    result = []
    for f in model.fixtures:
        leverage = sum(
            (_quantile(values, 0.9) - _quantile(values, 0.1))
            * (
                max(exposure[pid].values(), default=0)
                - min((exposure[pid].get(m.entry_id, 0) for m in state.managers), default=0)
            )
            for pid, values in points.items()
            if f.involves(state.players[pid].team_id)
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
            "players": [
                {
                    "player_id": pid,
                    "name": state.players[pid].name,
                    "team_id": state.players[pid].team_id,
                    "minutes": state.fixture_minutes.get((pid, f.id), 0),
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
