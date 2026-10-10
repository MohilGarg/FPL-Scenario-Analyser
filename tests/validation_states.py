"""Realistic deterministic inputs for validation; these are not historical snapshots."""

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from fpl_forfeit.models import ElementScore, Fixture, LeagueState, Manager, Pick, Player, Position
from fpl_forfeit.scoring import event_points, final_manager_scores


def validation_state(remaining: int = 5, managers: int = 12) -> LeagueState:
    rng = random.Random(841)
    positions = (
        [Position.GOALKEEPER] * 2
        + [Position.DEFENDER] * 6
        + [Position.MIDFIELDER] * 6
        + [Position.FORWARD] * 3
    )
    players = {}
    for team in range(1, 21):
        for slot, position in enumerate(positions):
            pid = team * 100 + slot
            starts = 10 if slot == 0 else 0 if slot == 1 else 9 if slot % 3 else 3
            minutes = starts * (90 if position == Position.GOALKEEPER else 78)
            players[pid] = Player(
                pid,
                f"Team {team} player {slot}",
                team,
                f"Team {team}",
                position,
                season_starts=starts,
                season_minutes=minutes,
                season_team_matches=10,
                season_goals=0 if position == Position.GOALKEEPER else rng.randrange(5),
                season_assists=rng.randrange(4),
                season_saves=30 if slot == 0 else 0,
                season_defensive_contribution=starts * (8 if position == Position.DEFENDER else 6),
                season_yellow_cards=rng.randrange(3),
                availability_status="a",
            )
    fixtures = tuple(
        Fixture(
            i,
            11,
            2 * i - 1,
            2 * i,
            f"Team {2 * i - 1}",
            f"Team {2 * i}",
            i <= 10 - remaining,
            i <= 10 - remaining,
            datetime(2026, 10, 10, 12, tzinfo=UTC) + timedelta(hours=i),
            2 if i <= 10 - remaining else None,
            1 if i <= 10 - remaining else None,
        )
        for i in range(1, 11)
    )
    values = {pid: ElementScore(0, 0) for pid in players}
    fixture_stats, fixture_minutes = {}, {}
    for f in fixtures:
        if not f.finished:
            continue
        for team, conceded in ((f.home_team_id, 1), (f.away_team_id, 2)):
            starters = [
                p
                for p in players.values()
                if p.team_id == team and p.id % 100 in (0, 2, 3, 4, 5, 8, 9, 10, 11, 14, 15)
            ]
            for p in starters:
                stats = {"minutes": 90, "goals_conceded": conceded}
                if p.id % 100 == 14:
                    stats["goals_scored"] = 2 if team == f.home_team_id else 1
                    stats["bonus"] = 3 if team == f.home_team_id else 1
                if p.id % 100 == 8:
                    stats["assists"] = 1
                if p.position == Position.DEFENDER:
                    stats["defensive_contribution"] = 10
                values[p.id] = ElementScore(event_points(p.position, stats), 90)
                fixture_stats[(p.id, f.id)] = stats
                fixture_minutes[(p.id, f.id)] = 90
    squads = []
    squad_positions = [Position.GOALKEEPER] + [Position.DEFENDER] * 4 + [Position.MIDFIELDER] * 4
    squad_positions += [Position.FORWARD] * 2
    squad_positions += [
        Position.GOALKEEPER,
        Position.DEFENDER,
        Position.MIDFIELDER,
        Position.FORWARD,
    ]
    for entry in range(1, managers + 1):
        chosen, teams = [], Counter()
        for index, position in enumerate(squad_positions, start=1):
            candidates = [
                p
                for p in players.values()
                if p.position == position and p.id not in chosen and teams[p.team_id] < 3
            ]
            p = rng.choices(candidates, weights=[p.season_starts + 2 for p in candidates])[0]
            chosen.append(p.id)
            teams[p.team_id] += 1
        picks = tuple(
            Pick(pid, i, is_captain=i == 10, is_vice_captain=i == 6)
            for i, pid in enumerate(chosen, start=1)
        )
        squads.append(
            Manager(
                entry,
                f"Manager {entry}",
                f"Squad {entry}",
                picks,
                transfer_cost=4 if entry % 5 == 0 else 0,
                active_chip="3xc" if entry == 1 else "bboost" if entry == 2 else None,
            )
        )
    state = LeagueState(
        777,
        "Synthetic validation league",
        11,
        players,
        fixtures,
        tuple(squads),
        values,
        datetime(2026, 10, 10, 12, tzinfo=UTC),
        fixture_minutes,
        fixture_stats=fixture_stats,
        team_goal_rates={team: (1.5, 1.3) for team in range(1, 21)},
    )
    if not remaining:
        from dataclasses import replace

        final = final_manager_scores(state, values)
        state.managers = tuple(
            replace(m, official_points=final[m.entry_id] + m.transfer_cost) for m in state.managers
        )
        state.event_final = True
    return state
