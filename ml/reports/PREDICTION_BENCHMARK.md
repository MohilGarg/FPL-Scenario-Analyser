# Prediction benchmark and promotion policy

Measured on 5 November 2026. These are retrospective **development** results. Neither previously inspected historical season is an untouched final test. The statistical production default remains unchanged.

## Starting point reproduced

Reloading the original frozen recent-average, Ridge and LightGBM models reproduces every saved prediction on 26,098 validation and 28,497 consumed-test observations within 1e-12, with identical prepared input SHA256 hashes and no refitting. The loader now resolves the pinned deadline CSV relative to this checkout: the old absolute OneDrive path prevented reproduction. The old frozen-code verifier therefore reports a changed loader fingerprint; the new explicit reproduction command reports that portability correction and checks target identity and predictions directly.

The earlier Ridge alpha really was fixed at 100. The existing fixture-aware model was worse than the statistical replay (points RMSE 2.05790 vs 2.03690) and frozen Ridge (1.97460) on its paired 24,530-row population. Its schedule audit has only one of 76 fitting Gameweeks with a qualifying timestamped fixture capture; the other 75 use labelled final-schedule proxies. Those numbers are a verified saved-artifact reference, not a newly rerun joint-simulation experiment.

## Population and chronology

No appearance-only filter is used. The earlier validation retained 15,029 zero-minute rows. This experiment also includes cold starts rather than discarding them; only complete, verified-deadline, quality-eligible source targets enter scoring.

| Season | Source rows | Included | Zero minutes | Cold starts | Quarantined |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023-24 | 28,742 | 28,742 | 17,656 | 865 | 0 |
| 2024-25 | 26,919 | 26,881 | 15,470 | 783 | 38 |

This is the full available **prepared source population**, not a verified deadline-eligible historic roster. Missing registration/blank rows cannot be invented as zeros. Historical prices, timestamped injuries and publication/revision vintages are unavailable. Thus price/injury groups and strict point-in-time superiority are unmeasured.

The four outer 2024/25 blocks are GW1–10, 11–20, 21–29 and 30–38. At each refit, training deadlines and each training target's latest fixture kickoff plus 24 hours must both precede the first evaluation deadline. All players at one deadline remain together. Ridge selects alpha on two expanding inner deadline blocks using pooled RMSE; median imputation, dropped columns and scaling are fitted inside each fold. Earlier outer observations may enter later refits only when their results are ready. 2025/26 outcomes are not read by this experiment; the separate reproduction command only verifies the already consumed original results.

## Paired expected-points results

Every model predicts all 26,881 evaluation rows across all 38 deadlines (100% coverage of this source population). The 6,013 selection candidates are defined before their deadlines by preceding-five-fixture mean minutes ≥60 and start rate ≥80%. This is an explicit regular-starter proxy, not an assertion of current eligibility. Bias is prediction minus actual.

| Model | All MAE | All RMSE | Bias | Candidate MAE | Candidate RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| recent_average | 1.08632 | 2.19066 | +0.00126 | 2.62201 | 3.56480 |
| ols_small | 1.08350 | 2.00587 | -0.00724 | 2.32899 | 3.18079 |
| ridge_fixed100 | 1.04301 | 1.97501 | +0.02418 | 2.32151 | 3.14116 |
| ridge_tuned | 1.04273 | 1.97512 | +0.01514 | 2.30689 | 3.13874 |
| ridge_no_minutes | 1.07416 | 2.00091 | -0.00484 | 2.28128 | 3.17514 |
| tree_tuned | 1.04236 | 1.97977 | +0.01962 | 2.31403 | 3.15133 |

The tuned Ridge and tree do **not** establish an accuracy improvement over fixed-alpha Ridge. Their tiny MAE differences do not justify promotion, and RMSE does not improve. Removing minutes/starts/appearance features worsens points RMSE: minutes information earns its place. The no-minutes ablation keeps the selected full-model alpha fixed, so it measures that pipeline's sensitivity, not the optimum re-tuned ablation.

