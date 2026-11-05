# Fitted playing-time sprint - 5 November 2026

Two selectable private challengers now change playing time inside the shared football simulator:
**Fitted minutes** and **Tuned empirical minutes**. They do not change the statistical default.
The historical reliable-starter improvement is measurable in minutes, but the points gain is too
small to satisfy the existing promotion rule. No captain/transfer decision improvement is established.

## What is fitted and what is held fixed

`ml/train_minutes_model.py` fits five mutually exclusive states: absent, short substitute,
60-minute substitute, short start and 60-minute start. Past 3/5/10-match selection, durations,
nonappearances and position enter a regularised multinomial model. An independently tuned
recency-weighted empirical challenger uses the same state/duration contract. Cold priors and
position/state duration distributions come only from training observations. Runtime inference is
portable standard-library Python; the private web application does not fit models or import sklearn.

Four expanding outer blocks cover 2024/25 GW1-10, 11-20, 21-29 and 30-38. Each uses two expanding
inner past-deadline folds to select regularisation, temperature, recency/prior strength and duration
shrinkage. Entire training deadline groups must have their last fixture kickoff +24h before the
cutoff. State selection weights reliable starters 70% and the full population 30%; duration selection
uses the corresponding minutes MSE. No 2025/26 outcomes were read for this sprint's fitting.
2024/25 has already been consumed for development; it is not an untouched confirmation season.

Next-GW official availability caps historical appearance once, rather than independently multiplying
another historical nonappearance probability. This remains an experimental combination. Availability
is shared across both legs of a Double Gameweek. Later means for currently flagged players are
unknown, with separately labelled conditional-if-available scenarios; no injury return date is
inferred. Invalid/missing history retrieval falls back per player; a genuine empty valid history
uses the learned cold prior. A future conditional estimate cannot overwrite the next-GW cap.

Pivotal starter rounding preserves feasible marginal start probabilities and projects overfull
ones to one goalkeeper/ten outfield slots. Existing shared substitution constraints then apply.
Anonymous players fill unused slots. This is a capacity-constrained joint model, not a confirmed
formation/lineup feed. An original GW10-input diagnostic (128 samples) produced 10.526 named
starters/team and 5.80% anonymous goals despite full official roster coverage. That is a remaining
allocation/calibration limitation, not evidence to force every historical player into a known XI.

## Diagnose the minutes failure

The raw fixture-level development population contains 27,245 rows, including zero appearances.
The strict reliable-starter group has five eligible prior observations, mean minutes >=60 and
start rate >=80%; it contains 5,348 fixture rows. These raw proposals are measured before legal
lineup/substitution sampling. Full calibration bins, conditional-duration errors, bias, tails,
search grids and deadline-block intervals are in [MINUTES_MODEL_SPRINT.json](MINUTES_MODEL_SPRINT.json).

| Raw minutes RMSE | Statistical | Original minutes v1 | Tuned empirical | Fitted |
| --- | ---: | ---: | ---: | ---: |
| All eligible rows | 30.889 | 27.858 | 24.090 | 23.187 |
| Reliable starters | 29.767 | 29.745 | 28.641 | 27.891 |
| Rotation prone | 37.894 | 36.834 | 34.129 | 32.574 |
| Recent substitutes | 31.538 | 31.262 | 31.092 | 29.806 |
| Sparse history, 1-3 matches | 38.375 | 34.213 | 22.533 | 21.544 |
| Cold, no observations | 52.113 | 52.113 | 37.680 | 37.680 |

For reliable starters, appearance/start/60-minute Brier scores improve from
0.0911/0.1371/0.1554 to **0.0807/0.1175/0.1407**. Original v1 barely changes their minutes,
and worsens appearance calibration. Selection/duration learning helps; full-population gains are
also heavily influenced by sparse/cold rows. They do not establish reliable transfer decisions.

