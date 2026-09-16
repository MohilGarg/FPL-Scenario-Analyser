from __future__ import annotations

from dataclasses import dataclass, replace

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


def outcome_catalog(player: Player, fixture: Fixture, current: ElementScore) -> tuple[Outcome, ...]:
    """Small, explicit outcome vocabulary for one player in one unfinished fixture.

    Values are additions to official points already reported by FPL. The vocabulary is
    deliberately bounded: it produces useful late-Gameweek examples, not a probability model.
    """

    if fixture.started:
        return _live_outcomes(player, fixture, current)

    if player.position in (Position.GOALKEEPER, Position.DEFENDER):
        goal_points = 10 if player.position == Position.GOALKEEPER else 6
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
            _outcome(
                player,
                fixture,
                "scores and assists without a clean sheet",
                2 + goal_points + 3,
                60,
                _goal_cost(player.position) + 1.7,
                own_goals=2,
                opponent_goals=1,
            ),
            _outcome(player, fixture, "plays 60+ minutes and is sent off", -1, 60, 3.5),
        ]
        if player.position == Position.DEFENDER:
            values.append(
                _outcome(
                    player,
                    fixture,
                    "scores twice without a clean sheet",
                    2 + (goal_points * 2),
                    60,
                    6.2,
                    own_goals=2,
                    opponent_goals=1,
                )
            )
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
        return _pregame_extensions(player, fixture, values)

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
            _outcome(player, fixture, "scores and assists", 10, 60, 3.1, own_goals=2),
            _outcome(player, fixture, "gets two assists", 8, 60, 3.4, own_goals=2),
            _outcome(player, fixture, "scores twice", 12, 60, 4.2, own_goals=2),
            _outcome(player, fixture, "scores a hat-trick", 17, 60, 7.2, own_goals=3),
            _outcome(player, fixture, "plays 60+ minutes and misses a penalty", 0, 60, 3.0),
            _outcome(player, fixture, "plays 60+ minutes and is sent off", -1, 60, 3.5),
        ]
        return _pregame_extensions(player, fixture, values)

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
        _outcome(player, fixture, "gets two assists", 8, 60, 3.3, own_goals=2),
        _outcome(player, fixture, "scores twice", 10, 60, 3.8, own_goals=2),
        _outcome(player, fixture, "scores a hat-trick", 14, 60, 6.5, own_goals=3),
        _outcome(player, fixture, "plays 60+ minutes and misses a penalty", 0, 60, 3.0),
        _outcome(player, fixture, "plays 60+ minutes and is sent off", -1, 60, 3.5),
    ]
    return _pregame_extensions(player, fixture, values)


def _pregame_extensions(
    player: Player, fixture: Fixture, values: list[Outcome]
) -> tuple[Outcome, ...]:
    """Explicit combinations, never a Cartesian product of every scoring event.

    Bonus/BPS and future defensive-contribution awards are not predicted. Already awarded
    official points are retained. Goalkeeper goals follow the 10-point scoring rule.
    """
    defensive = player.position in (Position.GOALKEEPER, Position.DEFENDER)
    goal_points = {
        Position.GOALKEEPER: 10,
        Position.DEFENDER: 6,
        Position.MIDFIELDER: 5,
        Position.FORWARD: 4,
    }[player.position]
    if player.position == Position.MIDFIELDER:
        # Existing attack totals omit the midfielder's clean-sheet point: make that assumption
        # explicit, and offer separate clean-sheet + return routes.
        for index, item in enumerate(tuple(values)):
            if item.own_team_min_goals:
                values[index] = replace(item, opponent_min_goals=1)
                values.append(
                    replace(
                        item,
                        label=item.label + " and keeps a clean sheet",
                        points_delta=item.points_delta + 1,
                        plausibility_cost=item.plausibility_cost + 1,
                        opponent_min_goals=0,
                        opponent_max_goals=0,
                    )
                )
    values.extend(
        [
            _outcome(
                player,
                fixture,
                "plays 60+ minutes and is booked",
                1,
                60,
                0.8,
                opponent_goals=1 if player.position != Position.FORWARD else 0,
            ),
            _outcome(
                player, fixture, "scores an own goal and concedes", 0, 60, 4.2, opponent_goals=1
            ),
        ]
    )
    if player.position in (Position.MIDFIELDER, Position.FORWARD):
        values.append(
            _outcome(
                player,
                fixture,
                "scores twice and assists",
                2 + 2 * goal_points + 3,
                60,
                5.8,
                own_goals=3,
                opponent_goals=1 if player.position == Position.MIDFIELDER else 0,
            )
        )
    if defensive:
        values.extend(
            [
                _outcome(
                    player,
                    fixture,
                    "assists and keeps a clean sheet",
                    9,
                    60,
                    2.9,
                    own_goals=1,
                    opponent_max=0,
                ),
                _outcome(
                    player,
                    fixture,
                    "gets two assists without a clean sheet",
                    8,
                    60,
                    4.2,
                    own_goals=2,
                    opponent_goals=1,
                ),
                _outcome(
                    player, fixture, "misses a penalty and concedes", 0, 60, 4.0, opponent_goals=1
                ),
            ]
        )
    if player.position == Position.GOALKEEPER:
        values.append(
            _outcome(
                player,
                fixture,
                "saves a penalty and keeps a clean sheet",
                11,
                60,
                5.1,
                opponent_max=0,
            )
        )
    return tuple(sorted(values, key=lambda item: item.plausibility_cost))


