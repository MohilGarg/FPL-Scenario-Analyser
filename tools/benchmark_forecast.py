"""Reproducible local computation benchmarks; excludes network fetch latency."""

from __future__ import annotations

import argparse
import json
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from fpl_forfeit.analysis import analysis_to_dict
from fpl_forfeit.api import load_snapshot, state_from_snapshot
from fpl_forfeit.forecast import Condition, outlook
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.solver import solve_candidate
from tests.validation_states import validation_state


def timed(call):
    start = perf_counter()
    result = call()
    return round(perf_counter() - start, 5), result


class SavedClient:
    def __init__(self, states):
        self.states, self.calls = states, 0

    def fetch_snapshot(self, league_id, **kwargs):
        self.calls += 1
        return {"league_id": league_id, "fetched_at": self.states[league_id].fetched_at.isoformat()}


def benchmark(state):
    result = {
        "managers": len(state.managers),
        "owned_players": len({p.player_id for m in state.managers for p in m.picks}),
        "remaining_fixtures": sum(not f.finished for f in state.fixtures),
    }
    result["projection_96_seconds"], _ = timed(
        lambda: outlook(state, samples=96, include_risk=False)
    )
    result["simulation_1600_seconds"], _ = timed(lambda: outlook(state))
    fixture = next(f for f in state.fixtures if not f.finished)
    condition = Condition(fixture.id, "score", home_goals=2, away_goals=1)
    result["what_if_1600_seconds"], _ = timed(lambda: outlook(state, conditions=(condition,)))
    states = {state.league_id: state}
    client = SavedClient(states)
    service = AnalysisService(client)
    with patch(
        "fpl_forfeit.service.state_from_snapshot", side_effect=lambda raw: states[raw["league_id"]]
    ):
        result["cold_summary_seconds"], summary = timed(
            lambda: service.analyse_league(state.league_id, include_scenarios=False)
        )
        result["cached_summary_seconds"], _ = timed(
            lambda: service.analyse_league(state.league_id, include_scenarios=False)
        )
        source = summary["league"]["fetched_at"]
        result["cold_service_outlook_seconds"], _ = timed(
            lambda: service.forecast(state.league_id, source=source)
        )
        result["cached_outlook_seconds"], _ = timed(
            lambda: service.forecast(state.league_id, source=source)
        )
        result["cold_service_what_if_seconds"], _ = timed(
            lambda: service.forecast(state.league_id, source=source, conditions=(condition,))
        )
        result["cached_what_if_seconds"], _ = timed(
            lambda: service.forecast(state.league_id, source=source, conditions=(condition,))
        )
        service._analyses.clear()
        with patch("fpl_forfeit.service.analysis_to_dict", wraps=analysis_to_dict) as compute:
            with ThreadPoolExecutor(max_workers=4) as pool:
                result["concurrent_four_summary_seconds"], _ = timed(
                    lambda: list(
                        pool.map(
                            lambda _: service.analyse_league(
                                state.league_id, include_scenarios=False
                            ),
                            range(4),
                        )
                    )
                )
            result["concurrent_summary_computations"] = compute.call_count
        service._outlooks.clear()
        with patch("fpl_forfeit.service.outlook", wraps=outlook) as compute:
            with ThreadPoolExecutor(max_workers=4) as pool:
                result["concurrent_four_outlook_seconds"], _ = timed(
                    lambda: list(
                        pool.map(
                            lambda _: service.forecast(state.league_id, source=source), range(4)
                        )
                    )
                )
            result["concurrent_outlook_computations"] = compute.call_count
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    report = {
        "measurement": "One timed run per cold operation; local Python wall clock, no upstream network. Synthetic states are not forecasting evidence.",
        "workloads": {},
    }
    for count in (12, 50):
        report["workloads"][f"synthetic_{count}"] = benchmark(validation_state(5, count))
        print(json.dumps(report["workloads"][f"synthetic_{count}"]), flush=True)
    if args.snapshot:
        report["workloads"]["official_saved"] = benchmark(
            state_from_snapshot(load_snapshot(args.snapshot))
        )
        print(json.dumps(report["workloads"]["official_saved"]), flush=True)
    for remaining in (5, 3, 1):
        state = validation_state(remaining, 12)
        elapsed, result = timed(
            lambda state=state: solve_candidate(state, state.managers[0], max_nodes=3000)
        )
        report.setdefault("exact", {})[str(remaining)] = {
            "seconds": elapsed,
            "nodes": result.nodes_checked,
            "exhausted": result.exhausted,
            "examples": len(result.scenarios),
        }
        print("exact", remaining, report["exact"][str(remaining)], flush=True)
    state = validation_state(5, 50)
    tracemalloc.start()
    outlook(state, samples=400)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report["peak_allocated_mib_50_managers_400_samples"] = round(peak / 1024**2, 2)
    # Independent leagues: cache identity must not collapse these jobs.
    states = {i: validation_state(3, 12) for i in range(1, 5)}
    for i, state in states.items():
        state.league_id = i
    service = AnalysisService(SavedClient(states))
    with (
        patch(
            "fpl_forfeit.service.state_from_snapshot",
            side_effect=lambda raw: states[raw["league_id"]],
        ),
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        report["four_distinct_cold_leagues_summary_seconds"], _ = timed(
            lambda: list(
                pool.map(lambda i: service.analyse_league(i, include_scenarios=False), states)
            )
        )
        report["four_distinct_cold_leagues_outlook_seconds"], _ = timed(
            lambda: list(
                pool.map(
                    lambda i: service.forecast(i, source=states[i].fetched_at.isoformat()), states
                )
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Saved", args.output, flush=True)


if __name__ == "__main__":
    main()
