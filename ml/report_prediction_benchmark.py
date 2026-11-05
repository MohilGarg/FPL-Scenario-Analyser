"""Render measured aggregate development results without refitting any model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def render(directory: Path, output: Path) -> None:
    result = json.loads((directory / "results.json").read_bytes())
    original_path = directory / "original_reproduction.json"
    if not original_path.exists():
        original_path = Path("ml/artifacts/prediction-benchmark-v2/original_reproduction.json")
    original = json.loads(original_path.read_bytes())
    minutes_path = directory / "playing_time.json"
    minutes = json.loads(minutes_path.read_bytes()) if minutes_path.exists() else None
    lines = [
        "# Prediction benchmark and promotion policy",
        "",
        (
            "Measured on 5 November 2026. These are retrospective **development** results. "
            "Neither previously inspected historical season is an untouched final test. "
            "The statistical production default remains unchanged."
        ),
        "",
        "## Starting point reproduced",
        "",
        (
            "Reloading the original frozen recent-average, Ridge and LightGBM models reproduces "
            "every saved prediction on 26,098 validation and 28,497 consumed-test observations "
            "within 1e-12, with identical prepared input SHA256 hashes and no refitting. "
            "The loader now resolves the pinned deadline CSV relative to this checkout: the old "
            "absolute OneDrive path prevented reproduction. The old frozen-code verifier therefore "
            "reports a changed loader fingerprint; the new explicit reproduction command reports "
            "that portability correction and checks target identity and predictions directly."
        ),
        "",
        (
            "The earlier Ridge alpha really was fixed at 100. The existing fixture-aware model "
            "was worse than the statistical replay (points RMSE 2.05790 vs 2.03690) and frozen "
            "Ridge (1.97460) on its paired 24,530-row population. Its schedule audit has only "
            "one of 76 fitting Gameweeks with a qualifying timestamped fixture capture; the other "
            "75 use labelled final-schedule proxies. Those numbers are a verified saved-artifact "
            "reference, not a newly rerun joint-simulation experiment."
        ),
        "",
        "## Population and chronology",
        "",
        (
            "No appearance-only filter is used. The earlier validation retained 15,029 zero-minute "
            "rows. This experiment also includes cold starts rather than discarding them; only "
            "complete, verified-deadline, quality-eligible source targets enter scoring."
        ),
        "",
        "| Season | Source rows | Included | Zero minutes | Cold starts | Quarantined |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for season, audit in result["population_audit"].items():
        lines.append(
            f"| {season} | {audit['source_rows']:,} | {audit['eligible_source_rows']:,} | "
            f"{audit['zero_minute_rows']:,} | {audit['cold_start_rows']:,} | {audit['quarantined_rows']} |"
        )
    lines += [
        "",
        (
            "This is the full available **prepared source population**, not a verified deadline-eligible "
            "historic roster. Missing registration/blank rows cannot be invented as zeros. Historical "
            "prices, timestamped injuries and publication/revision vintages are unavailable. Thus "
            "price/injury groups and strict point-in-time superiority are unmeasured."
        ),
        "",
        (
            "The four outer 2024/25 blocks are GW1–10, 11–20, 21–29 and 30–38. At each refit, "
            "training deadlines and each training target's latest fixture kickoff plus 24 hours must "
            "both precede the first evaluation deadline. All players at one deadline remain together. "
            "Ridge selects alpha on two expanding inner deadline blocks using pooled RMSE; median "
            "imputation, dropped columns and scaling are fitted inside each fold. Earlier outer "
            "observations may enter later refits only when their results are ready. 2025/26 outcomes "
            "are not read by this experiment; the separate reproduction command only verifies the "
            "already consumed original results."
        ),
        "",
        "## Paired expected-points results",
        "",
        (
            "Every model predicts all 26,881 evaluation rows across all 38 deadlines (100% coverage "
            "of this source population). The 6,013 selection candidates are defined before their "
            "deadlines by preceding-five-fixture mean minutes ≥60 and start rate ≥80%. This is an "
            "explicit regular-starter proxy, not an assertion of current eligibility. Bias is prediction minus actual."
        ),
        "",
        "| Model | All MAE | All RMSE | Bias | Candidate MAE | Candidate RMSE |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, groups in result["metrics"].items():
        all_rows, candidates = (
            groups["all_source_population"],
            groups["selection_candidates_predeadline"],
        )
        lines.append(
            f"| {name} | {all_rows['mae']:.5f} | {all_rows['rmse']:.5f} | {all_rows['bias']:+.5f} | "
            f"{candidates['mae']:.5f} | {candidates['rmse']:.5f} |"
        )
    lines += [
        "",
        (
            "The tuned Ridge and tree do **not** establish an accuracy improvement over fixed-alpha "
            "Ridge. Their tiny MAE differences do not justify promotion, and RMSE does not improve. "
            "Removing minutes/starts/appearance features worsens points RMSE: minutes information "
            "earns its place. The no-minutes ablation keeps the selected full-model alpha fixed, "
            "so it measures that pipeline's sensitivity, not the optimum re-tuned ablation."
        ),
        "",
        (
            "Differences below are contender minus fixed-alpha Ridge. Four-consecutive-deadline "
            "moving-block resampling (2,000 paired draws) preserves players together and some serial "
            "dependence. Intervals omit historical data bias, selection uncertainty and long-range dependence."
        ),
        "",
        "| Contender | MAE difference [95%] | RMSE difference [95%] | Deadlines with lower RMSE |",
        "| --- | --- | --- | ---: |",
    ]
    for name, d in result["paired_vs_fixed100"].items():
        lines.append(
            f"| {name} | {d['mae_delta']:+.5f} [{d['mae_ci95'][0]:+.5f}, {d['mae_ci95'][1]:+.5f}] | "
            f"{d['rmse_delta']:+.5f} [{d['rmse_ci95'][0]:+.5f}, {d['rmse_ci95'][1]:+.5f}] | "
            f"{d['deadlines_improved_rmse']}/{d['deadlines']} |"
        )
    lines += [
        "",
        "### Ridge search and tree search",
        "",
        (
            "Ridge searches alpha = 0.1, 1, 10, 100, 1,000 and 10,000. These are training-scale "
            "penalties, selected by squared error rather than MAE to target conditional means."
        ),
        "",
        "| Outer first GW | Selected alpha | Inner pooled RMSE at 100 | At selected alpha |",
        "| --- | ---: | ---: | ---: |",
    ]
    for f in result["folds"]:
        scores = f["inner_folds"][0]["pooled_rmse_by_alpha"]
        lines.append(
            f"| {f['gameweeks'][0]} | {f['selected_alpha']:g} | {scores['100.0']:.5f} | {scores[str(f['selected_alpha'])]:.5f} |"
        )
    lines += [
        "",
        "The bounded LightGBM search compares (leaves, minimum rows/leaf) = (7,100), "
        "(15,200), (31,200), with 120 trees, learning rate .04, depth cap 5, L2=10, two CPU "
        "threads and deterministic seed 20261105. A trailing past-deadline block chooses the "
        "configuration, with independently fitted preprocessing. The outer choices are "
        + ", ".join(str(t["chosen"]) for t in result["tree_selection"])
        + ".",
        "",
        "### Interpretable OLS",
        "",
        "The first outer-fold raw-unit equation (training-only median filling) is:",
        "",
        "```text",
    ]
    equation = result["ols_equations"][0]
    names = {
        "expected_goals": "xG",
        "expected_assists": "xA",
        "minutes": "minutes",
        "starts": "starts",
        "goals_scored": "goals",
        "assists": "FPL_assists",
        "total_points": "FPL_points",
    }
    expression = f"E[points] = {equation['intercept_raw_imputed']:.5f}"
    for feature, coefficient in equation["coefficients_per_raw_unit"].items():
        stat = feature.removeprefix("pre_gw_last3_matches_").removesuffix("_mean")
        expression += f" {coefficient:+.5f} * recent_{names[stat]}"
    lines += [
        expression,
        "```",
        "",
        (
            "Each recent regressor is the mean over at most three eligible preceding "
            "fixture rows, including nonappearances; these are not per-90 rates. All seven first-fold "
            "imputation medians are zero, learned from training only. A coefficient is a conditional "
            "points change per unit holding correlated regressors fixed, not a causal effect. For "
            "example, minutes has units points/minute and xG has points/expected goal. The negative "
            "starts coefficient is a collinearity adjustment; it is not evidence that starting hurts "
            "a player's points. Equations and imputation medians are saved for every outer refit."
        ),
        "",
        "## Playing-time diagnostics",
        "",
        (
            "Separate regularised logistic diagnostic models use only past 3/5/10-fixture minutes, "
            "start rate, appearance rate and counts. C=.1 is fixed. They are not a coherent joint "
            "runtime distribution or a new selected default. Appearance/start baselines shrink the "
            "last-five rate towards training prevalence with two pseudo-observations, avoiding "
            "misleading infinite penalties for empirical 0/1 forecasts. The 60-minute baseline uses "
            "training prevalence because the approved history schema has no threshold-rate regressor. "
            "For doubles, the target means any appearance, any start, or any individual fixture "
            "reaching 60; 30+30 minutes does not satisfy the last condition."
        ),
        "",
        "| Probability | Baseline Brier | Logistic Brier | Baseline log loss | Logistic log loss |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, values in result["probability_metrics"].items():
        a, b = values["baseline"], values["logistic"]
        lines.append(
            f"| {name} | {a['brier']:.5f} | {b['brier']:.5f} | {a['log_loss']:.5f} | {b['log_loss']:.5f} |"
        )
    if minutes:
        lines += [
            "",
            (
                "The independently implemented selectable private minutes-state model was also evaluated "
                f"on {minutes['fixture_rows']:,} paired 2024/25 player-fixture rows, including "
                f"{minutes['cold_start_player_gameweeks']:,} cold-start player-GWs. Neutral availability "
                "is used for both methods because historical injury vintages are unknown. These "
                "are fixture proposals before legal team-lineup constraints; prospective realised "
                "simulation evaluation remains necessary."
            ),
            "",
            "| Proposal metric | Existing statistical | Experimental minutes states |",
            "| --- | ---: | ---: |",
        ]
        for name in ("appearance", "start", "sixty"):
            a, b = (
                minutes["metrics"]["statistical"][name],
                minutes["metrics"]["minutes_states"][name],
            )
            lines.append(f"| {name} Brier | {a['brier']:.5f} | {b['brier']:.5f} |")
        for name in ("mae", "rmse", "bias"):
            a, b = (
                minutes["metrics"]["statistical"]["minutes"][name],
                minutes["metrics"]["minutes_states"]["minutes"][name],
            )
            lines.append(f"| Minutes {name} | {a:.5f} | {b:.5f} |")
        lines += [
            "",
            (
                f"Missing, transferred or invalid-history fallbacks: {minutes['fallback_player_gameweeks']:,} player-GWs; "
                "they remain in both sides of paired evaluation. This diagnostic does not establish "
                "better captaincy, transfers or joint score distributions."
            ),
        ]
        uncertainty = minutes["minutes_paired_uncertainty"]
        lines += [
            "",
            (
                f"Paired minutes RMSE difference: {uncertainty['rmse_delta']:+.3f} "
                f"[{uncertainty['rmse_ci95'][0]:+.3f}, {uncertainty['rmse_ci95'][1]:+.3f}] "
                "(95%, 2,000 four-deadline blocks). Both methods receive identical pre-deadline "
                "appearance histories and season totals. The new model keeps the runtime "
                "statistical fallback for absent or transferred histories."
            ),
            "",
            "| Proposal subgroup | N fixtures | Statistical minutes RMSE | Minutes states RMSE |",
            "| --- | ---: | ---: | ---: |",
        ]
        for group in ("cold", "sparse_1_to3", "regular_starters"):
            values = minutes["groups"][group]
            baseline = values["methods"]["statistical"]["minutes"]["rmse"]
            contender = values["methods"]["minutes_states"]["minutes"]["rmse"]
            lines.append(f"| {group} | {values['n']:,} | {baseline:.3f} | {contender:.3f} |")
        lines += [
            "",
            (
                "The practically important regular-starter group barely improves. The large all-roster "
                "gain is concentrated among sparse/low-selection players, and does not establish "
                "better minutes forecasts for the players normally considered for captaincy. Cold "
                "starts retain weak statistical fallbacks. This is a useful coverage/selection "
                "correction, with candidate calibration and future evidence still required."
            ),
        ]
        regular = minutes["groups"]["regular_starters"]["methods"]
        old = regular["statistical"]
        new = regular["minutes_states"]
        lines += [
            "",
            (
                f"For regular starters, appearance Brier worsens {old['appearance']['brier']:.5f} "
                f"to {new['appearance']['brier']:.5f}: the new mean appearance probability "
                f"{new['appearance']['mean_predicted']:.4f} underpredicts the observed "
                f"{new['appearance']['observed_rate']:.4f}. Minutes bias changes from "
                f"{old['minutes']['bias']:+.3f} to {new['minutes']['bias']:+.3f} minutes. "
                "Unknown injuries, selection changes and rotation remain major unmeasured inputs; "
                "the roster-wide gain is insufficient evidence for promotion."
            ),
        ]
    lines += [
        "",
        "## Coverage, unresolved experiments and promotion rule",
        "",
        (
            "The machine-readable aggregate includes positions, recent-minute bands, cold/sparse "
            "history, doubles, observed-zero-minute diagnostics and 10-bin probability calibration. "
            "The local results also retain alpha fold tables and OLS coefficients. "
            "Recent-minute bands are pre-deadline history, "
            "not predicted-minute bands. Historical price/injury groups, trustworthy external "
            "forecast backtests and multi-GW source comparisons remain unavailable. No historical "
            "external forecast is backdated, and consensus is never used as a target. The statistical "
            "and fixture simulation references have different populations and information; they "
            "must not be compared directly with this new table."
        ),
        "",
        (
            "Promotion is reserved for untouched prospective evidence at equivalent cutoffs: at "
            "least eight settled deadlines; ≥99% coverage of official eligible roster rows (report "
            "the missing share); ≥0.05 points or ≥2% lower RMSE on the predeclared selection-candidate "
            "population; paired deadline-block 95% upper bound below zero; no material all-roster "
            "RMSE, bias or appearance/start/60 calibration regression; gains across at least 60% "
            "of deadlines; and successful stale/missing-source fallback, restart recovery and "
            "equivalent information-cutoff checks. This prospective gate is fixed before future "
            "results. Until it passes, every new model/ensemble stays explicitly experimental and "
            "the existing default stays in place. Decision-level captaincy/lineup gains also need "
            "joint simulation calibration and Monte Carlo convergence checks."
        ),
        "",
        "## Reproduction",
        "",
        "```powershell",
        "$env:PYTHONPATH='src'",
        "ml/.venv/Scripts/python.exe -m ml.prediction_benchmark reproduce --output-dir ml/artifacts/prediction-benchmark-v2-final",
        "ml/.venv/Scripts/python.exe -m ml.prediction_benchmark run --output-dir ml/artifacts/prediction-benchmark-v2-reproduction",
        "ml/.venv/Scripts/python.exe -m ml.evaluate_playing_time --output ml/artifacts/prediction-benchmark-v2-reproduction/playing_time.json",
        "ml/.venv/Scripts/python.exe -m ml.report_prediction_benchmark --directory ml/artifacts/prediction-benchmark-v2-reproduction",
        "ml/.venv/Scripts/python.exe -m unittest ml.tests.test_prediction_benchmark -v",
        "```",
        "",
        (
            "Use the pinned isolated `ml/training/requirements.lock.txt` stack and existing verified "
            "`ml.prepare run` data. Raw histories, fitted arrays and full local artifacts stay ignored. "
            "The report command only renders saved values. Re-running requires a fresh directory "
            "and never overwrites the evidence. The committed aggregate JSON contains no player "
            "IDs, personal squads or restricted-provider rows."
        ),
        "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    aggregate = {k: v for k, v in result.items() if k not in ("ols_equations", "folds")}
    aggregate["original_reproduction"] = original
    if minutes:
        aggregate["playing_time"] = minutes
    output.with_suffix(".json").write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=Path("ml/artifacts/prediction-benchmark-v2-verified")
    )
    parser.add_argument("--output", type=Path, default=Path("ml/reports/PREDICTION_BENCHMARK.md"))
    args = parser.parse_args()
    render(args.directory, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