def _live_outcomes(player: Player, fixture: Fixture, current: ElementScore) -> tuple[Outcome, ...]:
    goal_points = {
        Position.GOALKEEPER: 10,
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
            _outcome(player, fixture, "comes on and assists", 4, 1, 1.8, own_goals=1),
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
            values.append(_outcome(player, fixture, "comes on and saves a penalty", 6, 1, 4.5))
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
    baseline_label = (
        "reaches 60 minutes with no attacking return" if reaches_sixty else "gets no more returns"
    )
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
        _outcome(
            player,
            fixture,
            "gets two assists while otherwise following the baseline",
            baseline_delta + 6,
            minutes_delta,
            3.3,
            own_goals=2,
            opponent_max=baseline_max,
        ),
        _outcome(
            player,
            fixture,
            "scores twice while otherwise following the baseline",
            baseline_delta + (goal_points * 2),
            minutes_delta,
            (_goal_cost(player.position) * 2) + 0.5,
            own_goals=2,
            opponent_max=baseline_max,
        ),
        _outcome(
            player,
            fixture,
            "misses a penalty and otherwise follows the baseline",
            baseline_delta - 2,
            minutes_delta,
            3.0,
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
        # A goal/assist need not also require this player's team to keep a clean sheet.
        for item in tuple(values):
            if item.own_team_min_goals and item.opponent_max_goals == 0:
                values.append(
                    replace(
                        item,
                        label=item.label.split(" and otherwise")[0].split(" while otherwise")[0]
                        + " but loses the clean sheet",
                        points_delta=item.points_delta - clean_sheet_value,
                        opponent_max_goals=None,
                        opponent_min_goals=1,
                        plausibility_cost=item.plausibility_cost + 0.9,
                    )
                )
    values.append(
        _outcome(
            player,
            fixture,
            "scores an own goal",
            appearance_delta - 2 - (clean_sheet_value if clean_now and not reaches_sixty else 0),
            minutes_delta,
            4.2,
            opponent_goals=1,
        )
    )
    if reaches_sixty:
        values.append(_outcome(player, fixture, "leaves before 60 minutes", 0, 0, 0.8))
    if player.position in (Position.MIDFIELDER, Position.FORWARD):
        values.append(
            _outcome(
                player,
                fixture,
                "scores a hat-trick from here",
                baseline_delta + 3 * goal_points,
                minutes_delta,
                7.5,
                own_goals=3,
                opponent_max=baseline_max,
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


def route_key(outcome: Outcome) -> str:
    """Material football route; incidental bookings/minutes do not create new returns."""
    label = outcome.label
    if "own goal" in label:
        return "own_goal"
    if "hat-trick" in label:
        attack = "hat_trick"
    elif "scores twice" in label:
        attack = "brace_assist" if "assist" in label else "brace"
    elif "scores" in label:
        attack = "goal_assist" if "assist" in label else "goal"
    elif "two assists" in label:
        attack = "two_assists"
    elif "assist" in label:
        attack = "assist"
    else:
        attack = ""
    if attack:
        return attack + ("_clean_sheet" if outcome.opponent_max_goals == 0 else "")
    if "penalty" in label:
        return "penalty_save" if "saves" in label else "penalty_miss"
    if "sent off" in label:
        return "red_card"
    if "loses the clean sheet" in label:
        return "clean_sheet_loss"
    if outcome.opponent_max_goals == 0 and "booked" not in label:
        return "clean_sheet"
    if "does not" in label:
        return "no_appearance"
    return "appearance_or_booking"


def football_consistent(
    outcomes: tuple[Outcome, ...], variables: tuple[ScenarioVariable, ...]
) -> bool:
    by_key = {(variable.player.id, variable.fixture.id): variable for variable in variables}
    minimum: dict[tuple[int, int], int] = {}
    maximum: dict[tuple[int, int], int] = {}
    for variable in variables:
        fixture = variable.fixture
        minimum[(fixture.id, fixture.home_team_id)] = fixture.home_score or 0
        minimum[(fixture.id, fixture.away_team_id)] = fixture.away_score or 0
    for outcome in outcomes:
        variable = by_key[(outcome.player_id, outcome.fixture_id)]
        own = variable.player.team_id
        opponent = variable.fixture.opponent_of(own)
        own_key = (outcome.fixture_id, own)
        opponent_key = (outcome.fixture_id, opponent)
        own_current = variable.fixture.goals_against(opponent) or 0
        opponent_current = variable.fixture.goals_against(own) or 0
        minimum[own_key] = max(minimum.get(own_key, 0), own_current + outcome.own_team_min_goals)
        minimum[opponent_key] = max(
            minimum.get(opponent_key, 0), opponent_current + outcome.opponent_min_goals
        )
        if outcome.opponent_max_goals is not None:
            maximum[opponent_key] = min(
                maximum.get(opponent_key, outcome.opponent_max_goals),
                outcome.opponent_max_goals,
            )
    return all(minimum.get(key, 0) <= limit for key, limit in maximum.items())
