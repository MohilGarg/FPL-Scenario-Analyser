# Two-stage appearance and conditional points - Phase 2c

Run on 12 October 2026, continuing `c246de2`. **The fixed two-stage model does not improve
on Ridge.** Validation MAE is 0.00577 points worse (0.56%), with slightly worse RMSE and
expected-point calibration. The absolute difference is small; Ridge remains the benchmark.
No production code or website behaviour changes.

## Protocol and reproducibility

- Fit exclusively on **27,877 2023/24 player-GWs**; evaluate the identical **26,098 2024/25
  player-GWs** used by Phase 2b Ridge, with unchanged deadlines, history gates and exclusions.
  Reloaded Ridge validation predictions match the previous experiment within `1e-12`.
- **2025/26 is already consumed.** This experiment has no test stage, does not load its arrays
  or saved test models/metrics, and makes no fresh holdout claim. Its earlier results remain
  historical reference in [BASELINE_EXPERIMENTS.md](BASELINE_EXPERIMENTS.md).
  The general dataset integrity validator still checks the existing three-season preparation;
  it does not fit, score or select this experiment's models.
- One predeclared configuration, [two-stage-v1.json](../training/configs/two-stage-v1.json),
  with no parameter search, validation refit, probability recalibration or prediction clipping.
  The repeated run after adding a missing-history fallback produces identical candidate predictions.
  No LightGBM extension was needed for this bounded, interpretable experiment.
- All 216 approved predictors enter training-only median imputation and standardisation.
  As before, 146 vary in training; four training-empty and 66 constant columns are dropped.
  A single preprocessor is fitted on all 2023/24 X and shared by both stages and Ridge;
  the conditional Ridge itself fits only positive-minute rows. Later values cannot activate
  dropped columns or change medians/scales. No identity, position, evaluation QA, current-GW
  outcome, final snapshot, price, source xP, opponent or schedule enters X.
- Fitting uses Python 3.13.5, scikit-learn 1.7.2, NumPy 2.3.3 and SciPy 1.16.2, two CPU
  threads, seed 20261012 and the existing dependency lock. Exact versions, configuration,
  source-lock/code hashes and season-filtered feature/target/minutes/row-key hashes are saved.

Selected SHA-256 fingerprints (full remaining fingerprints are in ignored `results.json`):

| Input | SHA-256 |
| --- | --- |
| Configuration | `6e969bd4de580f499b1071b9cd8ab4ea62cca1d85254d11cdb0f3f57829fef3c` |
| 2023/24 training X | `d9603bc12e1d7448232c9f9087b9c9371695d3dbd66f99e9fb0f8c9accec15fa` |
| 2023/24 points target | `179c77f8c75226194dd0d2e9e663b927774e20fa16cac69a6f58c152d7e3d98a` |
| 2024/25 validation X | `5ecd2dc827fdb0851f24a334830b42711e0d4ebfbe60781ef403ef8d689361f0` |

## Components and nonappearance contribution

