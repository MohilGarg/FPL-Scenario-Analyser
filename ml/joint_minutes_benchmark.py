"""Controlled shared-simulator minutes experiment with honest roster/schedule proxies.

Football rates and scoring are held fixed across the minutes methods. Every
method draws joint match outcomes; external point means never become draws.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import numpy as np

from fpl_forfeit.assistant.attacking_rates import AttackingRateModel
from fpl_forfeit.assistant.fitted_forecast import FittedMinutesSimulator
from fpl_forfeit.assistant.minutes_model import MinutesModel
from fpl_forfeit.assistant.playing_time import MinutesSimulator, estimate_minutes
from fpl_forfeit.forecasting.horizon import forecast_horizon
from fpl_forfeit.forecasting.models import TeamMatchResult
from fpl_forfeit.forecasting.statistics import estimate_team_goal_rates, player_rates
from fpl_forfeit.models import Fixture, FPLCatalogue, GameweekInfo
from fpl_forfeit.points import point_components
from ml.evaluation.metrics import metrics
from ml.fixture_dataset import load as load_fixture_data
from ml.minutes_data import eligible_history, groups, historical_player, load_matches
from ml.prediction_benchmark import paired, probability_scores, write_json

OUTPUT = Path("ml/artifacts/joint-minutes-sprint-v2")


def contexts(root: Path):
    rows, quarantine, deadlines = load_matches(root, ("2024-25",), football=True)
    _, _, _, schedules = load_fixture_data(("2024-25",))
    history, targets = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for row in rows:
        history[row["element"]].append(row)
        targets[row["gameweek"]][row["element"]].append(row)
    for gw, by_player in sorted(targets.items()):
        cutoff = deadlines["2024-25", gw]
        prior = {pid: eligible_history(history[pid], gw, cutoff) for pid in by_player}
        players = {
            pid: historical_player(target[0], prior[pid], football=True)
            for pid, target in by_player.items()
        }
        fixtures = tuple(
            Fixture(
                f["id"],
                gw,
                f["team_h"],
                f["team_a"],
                str(f["team_h"]),
                str(f["team_a"]),
                False,
                False,
                datetime.fromisoformat(f["kickoff_time"]),
            )
            for f in schedules["2024-25"]
            if f["event"] == gw
        )
        completed = [
            TeamMatchResult(f["team_h"], f["team_a"], f["team_h_score"], f["team_a_score"])
            for f in schedules["2024-25"]
            if f["event"] < gw
            and datetime.fromisoformat(f["kickoff_time"]) + timedelta(hours=24) < cutoff
        ]
        catalogue = FPLCatalogue(
            players,
            fixtures,
            (GameweekInfo(gw, cutoff, False),),
            estimate_team_goal_rates(completed),
            cutoff,
            True,
            frozenset(),
            {},
            {},
            3,
            5,
            (),
            scoring_season="2024-25",
        )
        yield {
            "gw": gw,
            "cutoff": cutoff,
            "prior": prior,
            "targets": by_player,
            "catalogue": catalogue,
            "quarantine": quarantine,
            "schedule": schedules["2024-25"],
        }


def artifact_for(gw: int, kind: str, directory: Path):
    first = 1 if gw < 11 else 11 if gw < 21 else 21 if gw < 30 else 30
    return MinutesModel(directory / f"{kind}-GW{first:02}.json")


def rate_metrics(rate):
    sub = (1 - rate.start) * (rate.cameo_probability or 0)
    return {
        "appearance": rate.start + sub,
        "start": rate.start,
        "sixty": rate.start * np.mean(np.asarray(rate.starting_minutes) >= 60)
        + sub * np.mean(np.asarray(rate.cameo_minutes) >= 60),
        "minutes": rate.start * np.mean(rate.starting_minutes)
        + sub * np.mean(rate.cameo_minutes or (0,)),
    }


def estimate_metrics(estimate):
    return {
        "appearance": 1 - estimate.no_show_probability,
        "start": estimate.start_probability,
        "sixty": estimate.sixty_probability,
        "minutes": estimate.expected_minutes,
    }


def capture(ctx, method, samples, seed, model_dir):
    started = perf_counter()
    cat, gw = ctx["catalogue"], ctx["gw"]
    estimates = {}
    model = None
    if method not in {"statistical", "minutes_v1"}:
        if ctx.get("captured_input_replay"):
            model = MinutesModel(
                Path("src/fpl_forfeit/assistant")
                / ("minutes_empirical_v2.json" if method == "empirical" else "minutes_v2.json")
            )
        else:
            model = artifact_for(gw, "empirical" if method == "empirical" else "softmax", model_dir)
    raw = {}
    for pid, player in cat.players.items():
        prior = ctx["prior"][pid]
        for fixture in cat.fixtures:
            if not fixture.involves(player.team_id):
                continue
            old = rate_metrics(player_rates(player))
            if method == "statistical":
                raw[pid, fixture.id] = old
                continue
            try:
                if method == "minutes_v1":
                    if not prior or prior[-1]["team_id"] != player.team_id:
                        raise ValueError("Legacy fallback")
                    estimate = estimate_minutes(player, prior)
                else:
                    estimate = model.predict(player, prior)
                estimates[fixture.id, pid] = estimate
                raw[pid, fixture.id] = estimate_metrics(estimate)
            except ValueError:
                raw[pid, fixture.id] = old
    factory = None
    if method == "minutes_v1":
        factory = lambda inputs: MinutesSimulator(inputs, estimates)
    elif method != "statistical":
        first = 1 if gw < 11 else 11 if gw < 21 else 21 if gw < 30 else 30
        attacking = (
            AttackingRateModel(Path("ml/artifacts/attacking-sprint-v2") / f"GW{first:02}.json")
            if method == "fitted_attacking"
            else None
        )
        factory = lambda inputs: FittedMinutesSimulator(
            inputs,
            estimates,
            version=model.version,
            balanced=method != "fitted_greedy",
            attacking_model=attacking,
        )
    forecast = forecast_horizon(cat, (gw,), samples=samples, seed=seed, simulator_factory=factory)
    projections = {(p.player_id, p.fixture_id): p for p in forecast.fixtures}
    result, fixtures = [], []
    for pid, targets in ctx["targets"].items():
        if (ctx.get("season", "2024-25"), pid, gw) in ctx["quarantine"]:
            continue
        actual = sum(r["total_points"] for r in targets)
        draws = np.asarray(forecast.outcomes[gw][pid].points, dtype=float)
        ordered = np.sort(draws)
        row = forecast.players[pid][gw]
        actual_components = defaultdict(int)
        for target in targets:
            fields = {k: v for k, v in target.items() if isinstance(v, int)}
            for name, value in point_components(
                cat.players[pid].position, fields, season=cat.scoring_season
            ).items():
                actual_components[name] += value
        scored_components = sum(actual_components.values())
        result.append(
            {
                "gameweek": gw,
                "player_id": pid,
                "groups": groups(ctx["prior"][pid], targets[0]),
                "actual": actual,
                "minutes_actual": sum(r["minutes"] for r in targets),
                "points": row["expected_points"],
                "minutes": row["expected_minutes"],
                "appearance": row["appearance_probability"],
                "start": row["starting_probability"],
                "sixty": row["probability_60_minutes"],
                "appearance_actual": int(any(r["minutes"] > 0 for r in targets)),
                "start_actual": int(any(r["starts"] for r in targets)),
                "sixty_actual": int(any(r["minutes"] >= 60 for r in targets)),
                "interval": row["points_interval_80"],
                "crps": float(
                    np.mean(np.abs(draws - actual))
                    - np.sum((2 * np.arange(1, samples + 1) - samples - 1) * ordered)
                    / (samples * samples)
                ),
                "components": row["expected_points_components"],
                "actual_components": dict(actual_components),
                "official_component_residual": actual - scored_components,
            }
        )
        for target in targets:
            key = pid, target["fixture_id"]
            if key not in projections:
                continue
            p = projections[key]
            fixtures.append(
                {
                    "gameweek": gw,
                    "player_id": pid,
                    "fixture_id": key[1],
                    "groups": groups(ctx["prior"][pid], target),
                    "raw": raw[key],
                    "simulated": {
                        "appearance": p.appearance_probability,
                        "start": p.starting_probability,
                        "sixty": p.probability_60_minutes,
                        "minutes": p.expected_minutes,
                    },
                    "actual": {
                        "appearance": int(target["minutes"] > 0),
                        "start": target["starts"],
                        "sixty": int(target["minutes"] >= 60),
                        "minutes": target["minutes"],
                    },
                }
            )
    return {
        "method": method,
        "gameweek": gw,
        "samples": samples,
        "seed": seed,
        "elapsed_seconds": perf_counter() - started,
        "rows": result,
        "fixtures": fixtures,
    }


def summarize(runs, *, membership=None, scope=None):
    # Preserve the original points-benchmark candidate rule, which allows partial
    # five-fixture history. The stricter reliable-starter training objective is
    # reported separately; no outcome or predicted method chooses membership.
    from ml.minutes_data import training_rows

    membership = membership or {
        (r["gameweek"], r["player_id"]): r["groups"]
        for r in training_rows(Path("ml/data/processed"))
        if r["season"] == "2024-25"
    }
    for run in runs:
        for row in [*run["rows"], *run["fixtures"]]:
            row["groups"] = membership[row["gameweek"], row["player_id"]]
    methods = sorted({r["method"] for r in runs})
    seeds = sorted({r["seed"] for r in runs})
    # Primary metrics are one fixed seed; independent-seed differences are reported
    # separately so noise cannot be hidden by picking the more attractive run.
    primary = [r for r in runs if r["seed"] == seeds[0]]
    report = {
        "methods": {},
        "paired_uncertainty": {},
        "monte_carlo": {},
        "replica_point_contrasts": {},
        "paired_ablations": {},
        "scope": scope
        or "2024-25 development; retrospective source-roster and final-fixture proxies, neutral availability; no untouched final test",
        "samples_per_deadline": primary[0]["samples"],
        "seeds": seeds,
    }
    for method in methods:
        selected = [v for r in primary if r["method"] == method for v in r["rows"]]
        fixture_rows = [v for r in primary if r["method"] == method for v in r["fixtures"]]
        report["methods"][method] = {}
        for group in selected[0]["groups"]:
            subset = [r for r in selected if r["groups"][group]]
            if not subset:
                continue
            actual = np.asarray([r["actual"] for r in subset])
            pred = np.asarray([r["points"] for r in subset])
            intervals = [r["interval"] for r in subset]
            distributions = {
                "crps": float(np.mean([r["crps"] for r in subset])),
                "interval80_coverage": float(
                    np.mean([a <= r["actual"] <= b for r, (a, b) in zip(subset, intervals)])
                ),
                "interval80_width": float(np.mean([b - a for a, b in intervals])),
                "interval80_score": float(
                    np.mean(
                        [
                            b - a + 10 * max(0, a - r["actual"]) + 10 * max(0, r["actual"] - b)
                            for r, (a, b) in zip(subset, intervals)
                        ]
                    )
                ),
            }
            values = {
                "points": metrics(actual, pred),
                "minutes": metrics(
                    np.asarray([r["minutes_actual"] for r in subset]),
                    np.asarray([r["minutes"] for r in subset]),
                ),
                "distribution": distributions,
                "components": {},
            }
            for field in ("appearance", "start", "sixty"):
                values[field] = probability_scores(
                    np.asarray([r[field + "_actual"] for r in subset]),
                    np.asarray([r[field] for r in subset]),
                )
            for name in selected[0]["components"]:
                values["components"][name] = metrics(
                    np.asarray([r["actual_components"].get(name, 0) for r in subset]),
                    np.asarray([r["components"].get(name, 0) for r in subset]),
                )
            f = [r for r in fixture_rows if r["groups"][group]]
            values["fixture_proposal_vs_simulation"] = {
                stage: {
                    field: (metrics if field == "minutes" else probability_scores)(
                        np.asarray([r["actual"][field] for r in f]),
                        np.asarray([r[stage][field] for r in f]),
                    )
                    for field in ("minutes", "appearance", "start", "sixty")
                }
                for stage in ("raw", "simulated")
            }
            values["component_official_residual_count"] = sum(
                r["official_component_residual"] != 0 for r in subset
            )
            report["methods"][method][group] = values
    base = {
        (r["gameweek"], v["player_id"]): v
        for r in primary
        if r["method"] == "statistical"
        for v in r["rows"]
    }
    for seed in seeds:
        seed_base = {
            (r["gameweek"], v["player_id"]): v
            for r in runs
            if r["seed"] == seed and r["method"] == "statistical"
            for v in r["rows"]
        }
        report["replica_point_contrasts"][str(seed)] = {}
        for method in methods:
            if method == "statistical":
                continue
            rows = [
                v for r in runs if r["seed"] == seed and r["method"] == method for v in r["rows"]
            ]
            report["replica_point_contrasts"][str(seed)][method] = {}
            for group in ("all", "selection_candidates", "fixed_selection_candidates"):
                subset = [r for r in rows if r["groups"][group]]
                actual = np.asarray([r["actual"] for r in subset])
                a = np.asarray([seed_base[r["gameweek"], r["player_id"]]["points"] for r in subset])
                b = np.asarray([r["points"] for r in subset])
                report["replica_point_contrasts"][str(seed)][method][group] = {
                    "rmse_difference": float(
                        np.sqrt(np.mean((b - actual) ** 2)) - np.sqrt(np.mean((a - actual) ** 2))
                    ),
                    "mae_difference": float(
                        np.mean(np.abs(b - actual)) - np.mean(np.abs(a - actual))
                    ),
                }
    for method in methods:
        if method == "statistical":
            continue
        rows = [v for r in primary if r["method"] == method for v in r["rows"]]
        for group in ("all", "selection_candidates", "fixed_selection_candidates"):
            subset = [r for r in rows if r["groups"][group]]
            if len({r["gameweek"] for r in subset}) >= 4:
                report["paired_uncertainty"][f"{method}:{group}"] = paired(
                    np.asarray([r["actual"] for r in subset]),
                    np.asarray([base[r["gameweek"], r["player_id"]]["points"] for r in subset]),
                    np.asarray([r["points"] for r in subset]),
                    np.asarray([r["gameweek"] for r in subset]),
                )
        if len(seeds) > 1:
            same = {
                (r["gameweek"], v["player_id"]): v
                for r in runs
                if r["method"] == method and r["seed"] == seeds[1]
                for v in r["rows"]
            }
            report["monte_carlo"][method] = {
                g: metrics(
                    np.asarray([r["points"] for r in rows if r["groups"][g]]),
                    np.asarray(
                        [
                            same[r["gameweek"], r["player_id"]]["points"]
                            for r in rows
                            if r["groups"][g]
                        ]
                    ),
                )
                for g in ("all", "selection_candidates", "fixed_selection_candidates")
            }
    for before, after in (
        ("fitted_greedy", "fitted_balanced"),
        ("fitted_balanced", "fitted_attacking"),
    ):
        if before not in methods or after not in methods:
            continue
        comparison = {}
        for seed in seeds:
            first = {
                (r["gameweek"], v["player_id"]): v
                for r in runs
                if r["method"] == before and r["seed"] == seed
                for v in r["rows"]
            }
            second = [
                v for r in runs if r["method"] == after and r["seed"] == seed for v in r["rows"]
            ]
            comparison[str(seed)] = {}
            for group in ("all", "selection_candidates", "fixed_selection_candidates"):
                subset = [r for r in second if r["groups"][group]]
                if len({r["gameweek"] for r in subset}) >= 4:
                    comparison[str(seed)][group] = paired(
                        np.asarray([r["actual"] for r in subset]),
                        np.asarray(
                            [first[r["gameweek"], r["player_id"]]["points"] for r in subset]
                        ),
                        np.asarray([r["points"] for r in subset]),
                        np.asarray([r["gameweek"] for r in subset]),
                    )
        report["paired_ablations"][f"{after}_minus_{before}"] = comparison
    return report


def run_context(task):
    ctx, methods, seeds, samples, model_dir, output = task
    for seed in seeds:
        for method in methods:
            path = output / f"GW{ctx['gw']:02}-{method}-{seed}-{samples}.json"
            if path.exists():
                continue
            result = capture(ctx, method, samples, seed, model_dir)
            write_json(path, result)
            print(f"GW{ctx['gw']} {method} seed{seed} {result['elapsed_seconds']:.1f}s", flush=True)
    return ctx["gw"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--model-dir", type=Path, default=Path("ml/artifacts/minutes-sprint-v2"))
    parser.add_argument("--samples", type=int, default=512)
    parser.add_argument("--gameweeks", type=int, nargs="*")
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["statistical", "minutes_v1", "empirical", "fitted_greedy", "fitted_balanced"],
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=[20261105, 20261205])
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tasks = (
        (ctx, args.methods, args.seeds, args.samples, args.model_dir, args.output_dir)
        for ctx in contexts(Path("ml/data/processed"))
        if not args.gameweeks or ctx["gw"] in args.gameweeks
    )
    if args.workers == 1:
        for task in tasks:
            run_context(task)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            list(executor.map(run_context, tasks))
    runs = [json.loads(p.read_bytes()) for p in args.output_dir.glob(f"GW*-*-{args.samples}.json")]
    report = summarize(runs)
    write_json(args.output_dir / "results.json", report)
    write_json(Path("ml/reports/JOINT_MINUTES_SPRINT.json"), report)


if __name__ == "__main__":
    main()
