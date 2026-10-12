# First baseline machine learning experiments ? Phase 2b

Run on 12 October 2026, continuing commit `b6c4a6e`. **Ridge was selected before the holdout**, by lowest validation MAE. Ridge and LightGBM are effectively tied on MAE; Ridge has the lowest holdout RMSE. These are retrospective outcome-only experiments, not a production-simulator comparison.

## Protocol and population

The existing 82,472 candidate player-GW observations are split chronologically: 27,877 from 2023/24 for initial fitting; 26,098 from 2024/25 for validation/model selection; and 28,497 from 2025/26 for the previously untouched test. After selection all three fixed approaches are refitted on the 53,975 earlier-season rows, with training-fitted preprocessing, before the test population is scored. Test outcomes never choose parameters, features, model family or early stopping. Earlier eligible outcomes within the test season can appear as pre-deadline lagged predictors for later test GWs; model parameters remain frozen.

One upfront configuration per approach is versioned in `ml/training/configs/baselines-v1.json`:

- **Recent average:** the existing mean points of the last three eligible observed GWs, including partial history and zero-minute records. It has no fitted parameters.
- **Ridge:** alpha=100, Cholesky solver, with training-only median imputation and standardisation.
- **LightGBM:** CPU L2 regression; 200 trees, learning rate 0.03, 15 leaves, depth 4, minimum 200 rows/leaf, max_bin=127, L1=0.1/L2=10, feature_fraction=0.8, deterministic seed 20261012; two threads. No early stopping or parameter search.

Only the existing 216 approved numeric predictors enter X. Both fitted preprocessors retained 146, dropped four entirely absent defensive-action sum columns and 66 training-constant columns. Newly available test values cannot activate dropped columns. Existing missingness indicators are retained when varying; their cross-season distribution can still shift. No position, current-GW outcome, opponent/fixture schedule, price, source xP, final-season total, identity or evaluation QA field is a predictor. Predictions are not clipped; genuine negative FPL points remain possible.

The candidate mask excludes 2,490 first-observation/cold-start rows and all 38 Ferguson 2024/25 GWs, with one overlap: 2,527 excluded from the 84,999 prepared observations. The 27-versus-28 source discrepancy remains unchanged. Zero-minute rows are retained (15,029 validation and 17,476 test); absence of an eligible roster/blank-GW row is not fabricated as zero. No deadline or target incompleteness exclusions were necessary in this pinned revision.

Selection and test are separate commands. Config, data, model/metric code and package versions are fingerprinted; a freeze marker and saved models exist before holdout evaluation. File hashes/Parquet storage reads may cover all-season bytes, but only season-filtered earlier-year arrays reach fitting and selection. Models, preprocessing, full predictions, coefficients and gain/split importances remain local in ignored `ml/artifacts/baselines-v1/`.

## Overall results

All metrics are unmultiplied FPL points. **Bias = mean(prediction ? actual)**; negative means underprediction. Validation models fit only 2023/24, whereas test models refit both earlier seasons.

| Approach | Validation MAE | RMSE | Bias | Test MAE | RMSE | Bias |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Recent average | 1.0782 | 2.1931 | +0.0019 | 1.0630 | 2.2015 | +0.0101 |
| Ridge | 1.0348 | 1.9729 | +0.0260 | 0.9932 | 1.9636 | -0.0531 |
| LightGBM | 1.0350 | 1.9762 | +0.0271 | 0.9927 | 1.9725 | -0.0738 |

## Position and minutes groups

Cells are **MAE / RMSE / bias**. Position labels are checked by season + element + player code against the checksum-verified archived official API and must match the source history position. All candidate rows passed (no UNKNOWN groups). This verifies retrospective official-season grouping, not historic pre-deadline availability of position labels. Position and observed minutes are used only after prediction for evaluation.