The 33 retrospective club-change diagnostic rows worsen from about 40.90 to 44.83 minutes RMSE
with the fitted method. Target-club identity is used only to label this diagnostic, not to predict
selection or reconstruct a registration date. The training player uses the last historically known
club. Runtime club changes explicitly carry selection uncertainty. Historical injury-vintage groups
cannot be invented; the separately timestamped GW7-9 availability audit is described in
[SELECTION_INPUTS_SPRINT.md](SELECTION_INPUTS_SPRINT.md).

## Controlled points comparison

The historical joint experiment freezes the same as-of football rates and season-correct scorer
across methods. The original points-benchmark candidate rule is preserved exactly: at least one
eligible prior match, last-five mean minutes >=60 and start rate >=80%. Its **6,013 player-GW keys
match the original prepared benchmark exactly**. The stricter five-observation group is reported
separately (5,266 player-GWs). The full population has 26,881 player-GWs.

These controls use historical appearance-informed statistical rates, retrospective source-roster
and final-fixture proxies, and neutral historical availability. They are not an exact reconstruction
of the production season-total-only baseline. The separate captured-input replay below checks
production inputs without those proxies. Football/scoring are held fixed within each comparison.

Each historical deadline uses 512 joint draws with two predeclared independent seeds. Results are
in [JOINT_MINUTES_SPRINT.json](JOINT_MINUTES_SPRINT.json), including every subgroup, components,
calibration, distribution scores, raw-versus-constrained minutes and independent-seed contrasts.

| Original candidate group, seed 20261105 | Points RMSE | Points MAE | Points bias | Minutes RMSE | CRPS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Statistical control | 3.11264 | 2.27764 | -0.0630 | 30.108 | 1.51090 |
| Original minutes v1 | 3.10866 | 2.26310 | -0.0944 | 30.179 | 1.51213 |
| Tuned empirical | 3.10875 | 2.24664 | -0.1342 | 28.897 | 1.50487 |
| Fitted + original greedy sampling | 3.10772 | 2.26176 | -0.0876 | 28.096 | 1.49845 |
| Fitted + pivotal sampling | **3.10222** | **2.24561** | -0.1220 | **27.978** | **1.49600** |

The fitted/pivotal candidate RMSE difference is **-0.01042 points** (95% four-deadline-block bootstrap
interval -0.02023 to -0.00067), only **0.335%**. It improves 21/37 candidate deadlines, below 60%.
The second seed gives -0.00847 points. Bias becomes more negative. The stricter starter group
improves 3.10498 to 3.09165, while the full population improves 2.03230 to 1.94940. These larger
full-roster gains must not substitute for candidate-level evidence.

Pivotal versus greedy candidate RMSE changes -0.00550 points in seed one but only -0.00155 in seed
two, with the latter interval crossing zero. Individual candidate means have roughly 0.18-point
between-seed RMS variation at 512 draws. The direction of the aggregate fitted-versus-control
comparison repeats, but tiny ablation gains and individual rankings remain sensitive to Monte Carlo
noise. Candidate 80% discrete intervals cover 87.31% for statistical and 87.43% for fitted, showing
overcoverage; improved CRPS alone is not proof of calibrated decision uncertainty.

## Next-component experiments and ablations

Goals dominate candidate component error; clean-sheet and assist errors also remain substantial.
A chronological recency/shrunk team-goals model was implemented and rejected: Poisson deviance
1.13601 -> 1.13986, RMSE 1.20388 -> 1.20605, and positive bias worsened. It remains offline only;
see [TEAM_GOALS_SPRINT.json](TEAM_GOALS_SPRINT.json).

A portable conditional goals/assists allocation model improves conditional exposure-weighted
deviance slightly (goals 0.37221 -> 0.37122; assists 0.39048 -> 0.38757). Actual minutes are exposure
only for conditional-rate fitting; the joint points comparison retains all nonappearances.
Its full ablation keeps fitted minutes exactly unchanged. Candidate points RMSE changes another
-0.00053 in seed one (95% interval -0.00252 to +0.00178) and **+0.00037** in seed two; MAE worsens
about 0.0059 in both. The adapter/artifact is implemented for research but is not selected in the
private UI or production collector. See [ATTACKING_RATES_SPRINT.json](ATTACKING_RATES_SPRINT.json)
and the paired ablations in the joint report. No practical points gain is claimed for this component.

