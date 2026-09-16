from __future__ import annotations

from dataclasses import dataclass

# Practical conservative bounds for one player's total contribution across all of their
# remaining fixtures in the current Gameweek. They are an analysis envelope, not theoretical
# or historical limits on what can happen in football.
DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION = -10
DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION = 35

DEFAULT_SCENARIO_COUNT = 3
DEFAULT_MAX_RELEVANT_PLAYERS = 8
DEFAULT_MAX_SEARCH_NODES = 50_000


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    min_remaining_player_contribution: int = DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION
    max_remaining_player_contribution: int = DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION
    scenario_count: int = DEFAULT_SCENARIO_COUNT
    max_relevant_players: int = DEFAULT_MAX_RELEVANT_PLAYERS
    max_search_nodes: int = DEFAULT_MAX_SEARCH_NODES
    allow_tied_last: bool = True

    def __post_init__(self) -> None:
        if self.min_remaining_player_contribution > self.max_remaining_player_contribution:
            raise ValueError("Minimum remaining contribution cannot exceed the maximum")
        if self.scenario_count < 1:
            raise ValueError("scenario_count must be at least 1")
        if self.max_relevant_players < 1:
            raise ValueError("max_relevant_players must be at least 1")
        if self.max_search_nodes < 1:
            raise ValueError("max_search_nodes must be at least 1")
