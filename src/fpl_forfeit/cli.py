from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .analysis import analyse_state, analysis_to_dict
from .api import FPLAPIError, FPLClient, load_snapshot, save_snapshot, state_from_snapshot
from .formatting import render_report
from .settings import (
    DEFAULT_MAX_RELEVANT_PLAYERS,
    DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION,
    DEFAULT_MAX_SEARCH_NODES,
    DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION,
    DEFAULT_SCENARIO_COUNT,
    AnalysisSettings,
)

DEFAULT_CONFIG_PATH = Path(".fpl-forfeit.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpl-forfeit",
        description="Find who can still finish last in an FPL mini-league this Gameweek.",
    )
    parser.add_argument(
        "league_id",
        nargs="?",
        type=int,
        help="Classic league ID; defaults to league_id in .fpl-forfeit.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Local JSON configuration file (default: .fpl-forfeit.json)",
    )
    parser.add_argument("--gameweek", type=int, help="Gameweek (defaults to FPL's current event)")
    parser.add_argument("--snapshot", type=Path, help="Analyse a previously saved JSON snapshot")
    parser.add_argument(
        "--save-snapshot", type=Path, help="Save fetched input data for replay/tests"
    )
    parser.add_argument(
        "--expected-managers",
        type=int,
        default=12,
        help="Refuse a surprising league size; use 0 to allow any size (default: 12)",
    )
    parser.add_argument(
        "--scenarios",
        type=int,
        default=DEFAULT_SCENARIO_COUNT,
        help="Distinct examples per at-risk manager",
    )
    parser.add_argument(
        "--max-relevant",
        type=int,
        default=DEFAULT_MAX_RELEVANT_PLAYERS,
        help="Maximum player-fixture variables in scenario search (default: 8)",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_SEARCH_NODES,
        help="Search effort per manager",
    )
    parser.add_argument(
        "--min-remaining-contribution",
        "--min-points-per-fixture",
        dest="min_remaining_contribution",
        type=int,
        default=DEFAULT_MIN_REMAINING_PLAYER_CONTRIBUTION,
        help="Lower bound for one player's total remaining Gameweek contribution (default: -10)",
    )
    parser.add_argument(
        "--max-remaining-contribution",
        "--max-points-per-fixture",
        dest="max_remaining_contribution",
        type=int,
        default=DEFAULT_MAX_REMAINING_PLAYER_CONTRIBUTION,
        help="Upper bound for one player's total remaining Gameweek contribution (default: 35)",
    )
    parser.add_argument(
        "--strict-last",
        action="store_true",
        help="Require strictly fewer points; by default a tie for last counts",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def configured_league_id(cli_value: int | None, config_path: Path) -> int:
    if cli_value is not None:
        return cli_value
    if not config_path.exists():
        raise FPLAPIError(
            'Provide LEAGUE_ID or create .fpl-forfeit.json containing {"league_id": 123456}'
        )
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        league_id = config["league_id"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise FPLAPIError(f"Could not read league_id from {config_path}: {exc}") from exc
    if isinstance(league_id, bool) or not isinstance(league_id, int) or league_id <= 0:
        raise FPLAPIError(f"league_id in {config_path} must be a positive integer")
    return league_id


def run(args: argparse.Namespace) -> int:
    if args.snapshot:
        snapshot = load_snapshot(args.snapshot)
    else:
        league_id = configured_league_id(args.league_id, args.config)
        snapshot = FPLClient().fetch_snapshot(league_id, args.gameweek)
        if args.save_snapshot:
            save_snapshot(snapshot, args.save_snapshot)

    state = state_from_snapshot(snapshot)
    if args.expected_managers and len(state.managers) != args.expected_managers:
        raise FPLAPIError(
            f"Expected {args.expected_managers} managers but found {len(state.managers)} in "
            f"{state.league_name!r}. Pass --expected-managers 0 to accept this."
        )
    settings = AnalysisSettings(
        min_remaining_player_contribution=args.min_remaining_contribution,
        max_remaining_player_contribution=args.max_remaining_contribution,
        scenario_count=args.scenarios,
        max_relevant_players=args.max_relevant,
        max_search_nodes=args.max_nodes,
        allow_tied_last=not args.strict_last,
    )
    result = analyse_state(state, settings)
    if args.json:
        print(json.dumps(analysis_to_dict(result), indent=2, ensure_ascii=False))
    else:
        print(
            render_report(
                state,
                result.standings,
                result.safety,
                result.searches,
                min_remaining_player_contribution=(settings.min_remaining_player_contribution),
                max_remaining_player_contribution=(settings.max_remaining_player_contribution),
                allow_tied_last=settings.allow_tied_last,
            )
        )
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