Differences below are contender minus fixed-alpha Ridge. Four-consecutive-deadline moving-block resampling (2,000 paired draws) preserves players together and some serial dependence. Intervals omit historical data bias, selection uncertainty and long-range dependence.

| Contender | MAE difference [95%] | RMSE difference [95%] | Deadlines with lower RMSE |
| --- | --- | --- | ---: |
| recent_average | +0.04330 [+0.02763, +0.06000] | +0.21565 [+0.19712, +0.23836] | 1/38 |
| ols_small | +0.04048 [+0.03264, +0.05574] | +0.03086 [+0.01734, +0.04025] | 7/38 |
| ridge_tuned | -0.00029 [-0.00415, +0.00393] | +0.00011 [-0.00273, +0.00347] | 18/38 |
| ridge_no_minutes | +0.03115 [+0.02290, +0.04036] | +0.02590 [+0.01867, +0.03346] | 5/38 |
| tree_tuned | -0.00065 [-0.00847, +0.00652] | +0.00476 [-0.00034, +0.00788] | 18/38 |

### Ridge search and tree search

Ridge searches alpha = 0.1, 1, 10, 100, 1,000 and 10,000. These are training-scale penalties, selected by squared error rather than MAE to target conditional means.

| Outer first GW | Selected alpha | Inner pooled RMSE at 100 | At selected alpha |
| --- | ---: | ---: | ---: |
| 1 | 10000 | 2.03787 | 2.01678 |
| 11 | 10000 | 2.01443 | 2.00526 |
| 21 | 1000 | 2.02237 | 2.01816 |
| 30 | 1000 | 2.02796 | 2.02653 |

The bounded LightGBM search compares (leaves, minimum rows/leaf) = (7,100), (15,200), (31,200), with 120 trees, learning rate .04, depth cap 5, L2=10, two CPU threads and deterministic seed 20261105. A trailing past-deadline block chooses the configuration, with independently fitted preprocessing. The outer choices are [7, 100], [31, 200], [15, 200], [31, 200].

### Interpretable OLS

The first outer-fold raw-unit equation (training-only median filling) is:

```text
E[points] = 0.22551 +1.96439 * recent_xG +3.17699 * recent_xA +0.04285 * recent_minutes -1.86930 * recent_starts -0.30470 * recent_goals +0.01913 * recent_FPL_assists +0.17208 * recent_FPL_points
```

Each recent regressor is the mean over at most three eligible preceding fixture rows, including nonappearances; these are not per-90 rates. All seven first-fold imputation medians are zero, learned from training only. A coefficient is a conditional points change per unit holding correlated regressors fixed, not a causal effect. For example, minutes has units points/minute and xG has points/expected goal. The negative starts coefficient is a collinearity adjustment; it is not evidence that starting hurts a player's points. Equations and imputation medians are saved for every outer refit.

## Playing-time diagnostics

Separate regularised logistic diagnostic models use only past 3/5/10-fixture minutes, start rate, appearance rate and counts. C=.1 is fixed. They are not a coherent joint runtime distribution or a new selected default. Appearance/start baselines shrink the last-five rate towards training prevalence with two pseudo-observations, avoiding misleading infinite penalties for empirical 0/1 forecasts. The 60-minute baseline uses training prevalence because the approved history schema has no threshold-rate regressor. For doubles, the target means any appearance, any start, or any individual fixture reaching 60; 30+30 minutes does not satisfy the last condition.

| Probability | Baseline Brier | Logistic Brier | Baseline log loss | Logistic log loss |
| --- | ---: | ---: | ---: | ---: |
| appearance | 0.11780 | 0.10748 | 0.38806 | 0.35170 |
| start | 0.10993 | 0.09628 | 0.36143 | 0.31075 |
| sixty | 0.20533 | 0.09884 | 0.60106 | 0.31671 |

The independently implemented selectable private minutes-state model was also evaluated on 27,245 paired 2024/25 player-fixture rows, including 783 cold-start player-GWs. Neutral availability is used for both methods because historical injury vintages are unknown. These are fixture proposals before legal team-lineup constraints; prospective realised simulation evaluation remains necessary.