**Appearance** means summed recorded GW minutes > 0, including any DGW leg. Logistic regression
uses L2 regularisation, C=1, LBFGS, tolerance `1e-6`, maximum 1,000 iterations and no class weighting.
It converged after 774 iterations. **Conditional points** use Ridge alpha=100, Cholesky solver,
fitted on 10,746 appearing training GWs. Reference Ridge uses its unchanged alpha=100 configuration
on all training rows. Logistic regression configuration follows the
[scikit-learn API](https://scikit-learn.org/1.7/modules/generated/sklearn.linear_model.LogisticRegression.html).

For each pre-deadline X:

`expected_points = p_appearance * conditional_points + (1 - p_appearance) * nonappearance_mean`

Zero minutes does **not** imply zero FPL points: disciplinary events can score without recorded
minutes. All 17,131 training nonappearances are retained, including four nonzero-point GWs summing
to -4. Their training-only mean is **-0.000233495** points. This is a constant approximation to
the conditional nonappearance mean, with no retrospective card information or validation-fitting.
Validation has one nonzero-point nonappearance among 15,029, with observed mean -0.000066538;
that value is diagnostic only. Setting the term to zero would change a validation prediction by
at most 0.000225567 points. Rare-event covariates cannot be estimated reliably from four events.
Tests cover p=0/p=1, mixtures, negative contributions and probability bounds. Negative predictions
are allowed mathematically; none of the full two-stage validation predictions happens to be negative.

## Points results and uncertainty

Bias is **mean(prediction minus actual)**; targets are unmultiplied player FPL points.

| Approach | N | MAE | RMSE | Bias |
| --- | ---: | ---: | ---: | ---: |
| Reference Ridge | 26,098 | **1.034843** | **1.972911** | +0.025955 |
| Logistic + conditional Ridge | 26,098 | 1.040613 | 1.977896 | +0.029954 |

Paired moving-block bootstrap: 1,000 seeded draws of contiguous four-GW blocks from the 37
observed validation GWs (2-38). Each draw retains paired predictions and all player rows in its
selected GWs; metrics are row-weighted and RMSE is recomputed from sampled squared errors.

| Two-stage minus Ridge | Difference | Approximate 95% interval |
| --- | ---: | --- |
| MAE | +0.005770 | [+0.004603, +0.007658] |
| RMSE | +0.004985 | [+0.000083, +0.008679] |
| Bias | +0.003999 | [+0.001166, +0.007024] |

The MAE interval suggests a consistent small deterioration within this validation season.
The RMSE lower bound is close to zero. These are approximate dependence-aware intervals,
not a full model of repeated players, clubs, overlapping histories or cross-season shifts.
They do not establish how either approach will perform on a new season. 2024/25 was already
used for earlier model selection, so this is an exploratory validation comparison.

Cells below are **MAE / RMSE / bias**. Official-season position labels are verified by
season + element + player code, as in Phase 2b; none is UNKNOWN. Position labels and actual
appearance status are **retrospective diagnostic groups**, never predictors or row exclusions.

| Group | N | Ridge | Two-stage |
| --- | ---: | --- | --- |
| GK | 2,745 | 0.7455 / 1.6428 / -0.0563 | 0.7502 / 1.6487 / -0.0597 |
| DEF | 8,756 | 1.0382 / 1.8834 / +0.0036 | 1.0344 / 1.8886 / -0.0118 |
| MID | 11,737 | 1.0605 / 2.0294 / +0.0760 | 1.0758 / 2.0365 / +0.0963 |
| FWD | 2,860 | 1.1972 / 2.2732 / -0.0319 | 1.1940 / 2.2693 / -0.0286 |
| Actual nonappearance | 15,029 | 0.3835 / 0.7980 / +0.3786 | 0.3778 / 0.7859 / +0.3778 |
| Actual appearance | 11,069 | 1.9193 / 2.8832 / -0.4528 | 1.9406 / 2.8957 / -0.4423 |
| Pre-deadline regular starters | 5,300 | 2.3273 / 3.1385 / +0.0233 | 2.3417 / 3.1463 / +0.0676 |

Regular starters are defined **before seeing target-GW minutes**: five eligible prior observed
GWs, start rate >=0.8 and mean summed GW minutes >=60. Missing history fails this subgroup rule.
These are observed GW windows, not calendar windows; starts mean any start in an eligible GW.
Ridge is better on regular starters and midfielders. Small DEF/FWD MAE improvements do not
demonstrate robust position-specific superiority; no subgroup-driven model selection is performed.

| Overall error distribution | Ridge | Two-stage |
| --- | ---: | ---: |
| Absolute error p50 / p90 / p95 | 0.352 / 2.761 / 4.222 | 0.304 / 2.799 / 4.289 |
| Error within 1 point | 68.74% | 69.30% |
| Error within 2 points | 83.80% | 83.78% |

The two-stage median improves, while upper-tail errors worsen. Predicting an expectation
optimises squared error in theory; MAE also reflects the distribution's zero-heavy median.

## Appearance and expected-point calibration

| Appearance predictor | Brier | Log loss | ECE, 10 fixed bins | Mean probability |
| --- | ---: | ---: | ---: | ---: |
| Logistic | **0.102030** | **0.336715** | 0.032657 | 0.421364 |
| Prior five observed-GW appearance rate | 0.111903 | 1.803089 | 0.061562 | 0.428023 |
| Training prevalence constant | 0.245738 | 0.684709 | 0.038653 | 0.385479 |

Actual validation appearance rate is **0.424132**. The lagged-rate baseline is unsmoothed and
often exactly 0 or 1: log-loss clips only at machine epsilon for arithmetic, so its large log
loss is sensitive to that convention. Brier is the more useful direct comparison. A missing
lagged rate falls back to training prevalence; no candidate in this revision needs that fallback.
ECE is a count-weighted absolute calibration gap in fixed probability bins; it depends on binning.

| Logistic probability band | N | Mean prediction | Appearance rate |
| --- | ---: | ---: | ---: |
| [0.0, 0.1) | 10,973 | 0.0563 | 0.0382 |
| [0.1, 0.2) | 1,829 | 0.1452 | 0.2247 |
| [0.2, 0.3) | 999 | 0.2442 | 0.3584 |
| [0.3, 0.4) | 763 | 0.3494 | 0.4705 |
| [0.4, 0.5) | 641 | 0.4489 | 0.5226 |
| [0.5, 0.6) | 691 | 0.5508 | 0.6281 |
| [0.6, 0.7) | 821 | 0.6509 | 0.6626 |
| [0.7, 0.8) | 1,251 | 0.7541 | 0.7434 |
| [0.8, 0.9) | 2,526 | 0.8578 | 0.8246 |
| [0.9, 1.0] | 5,604 | 0.9439 | 0.9272 |

Despite good average prevalence, intermediate probabilities underpredict appearance and high
probabilities overpredict it. These are reliability diagrams comparing mean predicted and observed
frequencies, following the [scikit-learn calibration definition](https://scikit-learn.org/1.7/modules/generated/sklearn.calibration.calibration_curve.html).

Expected-point bands are fixed in the configuration. Each model forms its own bands;
these are calibration diagnostics, not identical-row subgroup comparisons. Cells are **N;
mean prediction / actual points**.

| Predicted points band | Ridge | Two-stage |
| --- | --- | --- |
| Below 0 | 658; -0.0675 / 0.1201 | 0; unavailable |
| [0, 0.5) | 12,137; 0.1046 / 0.0959 | 13,565; 0.1342 / 0.1266 |
| [0.5, 1) | 2,037; 0.7448 / 0.7393 | 1,662; 0.7340 / 0.9332 |
| [1, 2) | 3,784; 1.5093 / 1.4701 | 3,390; 1.5128 / 1.4593 |
| [2, 3) | 4,434; 2.4940 / 2.4475 | 4,360; 2.4864 / 2.4729 |
| [3, 4) | 2,176; 3.4079 / 3.3470 | 2,075; 3.4193 / 3.2699 |
| [4, 6) | 814; 4.5902 / 4.3673 | 943; 4.6088 / 4.2778 |
| [6, infinity) | 58; 6.9659 / 6.7069 | 103; 7.0097 / 5.5825 |

Count-weighted absolute band gaps: **0.035415 Ridge versus 0.055324 two-stage** points.
The two-stage high-prediction tail overpredicts markedly, although its top band is small.

## What each component contributes

Two fixed diagnostic substitutions were declared alongside the model, not selected or tuned:

| Expected-point diagnostic | MAE | RMSE | Bias |
| --- | ---: | ---: | ---: |
| Lagged appearance rate + conditional Ridge | 1.040918 | 1.995572 | +0.057293 |
| Logistic + training appearance-points mean (2.828960) | 1.091660 | 2.036163 | +0.027391 |
| Full logistic + conditional Ridge | 1.040613 | 1.977896 | +0.029954 |

With the same return model, logistic improves RMSE over the lagged rate by **0.017676**, but
MAE by only **0.000305**. With the same logistic probabilities, predicting conditional returns
instead of a constant improves MAE by **0.051047** and RMSE by **0.058267**. On actual appearances
alone, the conditional Ridge has MAE/RMSE **2.0291/2.8324**, against **2.1704/2.9679** for the
training appearance mean. Thus both components carry useful information relative to their simple
references; their product still loses to unconditional Ridge. This is an ablation association,
not a causal decomposition, and the conditional scores cannot be compared directly with
unconditional Ridge scores as estimates of the same conditional mean.

All coefficients and intercepts are saved locally. Largest absolute standardised logistic terms
include last-three-GW starts sum (-1.158 log odds), last-three-match minutes mean (+0.929) and
last-three-match starts sum (-0.870). Conditional Ridge includes season-to-date xGC (-0.560
points), BPS (+0.387) and points (+0.344). Counterintuitive signs among overlapping sums/means
are consistent with strong collinearity; individual coefficients are not causal football effects.

## Runtime, limits and next experiment

Measured on this machine, shared preprocessing took **0.155 s**, reference Ridge fit **0.028 s**,
logistic fit **1.775 s**, and conditional Ridge fit **0.016 s**. Inference on 26,098 rows took
**0.042 s** for shared preprocessing, **0.0032 s** for Ridge, **0.0064 s** for both stages and their
combination. Full loading, fingerprinting, fitting, evaluation and saving took **8.05 s**.
These single-run timings are not performance benchmarks; existing pinned packages suffice.

The [readiness limits](TRAINING_READINESS.md) still apply: final corrected outcome vintages,
24-hour publication proxy, missing historical injuries/availability, upcoming schedule/blank/DGW
knowledge and roster timelines. The model predicts any recorded GW appearance, not full minutes,
60-minute eligibility or official autosub participation. Conditional returns mix brief cameos,
regular starts and doubles, which is a plausible source of remaining error, not established causation.
The two-stage model still overpredicts nonappearances by about 0.378 points on average.

Zero-minute rows are retained. Cold starts/core gaps exclude 865 training and 784 validation rows;
all 38 Ferguson GWs remain quarantined with the original 27-versus-28 source discrepancy, one
overlapping a cold start. No unknown roster/blank rows are manufactured. Retrospective quality
exclusion and position verification remain evaluation limitations, not additional predictors.
One training season and an already-used validation season cannot establish fresh superiority;
no fair production-simulator comparison is claimed.

**Recommended next experiment, not implemented:** assess conditional playing-time heterogeneity
(cameo versus sustained minutes) using only the same eligible historical features, with calibration
and any regularisation choices fitted in chronological inner folds of 2023/24. Predeclare a small
comparison against Ridge before reusing 2024/25; reserve genuinely new, prospectively collected
data for the final evaluation. A well-calibrated appearance component alone is insufficient.

## Outputs and checks

Commands are in [training/README.md](../training/README.md). Ignored `ml/artifacts/two-stage-v1/`
holds fitted models, preprocessing/component metadata, coefficients, full validation predictions,
hashes, metrics CSV, calibration tables and verification. The two inspected plots are
[appearance and points calibration](../artifacts/two-stage-v1/calibration.png) and
[Ridge/two-stage comparison](../artifacts/two-stage-v1/model_comparison.png); recreate them with
the `report` command after checkout. They are intentionally local generated outputs.

**58 focused ML tests passed**, including 15 new tests for the season gate, future/DGW leakage,
probability bounds, missing-history treatment, total expectation, training-only nonappearance
mean, regular-starter selection, paired intervals and deterministic save/reload. Verification
reproduced all 26,098 predictions and evaluation metrics, and matched previous Ridge validation.
Existing prepared-data integrity validation also passed: 86,755 retained matches, 84,999
player-GWs, 90,797 preserved source rows, 114 verified deadlines and 1,756 DGW player-GWs;
the 38 Ferguson exclusions are unchanged.
