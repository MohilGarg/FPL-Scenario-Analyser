"""Exhaustive legal XI, bench and captaincy selection on shared outcome samples.

Absent future players have zero points. Grouping samples by appearance pattern is
an exact computational shortcut, not an independence approximation. Substitution
decisions and final score checks use the public engine's FPL scoring functions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations, permutations
from math import sqrt
from statistics import fmean, pvariance

from ..forecasting.horizon import OutcomeSamples
from ..models import ElementScore, Manager, Pick, Player, Position
from ..substitutions import effective_multipliers, score_manager

COUNTS = {Position.GOALKEEPER: 2, Position.DEFENDER: 5, Position.MIDFIELDER: 5, Position.FORWARD: 3}
LIMITS = {
    Position.GOALKEEPER: (1, 1),
    Position.DEFENDER: (3, 5),
    Position.MIDFIELDER: (2, 5),
    Position.FORWARD: (1, 3),
}
ORDER = tuple(COUNTS)


def validate_squad(
    ids: tuple[int, ...], players: dict[int, Player], *, club_limit: int | None = None
) -> None:
    if len(ids) != 15 or len(set(ids)) != 15 or not set(ids) <= players.keys():
        raise ValueError("A squad needs 15 distinct known players.")
    if Counter(players[pid].position for pid in ids) != COUNTS:
        raise ValueError(
            "A squad must contain 2 goalkeepers, 5 defenders, 5 midfielders and 3 forwards."
        )
    if (
        club_limit is not None
        and max(Counter(players[pid].team_id for pid in ids).values()) > club_limit
    ):
        raise ValueError(f"The resulting squad exceeds the {club_limit}-player club limit.")


def make_lineup(
    ids: tuple[int, ...],
    players: dict[int, Player],
    starters: tuple[int, ...],
    bench: tuple[int, ...],
    captain: int,
    vice: int,
    chip: str | None = None,
) -> Manager:
    validate_squad(ids, players)
    if (
        len(starters) != 11
        or len(bench) != 4
        or len(set(starters + bench)) != 15
        or set(starters + bench) != set(ids)
    ):
        raise ValueError(
            "Select exactly eleven starters and four distinct substitutes from the squad."
        )
    counts = Counter(players[pid].position for pid in starters)
    if any(not low <= counts[position] <= high for position, (low, high) in LIMITS.items()):
        raise ValueError(
            "Use one goalkeeper, at least three defenders, two midfielders and one forward."
        )
    if players[bench[0]].position != Position.GOALKEEPER or any(
        players[p].position == Position.GOALKEEPER for p in bench[1:]
    ):
        raise ValueError(
            "The reserve goalkeeper must be first on the bench, followed by three outfield substitutes."
        )
    if captain == vice or captain not in starters or vice not in starters:
        raise ValueError("Captain and vice-captain must be different starting players.")
    if chip not in {None, "bboost", "3xc"}:
        raise ValueError(
            "Only no chip, Bench Boost or Triple Captain can be evaluated for a fixed squad."
        )
    return Manager(
        0,
        "My team",
        "Private plan",
        tuple(
            Pick(pid, index, 0, pid == captain, pid == vice)
            for index, pid in enumerate(starters + bench, 1)
        ),
        active_chip=chip,
    )


@lru_cache(maxsize=8192)
def _bench_flags(
    formation: tuple[int, int, int],
    missing: tuple[int, int, int],
    positions: tuple[Position, ...],
    available: tuple[bool, ...],
) -> tuple[bool, ...]:
    """Position-equivalent representative calls the shared autosub implementation."""
    if not any(missing) or not any(available):
        return False, False, False
    starter_positions = [Position.GOALKEEPER]
    for position, number in zip(ORDER[1:], formation, strict=True):
        starter_positions.extend([position] * number)
    all_positions = starter_positions + [Position.GOALKEEPER] + list(positions)
    players = {i: Player(i, "", 1, "", position) for i, position in enumerate(all_positions, 1)}
    scores = {i: ElementScore(0, 1) for i in range(1, 12)}
    for position, number in zip(ORDER[1:], missing, strict=True):
        for pid in [pid for pid in range(1, 12) if players[pid].position == position][:number]:
            scores[pid] = ElementScore(0, 0)
    scores[12] = ElementScore(0, 0)
    scores.update({i: ElementScore(0, int(plays)) for i, plays in enumerate(available, 13)})
    manager = Manager(0, "", "", tuple(Pick(i, i) for i in range(1, 16)))
    multipliers = effective_multipliers(manager, players, scores, {1: True})
    return tuple(bool(multipliers[i]) for i in range(13, 16))


class LineupOptimiser:
    def __init__(
        self, players: dict[int, Player], outcomes: dict[int, OutcomeSamples], ids: tuple[int, ...]
    ) -> None:
        validate_squad(ids, players)
        self.ids = tuple(sorted(ids))
        self.players = players
        self.outcomes = outcomes
        self.n = len(outcomes[ids[0]].points)
        if self.n == 0 or any(
            len(outcomes[pid].points) != self.n
            or len(outcomes[pid].minutes) != self.n
            or len(outcomes[pid].appeared) != self.n
            for pid in ids
        ):
            raise ValueError("Squad outcomes must use the same nonempty joint sample set.")
        self.index = {pid: i for i, pid in enumerate(self.ids)}
        self.bits = {pid: 1 << i for pid, i in self.index.items()}
        self.means = {pid: fmean(outcomes[pid].points) for pid in ids}
        groups: dict[int, list[int]] = defaultdict(lambda: [0] * 15)
        for sample in range(self.n):
            mask = sum(self.bits[pid] for pid in ids if outcomes[pid].appeared[sample])
            for pid in ids:
                if not outcomes[pid].appeared[sample] and outcomes[pid].points[sample]:
                    raise ValueError(
                        "An absent future player must have zero points; disciplinary appearances need an appearance flag."
                    )
                groups[mask][self.index[pid]] += outcomes[pid].points[sample]
        self.groups = tuple(groups.items())
        self.captain_pairs = {
            (captain, vice): sum(
                outcomes[captain].points[i]
                if outcomes[captain].minutes[i] > 0
                else outcomes[vice].points[i]
                if outcomes[vice].minutes[i] > 0
                else 0
                for i in range(self.n)
            )
            / self.n
            for captain in ids
            for vice in ids
            if captain != vice
        }
        self.candidates = 0

    def _base_score(
        self, starters: tuple[int, ...], bench: tuple[int, ...], chip: str | None
    ) -> float:
        if chip == "bboost":
            return sum(self.means.values())
        base = sum(self.means[pid] for pid in starters)
        goalkeeper = next(
            pid for pid in starters if self.players[pid].position == Position.GOALKEEPER
        )
        groups = [
            tuple(pid for pid in starters if self.players[pid].position == position)
            for position in ORDER[1:]
        ]
        formation = tuple(len(group) for group in groups)
        positions = tuple(self.players[pid].position for pid in bench[1:])
        bonus = 0
        for mask, totals in self.groups:
            if not mask & self.bits[goalkeeper] and mask & self.bits[bench[0]]:
                bonus += totals[self.index[bench[0]]]
            missing = tuple(
                sum(not bool(mask & self.bits[pid]) for pid in group) for group in groups
            )
            available = tuple(bool(mask & self.bits[pid]) for pid in bench[1:])
            flags = _bench_flags(formation, missing, positions, available)
            bonus += sum(
                totals[self.index[pid]]
                for pid, counts in zip(bench[1:], flags, strict=True)
                if counts
            )
        return base + bonus / self.n

    def _captain_key(self, pair: tuple[int, int]) -> tuple:
        captain, vice = pair
        return self.captain_pairs[pair], self.means[vice], self.means[captain], -captain, -vice

    def optimise(self, chip: str | None = None) -> dict:
        if chip not in {None, "3xc", "bboost"}:
            raise ValueError("Unsupported fixed-squad chip.")
        self.candidates = 0
        best = None
        goalkeepers = tuple(
            pid for pid in self.ids if self.players[pid].position == Position.GOALKEEPER
        )
        outfield = tuple(pid for pid in self.ids if pid not in goalkeepers)
        for goalkeeper in goalkeepers:
            for chosen in combinations(outfield, 10):
                starters = (goalkeeper,) + chosen
                counts = Counter(self.players[pid].position for pid in starters)
                if any(
                    not low <= counts[position] <= high for position, (low, high) in LIMITS.items()
                ):
                    continue
                captain, vice = max(
                    ((c, v) for c in starters for v in starters if c != v), key=self._captain_key
                )
                reserve = next(pid for pid in goalkeepers if pid != goalkeeper)
                others = sorted(
                    (pid for pid in outfield if pid not in chosen),
                    key=lambda pid: (-self.means[pid], pid),
                )
                for ordering in permutations(others):
                    bench = (reserve,) + ordering
                    value = (
                        self._base_score(starters, bench, chip)
                        + (2 if chip == "3xc" else 1) * self.captain_pairs[captain, vice]
                    )
                    self.candidates += 1
                    if best is None or value > best[0] + 1e-10:
                        best = value, starters, bench, captain, vice
        if best is None:
            raise ValueError("No legal XI exists for this squad.")
        _, starters, bench, captain, vice = best
        result = self.evaluate(starters, bench, captain, vice, chip)
        if abs(result["expected_points"] - best[0]) > 1e-8:
            raise ValueError("Grouped optimisation did not reconcile with shared FPL scoring.")
        result["legal_lineups_evaluated"] = self.candidates
        result["method"] = (
            "Exhaustive legal XI, six bench orders, and every starting captain/vice pair; optimal on these shared samples, not a guarantee of future points."
        )
        result["captain_alternatives"] = [
            {
                "captain": c,
                "vice_captain": v,
                "expected_extra_points": self.captain_pairs[c, v] * (2 if chip == "3xc" else 1),
            }
            for c, v in sorted(
                ((c, v) for c in starters for v in starters if c != v),
                key=self._captain_key,
                reverse=True,
            )[:5]
        ]
        return result

    def evaluate(
        self,
        starters: tuple[int, ...],
        bench: tuple[int, ...],
        captain: int,
        vice: int,
        chip: str | None = None,
    ) -> dict:
        manager = make_lineup(self.ids, self.players, starters, bench, captain, vice, chip)
        values = []
        base_values = []
        uncaptained = Manager(
            0,
            "",
            "",
            tuple(Pick(p.player_id, p.squad_position) for p in manager.picks),
            active_chip=chip,
        )
        complete = {player.team_id: True for player in self.players.values()}
        for i in range(self.n):
            scores = {
                pid: ElementScore(
                    self.outcomes[pid].points[i],
                    self.outcomes[pid].minutes[i],
                    bool(self.outcomes[pid].appeared[i] and not self.outcomes[pid].minutes[i]),
                )
                for pid in self.ids
            }
            values.append(score_manager(manager, self.players, scores, complete))
            base_values.append(score_manager(uncaptained, self.players, scores, complete))
        ordered = sorted(values)
        counts = Counter(self.players[pid].position for pid in starters)
        mean = fmean(values)
        return {
            "starting_ids": list(starters),
            "bench_ids": list(bench),
            "captain": captain,
            "vice_captain": vice,
            "chip": chip,
            "formation": "-".join(str(counts[position]) for position in ORDER[1:]),
            "expected_points": mean,
            "expected_base_points": fmean(base_values),
            "expected_captaincy_extra": mean - fmean(base_values),
            "captain_appearance_probability": sum(m > 0 for m in self.outcomes[captain].minutes)
            / self.n,
            "vice_takeover_probability": sum(
                self.outcomes[captain].minutes[i] == 0 and self.outcomes[vice].minutes[i] > 0
                for i in range(self.n)
            )
            / self.n,
            "points_interval_80": [
                ordered[int((self.n - 1) * 0.1)],
                ordered[int((self.n - 1) * 0.9)],
            ],
            "standard_error": sqrt(pvariance(values) / self.n),
            "samples": self.n,
        }