| Proposal metric | Existing statistical | Experimental minutes states |
| --- | ---: | ---: |
| appearance Brier | 0.15713 | 0.14254 |
| start Brier | 0.16171 | 0.12946 |
| sixty Brier | 0.14748 | 0.12251 |
| Minutes mae | 27.01537 | 21.45240 |
| Minutes rmse | 30.88864 | 27.85839 |
| Minutes bias | 13.41685 | 6.79977 |

Missing, transferred or invalid-history fallbacks: 813 player-GWs; they remain in both sides of paired evaluation. This diagnostic does not establish better captaincy, transfers or joint score distributions.

Paired minutes RMSE difference: -3.030 [-3.385, -2.799] (95%, 2,000 four-deadline blocks). Both methods receive identical pre-deadline appearance histories and season totals. The new model keeps the runtime statistical fallback for absent or transferred histories.

| Proposal subgroup | N fixtures | Statistical minutes RMSE | Minutes states RMSE |
| --- | ---: | ---: | ---: |
| cold | 785 | 52.113 | 52.113 |
| sparse_1_to3 | 2,342 | 38.375 | 34.213 |
| regular_starters | 5,348 | 29.767 | 29.745 |

The practically important regular-starter group barely improves. The large all-roster gain is concentrated among sparse/low-selection players, and does not establish better minutes forecasts for the players normally considered for captaincy. Cold starts retain weak statistical fallbacks. This is a useful coverage/selection correction, with candidate calibration and future evidence still required.

For regular starters, appearance Brier worsens 0.09109 to 0.09274: the new mean appearance probability 0.8851 underpredicts the observed 0.8988. Minutes bias changes from -0.639 to -1.111 minutes. Unknown injuries, selection changes and rotation remain major unmeasured inputs; the roster-wide gain is insufficient evidence for promotion.

## Coverage, unresolved experiments and promotion rule

The machine-readable aggregate includes positions, recent-minute bands, cold/sparse history, doubles, observed-zero-minute diagnostics and 10-bin probability calibration. The local results also retain alpha fold tables and OLS coefficients. Recent-minute bands are pre-deadline history, not predicted-minute bands. Historical price/injury groups, trustworthy external forecast backtests and multi-GW source comparisons remain unavailable. No historical external forecast is backdated, and consensus is never used as a target. The statistical and fixture simulation references have different populations and information; they must not be compared directly with this new table.

Promotion is reserved for untouched prospective evidence at equivalent cutoffs: at least eight settled deadlines; ≥99% coverage of official eligible roster rows (report the missing share); ≥0.05 points or ≥2% lower RMSE on the predeclared selection-candidate population; paired deadline-block 95% upper bound below zero; no material all-roster RMSE, bias or appearance/start/60 calibration regression; gains across at least 60% of deadlines; and successful stale/missing-source fallback, restart recovery and equivalent information-cutoff checks. This prospective gate is fixed before future results. Until it passes, every new model/ensemble stays explicitly experimental and the existing default stays in place. Decision-level captaincy/lineup gains also need joint simulation calibration and Monte Carlo convergence checks.

## Reproduction

```powershell
$env:PYTHONPATH='src'
ml/.venv/Scripts/python.exe -m ml.prediction_benchmark reproduce --output-dir ml/artifacts/prediction-benchmark-v2-final
ml/.venv/Scripts/python.exe -m ml.prediction_benchmark run --output-dir ml/artifacts/prediction-benchmark-v2-reproduction
ml/.venv/Scripts/python.exe -m ml.evaluate_playing_time --output ml/artifacts/prediction-benchmark-v2-reproduction/playing_time.json
ml/.venv/Scripts/python.exe -m ml.report_prediction_benchmark --directory ml/artifacts/prediction-benchmark-v2-reproduction
ml/.venv/Scripts/python.exe -m unittest ml.tests.test_prediction_benchmark -v
```

Use the pinned isolated `ml/training/requirements.lock.txt` stack and existing verified `ml.prepare run` data. Raw histories, fitted arrays and full local artifacts stay ignored. The report command only renders saved values. Re-running requires a fresh directory and never overwrites the evidence. The committed aggregate JSON contains no player IDs, personal squads or restricted-provider rows.