| Period | Group | N | Recent average | Ridge | LightGBM |
| --- | --- | ---: | --- | --- | --- |
| Validation | GK | 2,745 | 0.7840 / 1.8392 / +0.0089 | 0.7455 / 1.6428 / -0.0563 | 0.7510 / 1.6420 / -0.0301 |
| Validation | DEF | 8,756 | 1.0789 / 2.0904 / -0.0027 | 1.0382 / 1.8834 / +0.0036 | 1.0497 / 1.8858 / +0.0357 |
| Validation | MID | 11,737 | 1.1041 / 2.2509 / -0.0001 | 1.0605 / 2.0294 / +0.0760 | 1.0492 / 2.0307 / +0.0460 |
| Validation | FWD | 2,860 | 1.2520 / 2.5443 / +0.0175 | 1.1972 / 2.2732 / -0.0319 | 1.2042 / 2.2895 / -0.0221 |
| Validation | zero_minutes | 15,029 | 0.2616 / 0.8216 / +0.2566 | 0.3835 / 0.7980 / +0.3786 | 0.3963 / 0.8006 / +0.3963 |
| Validation | nonzero_minutes | 11,069 | 2.1869 / 3.2286 / -0.3439 | 1.9193 / 2.8832 / -0.4528 | 1.9021 / 2.8876 / -0.4743 |
| Test | GK | 3,286 | 0.6123 / 1.5935 / +0.0158 | 0.6037 / 1.4165 / -0.0053 | 0.6020 / 1.4204 / -0.0133 |
| Test | DEF | 9,331 | 1.2432 / 2.4262 / +0.0126 | 1.1357 / 2.1529 / -0.1398 | 1.1322 / 2.1617 / -0.1719 |
| Test | MID | 12,740 | 1.0297 / 2.1323 / +0.0077 | 0.9685 / 1.9127 / -0.0114 | 0.9690 / 1.9217 / -0.0301 |
| Test | FWD | 3,140 | 1.1342 / 2.3212 / +0.0065 | 1.0773 / 2.0676 / -0.0144 | 1.0833 / 2.0809 / -0.0235 |
| Test | zero_minutes | 17,476 | 0.2548 / 0.8750 / +0.2524 | 0.3482 / 0.7567 / +0.3474 | 0.3507 / 0.7442 / +0.3507 |
| Test | nonzero_minutes | 11,021 | 2.3445 / 3.3642 / -0.3740 | 2.0159 / 3.0103 / -0.6880 | 2.0107 / 3.0302 / -0.7471 |

## Error distributions and uncertainty

| Approach | Validation absolute p50 / p90 / p95 | Test absolute p50 / p90 / p95 | Test error ?1 point | Test error ?2 points |
| --- | --- | --- | ---: | ---: |
| Recent average | 0.333 / 3.333 / 5.000 | 0.000 / 3.667 / 5.000 | 73.7% | 82.6% |
| Ridge | 0.352 / 2.761 / 4.222 | 0.236 / 2.717 / 4.137 | 70.2% | 84.5% |
| LightGBM | 0.336 / 2.779 / 4.262 | 0.229 / 2.702 / 4.188 | 70.7% | 84.8% |

The local comparison report also includes signed-error quantiles and negative-prediction fractions. The two inspected exportable charts are `comparison.png` (MAE/RMSE) and `error_distributions.png` (absolute-error CDF, displayed through 12 points; tail metrics use all errors).

**Improvement is modest but consistent against the recent-average baseline:** Ridge reduces validation MAE by 4.02% and RMSE by 10.04%, and holdout MAE by 6.57% and RMSE by 10.81%. The paired four-consecutive-GW moving-block bootstrap (1,000 draws, 37 observed GWs per scored season) gives these approximate 95% intervals for MAE differences; negative favours the contender:

| Comparison | Validation difference [95% interval] | Test difference [95% interval] |
| --- | --- | --- |
| ridge_minus_recent_average | -0.0434 [-0.0607, -0.0273] | -0.0698 [-0.0811, -0.0478] |
| lightgbm_minus_recent_average | -0.0432 [-0.0625, -0.0253] | -0.0703 [-0.0827, -0.0474] |
| ridge_minus_lightgbm | -0.0002 [-0.0075, +0.0072] | +0.0005 [-0.0017, +0.0028] |

The Ridge?LightGBM intervals contain zero in both periods. LightGBM's 0.00047-point lower test MAE is not a meaningful win and does not replace the validation-selected Ridge. The recent average remains better on zero-minute MAE and exact/near-exact errors; learned models improve nonzero-minute error and larger-error tails. Conditional bias in observed appearance groups is not, by itself, proof of unconditional miscalibration. Blocks cover some shared-fixture and serial dependence, but one validation/test season each and long-range player dependence limit significance claims.

