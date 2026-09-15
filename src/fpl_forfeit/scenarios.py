from __future__ import annotations

from dataclasses import dataclass

from .models import ElementScore, Fixture, Player, Position


@dataclass(frozen=True, slots=True)
class Outcome:
    player_id: int
    fixture_id: int
    label: str
    points_delta: int
    minutes_delta: int
    plausibility_cost: float
    own_team_min_goals: int = 0
    opponent_min_goals: int = 0
    opponent_max_goals: int | None = None
    baseline: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioVariable:
    player: Player
    fixture: Fixture
    outcomes: tuple[Outcome, ...]


def _outcome(
    player: Player,
    fixture: Fixture,
    label: str,
    points: int,
    minutes: int,
    cost: float,
    *,
    own_goals: int = 0,
    opponent_goals: int = 0,
    opponent_max: int | None = None,
    baseline: bool = False,
) -> Outcome:
    return Outcome(
        player_id=player.id,
        fixture_id=fixture.id,
        label=label,
        points_delta=points,
        minutes_delta=minutes,
        plausibility_cost=cost,
        own_team_min_goals=own_goals,
        opponent_min_goals=opponent_goals,
        opponent_max_goals=opponent_max,
        baseline=baseline,
    )


def outcome_catalog(
    player: Player, fixture: Fixture, current: ElementScore
) -> tuple[Outcome, ...]:
    """Small, explicit outcome vocabulary for one player in one unfinished fixture.

    Values are additions to official points already reported by FPL. The vocabulary is
    deliberately bounded: it produces useful late-Gameweek examples, not a probability model.
    """

    if fixture.started:
        return _live_outcomes(player, fixture, current)

    if player.position in (Position.GOALKEEPER, Position.DEFENDER):
        goal_points = 6
        values = [
            _outcome(
                player,
                fixture,
                "plays 60+ minutes without a return and concedes",
                2,
                60,
                0.0,
                opponent_goals=1,
                baseline=True,
            ),
            _outcome(player, fixture, "makes only a brief appearance", 1, 1, 0.9),
            _outcome(
                player,
                fixture,
                "keeps a clean sheet",
                6,
                60,
                1.1,
                opponent_max=0,
            ),
            _outcome(player, fixture, "does not play", 0, 0, 1.3),
            _outcome(
                player,
                fixture,
                "gets an assist but no clean sheet",
                5,
                60,
                1.8,
                own_goals=1,
                opponent_goals=1,
            ),
            _outcome(
                player,
                fixture,
                "scores but gets no clean sheet",
                2 + goal_points,
                60,
                _goal_cost(player.position),
                own_goals=1,
                opponent_goals=1,
            ),
            _outcome(
                player,
                fixture,
                "scores and keeps a clean sheet",
                2 + goal_points + 4,
                60,
                _goal_cost(player.position) + 1.1,
                own_goals=1,
                opponent_max=0,
            ),
            _outcome(player, fixture, "plays 60+ minutes and is sent off", -1, 60, 3.5),
        ]
        if player.position == Position.GOALKEEPER:
            values.append(
                _outcome(
                    player,
                    fixture,
                    "saves a penalty without a clean sheet",
                    7,
                    60,
                    4.0,
                    opponent_goals=1,
                )
            )
        return tuple(sorted(values, key=lambda item: item.plausibility_cost))

    if player.position == Position.MIDFIELDER:
        values = [
            _outcome(
                player,
                fixture,
                "plays 60+ minutes without an attacking return and concedes",
                2,
                60,
                0.0,
                opponent_goals=1,
                baseline=True,
            ),
            _outcome(player, fixture, "makes only a brief appearance", 1, 1, 0.8),
            _outcome(
                player,
                fixture,
                "plays 60+ minutes without an attacking return and keeps a clean sheet",
                3,
                60,
                1.0,
                opponent_max=0,
            ),
            _outcome(player, fixture, "does not play", 0, 0, 1.3),
            _outcome(player, fixture, "gets an assist", 5, 60, 1.5, own_goals=1),
            _outcome(player, fixture, "scores", 7, 60, 1.8, own_goals=1),
            _outcome(
                player, fixture, "scores and assists", 10, 60, 3.1, own_goals=2
            ),
            _outcome(player, fixture, "plays 60+ minutes and is sent off", -1, 60, 3.5),
        ]
        return tuple(sorted(values, key=lambda item: item.plausibility_cost))

    values = [
        _outcome(
            player,
            fixture,
            "plays 60+ minutes without an attacking return",
            2,
            60,
            0.0,
            baseline=True,
        ),
        _outcome(player, fixture, "makes only a brief appearance", 1, 1, 0.8),
        _outcome(player, fixture, "does not play", 0, 0, 1.3),
        _outcome(player, fixture, "gets an assist", 5, 60, 1.5, own_goals=1),
        _outcome(player, fixture, "scores", 6, 60, 1.6, own_goals=1),
        _outcome(player, fixture, "scores and assists", 9, 60, 2.9, own_goals=2),
        _outcome(player, fixture, "plays 60+ minutes and is sent off", -1, 60, 3.5),
    ]
    return tuple(sorted(values, key=lambda item: item.plausibility_cost))