## Original captured-input replay

`ml/replay_captured_minutes.py` first reproduces each original archived GW7-9 statistical forecast
at its recorded seed/sample count, then evaluates the new fitted artifacts at 1,024 draws with two
independent seeds on those same actual pre-deadline inputs. Membership is fixed by original
statistical expected minutes >=45, matching the existing prospective candidate definition.
This checks actual official availability and removes historical roster/schedule proxies. New models
were fitted during this sprint, so these are **counterfactual development scores**, not forecasts
that existed before those deadlines or an untouched prospective confirmation.

All three original statistical replays match with **maximum difference 0.0**. The final export
retraining reproduced all eight outer fold parameter files exactly; both final models have identical
numeric parameters to the original experiment, with refreshed code-hash provenance.

| Actual captured inputs, fixed 614 candidates, seed 20261105 | Points RMSE | Minutes RMSE | Appearance Brier | Points bias |
| --- | ---: | ---: | ---: | ---: |
| Statistical | **3.04118** | 27.345 | 0.07722 | -0.1976 |
| Original minutes v1 | 3.04638 | **26.875** | **0.07272** | -0.1726 |
| Tuned empirical | 3.06456 | 27.639 | 0.08033 | -0.3535 |
| Fitted + pivotal | 3.07262 | 26.916 | 0.07467 | -0.3695 |

The fitted candidate points regression repeats in the second seed (+0.00962 RMSE versus statistical,
compared with +0.03144 in seed one). Full-population RMSE instead improves 1.98527 -> 1.97499 on
2,004 rows. This reversal is why all-roster improvements cannot justify a decision claim. Three
deadlines are insufficient for a useful confirmation interval, and none count as prospective
challenger deadlines. The fitted method does **not** beat original v1 candidate minutes on these
captured inputs. Full subgroup/distribution/replica results are in
[CAPTURED_MINUTES_SPRINT.json](CAPTURED_MINUTES_SPRINT.json). The practical next step is genuine
future capture and fresh selection evidence, not promotion or fitting to these three consumed GWs.

## Reproduce and activate

Use the pinned isolated research environment and prepared datasets documented in
[training/README.md](../training/README.md), with `PYTHONPATH=src;.` on Windows (`src:.` on Linux).
Use a fresh training directory; expensive simulation runs preserve cached original draws.

```text
python -m ml.train_minutes_model --output-dir ml/artifacts/minutes-sprint-v2-final --export-runtime
python -m ml.train_team_goals
python -m ml.train_attacking_rates
python -m ml.joint_minutes_benchmark --model-dir ml/artifacts/minutes-sprint-v2-final --methods statistical minutes_v1 empirical fitted_greedy fitted_balanced fitted_attacking
python -m ml.replay_captured_minutes
python -m ml.audit_availability
python -m ml.selection_inputs
```

The exact locked artifacts and embedded model hashes are saved with future challenger captures.
Only their next-GW predictions are added to the durable release to bound storage. All six live
views, public analytics and server-side Assistant authentication retain their existing boundary.
Selectors are request-local; unsupported future means prevent complete planning totals.

The original promotion gate remains unchanged: eight future settled deadlines, >=99% roster
coverage, practical RMSE gain >=0.05 points or 2%, a paired deadline interval below zero, >=60%
deadlines improved, no material calibration/full-roster regression and verified cutoff/recovery
operation. This sprint's evidence does not meet it. See
[COLLECTION_STATUS_SPRINT.md](COLLECTION_STATUS_SPRINT.md) for actual activation and freshness,
and [SELECTION_INPUTS_SPRINT.md](SELECTION_INPUTS_SPRINT.md) for source coverage and remaining blockers.