## Coefficients and importance

The final models below are trained only on 2023/24+2024/25. Ridge values are points per training-standard-deviation, conditional on strongly correlated inputs; signs are not causal football effects. Full 146-feature standardized/raw coefficients and LightGBM gain/split counts are saved locally.

| Ridge feature | Standardized coefficient |
| --- | ---: |
| `pre_gw_expected_goals_conceded_sum` | -0.4639 |
| `pre_gw_last3_matches_minutes_mean` | +0.4206 |
| `pre_gw_last3_matches_appearance_rate` | +0.3925 |
| `pre_gw_last3_gws_minutes_mean` | +0.3842 |
| `pre_gw_minutes_sum` | +0.3325 |

| LightGBM feature | Gain importance |
| --- | ---: |
| `pre_gw_last3_matches_minutes_mean` | 36.07% |
| `pre_gw_last3_matches_total_points_mean` | 21.15% |
| `pre_gw_points_mean_last3_observed_gws` | 5.99% |
| `pre_gw_last3_matches_appearance_rate` | 5.11% |
| `pre_gw_last3_matches_minutes_sum` | 4.99% |

Recent minutes/appearance and historical points dominate the tree's recorded gain importance. This is a training-derived attribution among correlated variables, not held-out permutation importance or evidence of a causal mechanism.

## Runtime and reproducibility

| Approach | Validation fit seconds | Validation inference seconds | Final refit seconds | Test inference seconds |
| --- | ---: | ---: | ---: | ---: |
| Recent average | 0.000000 | 0.000321 | 0.000000 | 0.000230 |
| Ridge | 0.892610 | 0.008361 | 0.107261 | 0.003184 |
| LightGBM | 1.951839 | 0.122006 | 5.139275 | 0.052757 |

Shared preprocessing fit/transform took 0.4325s and 0.5620s, respectively; validation/test transforms took 0.0513s and 0.0465s. Timings are single-run operation wall times on this machine, excluding file I/O/imports; warm-up can affect them. The average has no training step or preprocessing requirement.

The environment is Python 3.13.5, NumPy 2.3.3, scikit-learn 1.7.2, LightGBM 4.6.0 and PyArrow 21.0.0, with the full dependency lock committed. Forty-three focused ML tests passed. Reloaded fitted models reproduced all 26,098 validation and 28,497 test predictions within 1e-12 without refitting, with config/input/model/metric hashes verified. Only a post-run chart legend layout correction differs from the original rendering source, which is preserved locally; fitting, preprocessing, metrics and the selected approach are unchanged.

See [training commands and artifacts](../training/README.md), [versioned configuration](../training/configs/baselines-v1.json) and [Phase 2a readiness](TRAINING_READINESS.md). The statistical implementations follow the official [Ridge documentation](https://scikit-learn.org/1.7/modules/generated/sklearn.linear_model.Ridge.html) and [LightGBM parameters](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html).

## Limits and next experiment

Final corrected outcome histories lack original stat publication/revision times; the 24-hour readiness delay is a proxy. Historic injuries, eligibility/registration, price, schedule versions and set-piece roles remain missing. Source rows can include long-term zero-minute/unavailable players, so this is not a verified deadline-eligible squad population. Cold starts and blanks are incomplete. The 2025/26 defensive-contribution and assist rule changes create a scoring regime shift; no earlier defensive actions are invented. Correlated/redundant features and changing missingness limit coefficient interpretation and generalisation. These limitations prevent claiming a fair comparison against the production simulator, which can access different information.

**Recommended next experiment, not implemented:** test a two-part appearance-probability plus conditional-points model against this frozen Ridge, using 2023/24 training and 2024/25 validation only. Pre-specify expected-point calibration and MAE/RMSE tradeoffs: post-appearance subgroup bias is partly inherent to an unconditional forecast and cannot simply be trained away. Do not tune against the now-revealed 2025/26 outcomes; reserve new chronologically later data for a genuinely untouched final test. No deployment or Assistant/API/website integration is authorised by these offline results.
