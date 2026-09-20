from __future__ import annotations

from .scenarios import Outcome, route_key


def individual_cost(outcome: Outcome) -> float:
    """Non-shared cost; also a monotone lower bound for best-first search."""
    return max(0.0, outcome.plausibility_cost - outcome.shared_event_cost)


def scenario_cost(outcomes: tuple[Outcome, ...]) -> float:
    """A transparent rarity/complexity heuristic; never a validity test."""

    shared: dict[tuple[int, int, str], float] = {}
    separate_changes = 0
    for outcome in outcomes:
        if outcome.shared_event:
            shared[outcome.shared_event] = max(
                shared.get(outcome.shared_event, 0), outcome.shared_event_cost
            )
        # A team clean sheet / loss is one event, not an independent coincidence for
        # each owned teammate. Attacking returns and bookings remain separate events.
        pure_shared = (
            outcome.shared_event is not None
            and route_key(outcome) in {"clean_sheet", "clean_sheet_loss"}
            and "booked" not in outcome.label
        )
        separate_changes += int(not outcome.baseline and not pure_shared)
    changed = separate_changes + len(shared)
    event_cost = sum(individual_cost(outcome) for outcome in outcomes) + sum(shared.values())
    complexity_penalty = max(0, changed - 1) * 0.35
    return round(event_cost + complexity_penalty, 3)
