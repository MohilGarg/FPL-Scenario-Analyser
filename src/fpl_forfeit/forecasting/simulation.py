"""Shared football outcomes, with no league, manager, scoring-multiplier or HTTP logic."""

from __future__ import annotations

import random
from collections import defaultdict

from ..models import Fixture, Player, Position
from .models import Condition, FixtureSample, ForecastInputs, ScenarioConflict, canonical_conditions
from .statistics import player_rates, poisson, team_goal_rate


def observed_minutes(inputs: ForecastInputs, player_id: int, fixture_id: int) -> int:
    key = (player_id, fixture_id)
    return inputs.fixture_stats.get(key, {}).get("minutes", inputs.fixture_minutes.get(key, 0))


def fixture_clock(state: ForecastInputs, fixture: Fixture) -> int:
    if not fixture.started:
        return 0
    observed = max(
        (
            observed_minutes(state, pid, fid)
            for pid, fid in set(state.fixture_minutes) | set(state.fixture_stats)
            if fid == fixture.id
            and pid in state.players
            and fixture.involves(state.players[pid].team_id)
        ),
        default=0,
    )
    return min(90, max(fixture.elapsed_minutes or 0, observed))


class FixtureSimulator:
    def __init__(
        self,
        state: ForecastInputs,
        conditions: tuple[Condition, ...] = (),
        *,
        player_ids: tuple[int, ...] | None = None,
    ) -> None:
        self.inputs = state
        self.player_ids = set(state.players if player_ids is None else player_ids)
        if not self.player_ids <= state.players.keys():
            raise ValueError("Selected player IDs are absent from forecast inputs.")
        if len({f.id for f in state.fixtures}) != len(state.fixtures):
            raise ValueError("Fixture IDs must be unique across the forecast horizon.")
        self.players = {pid: state.players[pid] for pid in sorted(self.player_ids)}
        self.rates = {pid: player_rates(p) for pid, p in state.players.items()}
        self.fixtures = tuple(
            sorted(
                (f for f in state.fixtures if not f.finished),
                key=lambda f: (f.gameweek, f.id),
            )
        )
        self.by_fixture = {f.id: f for f in self.fixtures}
        self.rules: dict[int, dict[tuple[str, int], Condition]] = defaultdict(dict)
        self.conditions = canonical_conditions(conditions)
        for c in self.conditions:
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
                if p.team_id == team and p.id not in self.player_ids
            )
            for f in self.fixtures
            for team in (f.home_team_id, f.away_team_id)
            for kind in ("goal", "assist")
        }
        self.unowned_starts = {
            (f.id, team): sum(
                self.rates[p.id].start
                for p in state.players.values()
                if p.team_id == team and p.id not in self.player_ids
            )
            for f in self.fixtures
            for team in (f.home_team_id, f.away_team_id)
        }

    def _clock(self, f: Fixture) -> int:
        return fixture_clock(self.inputs, f)

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
                raise ScenarioConflict("Choose a selected player involved in that fixture.")
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
                old = self.inputs.fixture_stats.get((p.id, f.id), {})
                minutes = observed_minutes(self.inputs, p.id, f.id)
                if ("dnp", p.id) in rules and (
                    minutes
                    or old.get("yellow_cards")
                    or old.get("red_cards")
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
                    confirmed_future = forced and forced.value > minutes
                    if old.get("red_cards") or (minutes and minutes < clock - 2 and not forced):
                        raise ScenarioConflict(
                            f"{p.name} is sent off or has no confirmed future involvement in this feed. "
                            "For an active substitute, set final fixture minutes explicitly."
                        )
                    if any((kind, p.id) in rules for kind in ("goal", "assist")) and (
                        minutes and minutes < clock - 2 and not confirmed_future
                    ):
                        raise ScenarioConflict(
                            f"{p.name} needs some selected future minutes to add a return."
                        )
                    if clock >= 90:
                        raise ScenarioConflict("No regulation time remains for additional events.")
                if (
                    f.started
                    and (p.id, f.id) not in self.inputs.fixture_stats
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
                potential = {
                    p.id: self._potential_window(f, p.id)
                    for p in self.players.values()
                    if p.team_id == team
                }
                needed = len(self._return_pairs(f, team, potential))
                if fixed is not None and fixed - current < needed:
                    raise ScenarioConflict(
                        "The selected score needs more goals to allow distinct scorers and "
                        "assisters with overlapping playing minutes."
                    )
                required = {
                    pid
                    for (kind, pid) in rules
                    if kind in {"goal", "assist", "minutes", "substitute"}
                    and self.players[pid].team_id == team
                }
                for t in {a for a, _ in potential.values()}:
                    active = [pid for pid in required if potential[pid][0] <= t < potential[pid][1]]
                    if (
                        len(active) > 11
                        or sum(self.players[pid].position == Position.GOALKEEPER for pid in active)
                        > 1
                    ):
                        raise ScenarioConflict(
                            "Selected players cannot coexist in the modelled lineup."
                        )
                if (
                    sum(
                        kind == "substitute" and self.players[pid].team_id == team
                        for kind, pid in rules
                    )
                    > 5
                ):
                    raise ScenarioConflict(
                        "Use at most five specified substitute appearances per team."
                    )

    def _potential_window(self, f: Fixture, pid: int) -> tuple[int, int]:
        """Widest permitted window for deterministic condition validation."""
        rules, clock = self.rules[f.id], self._clock(f)
        minutes = observed_minutes(self.inputs, pid, f.id)
        cameo = rules.get(("substitute", pid))
        forced = rules.get(("minutes", pid))
        if cameo:
            return 90 - cameo.value, 90
        if forced:
            start = max(0, clock - minutes) if minutes else (90 - forced.value if f.started else 0)
            return start, start + forced.value
        return (max(0, clock - minutes) if minutes else clock), 90

    def _return_pairs(
        self,
        f: Fixture,
        team: int,
        windows: dict[int, tuple[int, int]],
    ) -> list[tuple[int | None, int | None]]:
        goals = [
            pid
            for (kind, pid), c in self.rules[f.id].items()
            if kind == "goal" and self.players[pid].team_id == team
            for _ in range(c.value)
        ]
        assists = [
            pid
            for (kind, pid), c in self.rules[f.id].items()
            if kind == "assist" and self.players[pid].team_id == team
            for _ in range(c.value)
        ]
        matched: dict[int, int] = {}
        clock = self._clock(f)

        def augment(g: int, visited: set[int]) -> bool:
            scorer = goals[g]
            for a, assister in enumerate(assists):
                if (
                    a in visited
                    or scorer == assister
                    or max(clock, windows[scorer][0], windows[assister][0])
                    >= min(windows[scorer][1], windows[assister][1])
                ):
                    continue
                visited.add(a)
                if a not in matched or augment(matched[a], visited):
                    matched[a] = g
                    return True
            return False

        for g in range(len(goals)):
            augment(g, set())
        goal_matches = {g: a for a, g in matched.items()}
        return [
            (pid, assists[goal_matches[g]] if g in goal_matches else None)
            for g, pid in enumerate(goals)
        ] + [(None, pid) for a, pid in enumerate(assists) if a not in matched]

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
            old = self.inputs.fixture_stats.get((p.id, f.id), {})
            minutes = observed_minutes(self.inputs, p.id, f.id)
            if ("dnp", p.id) in rules:
                windows[p.id] = (90, 90)
                continue
            forced = rules.get(("minutes", p.id))
            cameo = rules.get(("substitute", p.id))
            if f.started:
                # Missing breakdown: freeze this player's awarded total, explicitly reported.
                if (p.id, f.id) not in self.inputs.fixture_stats:
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
            elif cameo:
                windows[p.id] = (90 - cameo.value, 90)
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
                choices = self.rates[p.id].starting_minutes
                if any((kind, p.id) in rules for kind in ("goal", "assist")):
                    latest_start = max(
                        (
                            self._potential_window(f, pid)[0]
                            for (kind, pid) in rules
                            if kind in {"goal", "assist"} and self.players[pid].team_id == team
                        ),
                        default=0,
                    )
                    choices = tuple(t for t in choices if t > latest_start) or (90,)
                end = forced.value if forced else rng.choice(choices)
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
        return team_goal_rate(self.inputs.team_goal_rates, team, opponent, home)

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
        active = sum(
            a <= minute < b for pid, (a, b) in windows.items() if self.players[pid].team_id == team
        )
        sent_off = sum(
            bool(self.inputs.fixture_stats.get((p.id, f.id), {}).get("red_cards"))
            for p in self.team_players[(f.id, team)]
        )
        slots = max(0, 11 - sent_off - active)
        anonymous = max(
            self.unowned_weights[(f.id, team, kind)]
            * min(1, slots / max(1, self.unowned_starts[(f.id, team)])),
            slots * (0.10 if kind == "goal" else 0.07),
        )
        if not ids and not anonymous:
            if kind == "assist":
                return None
            raise ScenarioConflict(
                "No eligible goalscorer is on the pitch for the selected events."
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
            pairs = self._return_pairs(f, team, windows)
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
            old = self.inputs.fixture_stats.get((p.id, f.id), {})
            if f.started and (p.id, f.id) not in self.inputs.fixture_stats:
                continue
            a, b = windows[p.id]
            old_minutes = old.get("minutes", self.inputs.fixture_minutes.get((p.id, f.id), 0))
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
        anonymous_competitors = max(0, 22 - len(merit))
        merit.extend((None, rng.random() * 12 + 3) for _ in range(anonymous_competitors))
        for bonus, (pid, _) in zip((3, 2, 1), sorted(merit, key=lambda x: -x[1])):
            if pid is not None:
                stats[pid]["bonus"] = bonus
        return FixtureSample(f.id, *totals, stats, events, windows)

    def sample(self, rng: random.Random) -> tuple[FixtureSample, ...]:
        """Draw each unfinished fixture once; consumers share its football outcomes."""
        return tuple(self.sample_fixture(f, rng) for f in self.fixtures)
