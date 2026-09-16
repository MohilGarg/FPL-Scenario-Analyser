from __future__ import annotations

from .scenarios import Outcome


def scenario_cost(outcomes: tuple[Outcome, ...]) -> float:
    """A transparent rarity/complexity heuristic; never a validity test."""

    changed = sum(not outcome.baseline for outcome in outcomes)
    event_cost = sum(outcome.plausibility_cost for outcome in outcomes)
    complexity_penalty = max(0, changed - 1) * 0.35
    return round(event_cost + complexity_penalty, 3)