def _live_outcomes(
    player: Player, fixture: Fixture, current: ElementScore
) -> tuple[Outcome, ...]:
    goal_points = {
        Position.GOALKEEPER: 6,
        Position.DEFENDER: 6,
        Position.MIDFIELDER: 5,
        Position.FORWARD: 4,
    }[player.position]
    if current.minutes == 0:
        # Once a fixture is live, a new entrant cannot be assumed to reach 60 minutes.
        values = [
            _outcome(
                player,
                fixture,
                "does not come on",
                0,
                0,
                0.0,
                baseline=True,
            ),
            _outcome(player, fixture, "comes on briefly", 1, 1, 0.7),
            _outcome(
                player, fixture, "comes on and assists", 4, 1, 1.8, own_goals=1
            ),
            _outcome(
                player,
                fixture,
                "comes on and scores",
                1 + goal_points,
                1,
                _goal_cost(player.position) + 0.5,
                own_goals=1,
            ),
            _outcome(player, fixture, "comes on and is sent off", -2, 1, 3.5),
        ]
        if player.position == Position.GOALKEEPER:
            values.append(
                _outcome(player, fixture, "comes on and saves a penalty", 6, 1, 4.5)
            )
        return tuple(sorted(values, key=lambda item: item.plausibility_cost))

    reaches_sixty = current.minutes < 60
    minutes_delta = max(0, 60 - current.minutes)
    appearance_delta = 1 if reaches_sixty else 0
    goals_against = fixture.goals_against(player.team_id)
    clean_now = goals_against == 0
    clean_sheet_value = {
        Position.GOALKEEPER: 4,
        Position.DEFENDER: 4,
        Position.MIDFIELDER: 1,
        Position.FORWARD: 0,
    }[player.position]

    baseline_delta = appearance_delta + (clean_sheet_value if reaches_sixty and clean_now else 0)
    baseline_max = 0 if clean_now and clean_sheet_value else None
    baseline_label = "reaches 60 minutes with no attacking return" if reaches_sixty else "gets no more returns"
    if clean_now and clean_sheet_value:
        baseline_label += " and keeps the clean sheet"
    values = [
        _outcome(
            player,
            fixture,
            baseline_label,
            baseline_delta,
            minutes_delta,
            0.0,
            opponent_max=baseline_max,
            baseline=True,
        ),
        _outcome(
            player,
            fixture,
            "is booked and otherwise follows the baseline",
            baseline_delta - 1,
            minutes_delta,
            0.8,
            opponent_max=baseline_max,
        ),
        _outcome(
            player,
            fixture,
            "gets an assist and otherwise follows the baseline",
            baseline_delta + 3,
            minutes_delta,
            1.4,
            own_goals=1,
            opponent_max=baseline_max,
        ),
        _outcome(
            player,
            fixture,
            "scores and otherwise follows the baseline",
            baseline_delta + goal_points,
            minutes_delta,
            _goal_cost(player.position),
            own_goals=1,
            opponent_max=baseline_max,
        ),
        _outcome(
            player,
            fixture,
            "scores and assists while otherwise following the baseline",
            baseline_delta + goal_points + 3,
            minutes_delta,
            _goal_cost(player.position) + 1.5,
            own_goals=2,
            opponent_max=baseline_max,
        ),
        _outcome(player, fixture, "is sent off", -3, 0, 3.4),
    ]
    if clean_now and clean_sheet_value:
        # FPL already includes the clean-sheet points at 60; before 60 they simply never arrive.
        lost_clean_sheet = -clean_sheet_value if not reaches_sixty else 0
        values.append(
            _outcome(
                player,
                fixture,
                "gets no attacking return and loses the clean sheet",
                appearance_delta + lost_clean_sheet,
                minutes_delta,
                0.9,
                opponent_goals=1,
            )
        )
    if player.position == Position.GOALKEEPER:
        values.append(
            _outcome(
                player,
                fixture,
                "saves a penalty and otherwise follows the baseline",
                baseline_delta + 5,
                minutes_delta,
                4.0,
                opponent_max=baseline_max,
            )
        )
    return tuple(sorted(values, key=lambda item: item.plausibility_cost))


def _goal_cost(position: Position) -> float:
    return {
        Position.FORWARD: 1.6,
        Position.MIDFIELDER: 1.8,
        Position.DEFENDER: 2.8,
        Position.GOALKEEPER: 6.0,
    }[position]


def football_consistent(
    outcomes: tuple[Outcome, ...], variables: tuple[ScenarioVariable, ...]
) -> bool:
    by_key = {(variable.player.id, variable.fixture.id): variable for variable in variables}
    minimum: dict[tuple[int, int], int] = {}
    maximum: dict[tuple[int, int], int] = {}
    for outcome in outcomes:
        variable = by_key[(outcome.player_id, outcome.fixture_id)]
        own = variable.player.team_id
        opponent = variable.fixture.opponent_of(own)
        own_key = (outcome.fixture_id, own)
        opponent_key = (outcome.fixture_id, opponent)
        minimum[own_key] = max(minimum.get(own_key, 0), outcome.own_team_min_goals)
        minimum[opponent_key] = max(
            minimum.get(opponent_key, 0), outcome.opponent_min_goals
        )
        if outcome.opponent_max_goals is not None:
            maximum[opponent_key] = min(
                maximum.get(opponent_key, outcome.opponent_max_goals),
                outcome.opponent_max_goals,
            )
    return all(minimum.get(key, 0) <= limit for key, limit in maximum.items())
