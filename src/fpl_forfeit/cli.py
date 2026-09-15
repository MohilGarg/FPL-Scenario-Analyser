from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .api import FPLAPIError, FPLClient, load_snapshot, save_snapshot, state_from_snapshot
from .formatting import render_report
from .safety import assess_safety
from .solver import SearchResult, solve_candidate
from .state import current_standings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpl-forfeit",
        description="Find who can still finish last in an FPL mini-league this Gameweek.",
    )
    parser.add_argument("league_id", nargs="?", type=int, help="Classic league ID from its URL")
    parser.add_argument("--gameweek", type=int, help="Gameweek (defaults to FPL's current event)")
    parser.add_argument("--snapshot", type=Path, help="Analyse a previously saved JSON snapshot")
    parser.add_argument("--save-snapshot", type=Path, help="Save fetched input data for replay/tests")
    parser.add_argument(
        "--expected-managers",
        type=int,
        default=12,
        help="Refuse a surprising league size; use 0 to allow any size (default: 12)",
    )
    parser.add_argument(
        "--scenarios", type=int, default=3, help="Distinct examples per at-risk manager"
    )
    parser.add_argument(
        "--max-relevant",
        type=int,
        default=8,
        help="Maximum player-fixture variables in scenario search (default: 8)",
    )
    parser.add_argument(
        "--max-nodes", type=int, default=50_000, help="Search effort per manager"
    )
    parser.add_argument(
        "--min-points-per-fixture",
        type=int,
        default=-8,
        help="Lower safety-bound swing per owned player-fixture (default: -8)",
    )
    parser.add_argument(
        "--max-points-per-fixture",
        type=int,
        default=20,
        help="Upper safety-bound swing per owned player-fixture (default: 20)",
    )
    parser.add_argument(
        "--strict-last",
        action="store_true",
        help="Require strictly fewer points; by default a tie for last counts",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def _json_report(
    state: Any,
    standings: Sequence[Any],
    safety: dict[int, Any],
    searches: dict[int, SearchResult],
) -> str:
    payload = {
        "league": {"id": state.league_id, "name": state.league_name},
        "gameweek": state.gameweek,
        "fetched_at": state.fetched_at.isoformat(),
        "standings": [
            {
                "entry_id": row.manager.entry_id,
                "manager": row.manager.manager_name,
                "team": row.manager.team_name,
                "effective_score": row.effective_score,
                "transfer_cost": row.manager.transfer_cost,
                "active_chip": row.manager.active_chip,
                "safe": safety[row.manager.entry_id].safe,
                "bounds": {
                    "lower": safety[row.manager.entry_id].bounds.lower,
                    "upper": safety[row.manager.entry_id].bounds.upper,
                },
                "scenarios": [
                    {
                        "plausibility_cost": scenario.plausibility_cost,
                        "final_scores": scenario.final_scores,
                        "events": [
                            {
                                "player_id": outcome.player_id,
                                "player": state.players[outcome.player_id].name,
                                "fixture_id": outcome.fixture_id,
                                "description": outcome.label,
                                "points_delta": outcome.points_delta,
                                "minutes_delta": outcome.minutes_delta,
                            }
                            for outcome in scenario.outcomes
                        ],
                    }
                    for scenario in searches.get(
                        row.manager.entry_id,
                        SearchResult(row.manager.entry_id, (), 0, 0, True),
                    ).scenarios
                ],
            }
            for row in standings
        ],
        "limits": {
            "safety_model": "bounded per owned player-fixture",
            "scenario_ranks_are_probabilities": False,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def run(args: argparse.Namespace) -> int:
    if args.snapshot:
        snapshot = load_snapshot(args.snapshot)
    else:
        if args.league_id is None:
            raise FPLAPIError("Provide LEAGUE_ID or --snapshot PATH")
        snapshot = FPLClient().fetch_snapshot(args.league_id, args.gameweek)
        if args.save_snapshot:
            save_snapshot(snapshot, args.save_snapshot)

    state = state_from_snapshot(snapshot)
    if args.expected_managers and len(state.managers) != args.expected_managers:
        raise FPLAPIError(
            f"Expected {args.expected_managers} managers but found {len(state.managers)} in "
            f"{state.league_name!r}. Pass --expected-managers 0 to accept this."
        )
    standings = current_standings(state)
    scores = {row.manager.entry_id: row.effective_score for row in standings}
    safety = assess_safety(
        state,
        scores,
        min_points_per_fixture=args.min_points_per_fixture,
        max_points_per_fixture=args.max_points_per_fixture,
    )
    searches: dict[int, SearchResult] = {}
    for row in standings:
        if safety[row.manager.entry_id].safe:
            continue
        searches[row.manager.entry_id] = solve_candidate(
            state,
            row.manager,
            limit=args.scenarios,
            max_relevant=args.max_relevant,
            max_nodes=args.max_nodes,
            allow_tied_last=not args.strict_last,
        )
    if args.json:
        print(_json_report(state, standings, safety, searches))
    else:
        print(render_report(state, standings, safety, searches))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # Windows may otherwise inherit a legacy code page that cannot print manager names.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (FPLAPIError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
