from __future__ import annotations

from dataclasses import dataclass

# Practical conservative bounds for one player's total contribution across all of their
# remaining fixtures in the current Gameweek. They are an analysis envelope, not theoretical
# or historical limits on what can happen in football.
DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION = -10
DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION = 35


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    min_remaining_player_contribution: int = DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION
    max_remaining_player_contribution: int = DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION
    allow_tied_last: bool = True

    def __post_init__(self) -> None:
        if self.min_remaining_player_contribution > self.max_remaining_player_contribution:
            raise ValueError("Minimum remaining contribution cannot exceed the maximum")
