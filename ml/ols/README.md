# A small OLS experiment for learning

Predict an individual player's **next Gameweek's unmultiplied FPL points** from 11
simple regressors. This folder is isolated from the existing Ridge/statistical
models, training pipelines, private Assistant and live website. There are no
production imports, routes, selectors or new runtime dependencies.

Read the generated [historical backtest report](reports/REPORT.md) for the fitted
equation, all coefficients, 3 real-player contribution breakdowns, plots, overall
and per-Gameweek errors. It uses the completed 2023/24, 2024/25 and 2025/26 archive;
no 2026/27 results or future waiting are needed.

## Run from the repository root

Use Python 3.12 or 3.13 with the pinned research dependencies. PowerShell commands
for an existing research environment:

```powershell
.\ml\.venv\Scripts\python.exe -m pip install -r ml/ols/requirements.txt
.\ml\.venv\Scripts\python.exe -m unittest ml.ols.test_ols -v
.\ml\.venv\Scripts\python.exe -m ml.ols.run develop
.\ml\.venv\Scripts\python.exe -m ml.ols.run evaluate
```

For a fresh environment, run `py -3.13 -m venv ml/.venv` first. On other platforms
use `ml/.venv/bin/python`. Dependencies are numpy (arrays), pyarrow (the existing
Parquet data), scikit-learn (OLS) and matplotlib (standalone scientific plots).
Do not install the broader training requirements or LightGBM just for this model.

The cached dataset already exists in this checkout. If setting up a fresh checkout,
reuse the existing preparation **without modifying it**:

```powershell
.\ml\.venv\Scripts\python.exe -m ml.prepare run
```

That optional preparation step fetches checksum-locked public historical data and
archived official deadlines into ignored `ml/data/`. The OLS commands themselves
are offline, read the resulting Parquet files, recheck provenance and never write
to those inputs. `--data-dir PATH` selects another prepared cache (with its sibling
`raw/` provenance cache); it does not change the fixed season splits.

## What happens in the experiment

1. **Fix the design:** `protocol.json` declares the split, three-GW window and three
   historical examples. The feature whitelist and definitions are in `data.py`.
   This v1 design was fixed before its first fit. There is no tuning or model search.
2. **Develop separately:** `develop` reads only 2023/24. It fits GWs up to 28 and
   checks later GWs in that season. Output goes to ignored `development/`. This is
   a calculation/chronology check, not permission to tune against the later seasons.
3. **Fit once:** `evaluate` fits all eligible 2023/24 rows using
   `LinearRegression().fit(X_train, y_train)`. Every row has equal weight. No
   scaling, imputation, regularisation, hyperparameter tuning or clipping is used.
4. **Backtest chronologically:** for each observed 2024/25 and 2025/26 player-GW,
   use only that player's earlier-GW records whose kickoff plus 24 hours is strictly
   before its verified deadline. Update the available history as time advances;
   keep coefficients fixed. All training outcome readiness times must precede
   the first evaluation deadline. This also purges any late training labels in
   the development split.
5. **Compare:** evaluate actual event points against OLS and the same eligible
   recent-points-average baseline on identical rows. Report MAE, RMSE and R²
   overall, by GW and in post-outcome zero/positive-minute diagnostic groups.

**Evaluation honesty:** 2024/25 is held out from coefficient fitting; 2025/26 is
an additional retrospective comparison. Both seasons were already exposed in
earlier repository experiments. Neither can now be presented as an untouched final
holdout. This model was not revised in response to either season's OLS scores.
For a future final test, declare a separate unseen period and freeze the code,
data rules and protocol before inspecting that period's results; evaluate it once
after development is complete. Do not relabel this report as that final test.

## Inputs in plain English

Seven numbers describe recent goals, FPL assists, official xG, official xA, minutes,
starts and FPL points. For each number, sum eligible fixtures inside each historical
GW, then average the latest **up to three observed GWs**. Thus two matches in a DGW
belong to one historical GW; zero-minute records count; one or two available GWs
are not padded with zeros. Missing/blank GWs are unknown and are not filled in.

Three 0/1 position indicators (DEF/MID/FWD) come from the last eligible historical
record; GK is the reference. A fourth position indicator plus the intercept would
be exactly redundant. The last regressor is days from the latest eligible kickoff
to the prediction deadline. No full-season cumulative snapshot, target-GW club,
target minutes, final upcoming opponent, fixture difficulty, injury or source xP
enters the feature matrix. The report defines and motivates every regressor.

**Why omit opponent strength and home/away?** The archive has final fixtures but
does not establish the schedule actually published at every historical deadline.
Using the final schedule would leak postponements/rearrangements. These are useful
future candidate inputs only after acquiring verified historical fixture snapshots.

The archive includes finalized/revised stats, not their original publication
vintages. A 24-hour availability buffer is an assumption, not proof of publication.
Verified deadline gating prevents future-match/target-GW leakage, but cannot reverse
later stat revisions. Cold starts are excluded explicitly; no same-season history
means no prediction. Missing selectable-player/blank-GW rows are not reconstructed.
The existing retrospective data-quality quarantine is reused as an evaluation
filter, never as an input. Read the report's coverage table before generalising.

## How OLS learns and predicts

Think of each regressor as a column in a spreadsheet, each player-GW as a row, and
actual next-GW points as the answer column. OLS chooses an intercept and one weight
per column **jointly**, making the sum of squared training prediction errors as
small as possible. Squaring means that a miss of 6 counts nine times as much as a
miss of 2. There is no penalty that shrinks weights toward zero.

With an intercept column in X and full column rank, the familiar coefficient
formula is `(X.T @ X)^-1 @ X.T @ y`. The library solves least squares numerically;
the code does not explicitly invert that matrix.

To predict, start at the intercept, multiply every input by its coefficient and
add all those contributions. For example, a coefficient of 0.02 on minutes means
ten more recent minutes adds 0.2 predicted points **if the other inputs stay fixed**.
That is an illustration, not the fitted coefficient: use `reports/model.json` or
the report for the actual estimate. Predictions are expected points, so fractions
are normal. Negative values are possible and are deliberately retained.

Coefficients measure conditional associations, not causal effects or independent
feature importance. Minutes/starts and goals/xG/points overlap (multicollinearity),
so their fitted weights can be unstable or have surprising signs. More features
can fit training noise (overfitting). The chronological evaluation and simple
baseline show whether the fitted line generalises, without claiming that it will
predict rare hauls, unexpected absences or new scoring rules well.

MAE answers "how many points wrong, on average?" RMSE gives big errors more weight.
R² compares squared error with a constant evaluation-mean prediction: 1 is perfect,
0 matches that reference, and negative is worse. It is not a percent-correct score.

## Files and outputs

| File | Purpose |
| --- | --- |
| `data.py` | Deadline checks, 11-feature construction, keyed alignment, chronological splits |
| `run.py` | The OLS fit, baseline, errors, coefficients, contribution tables and plots |
| `test_ols.py` | Lightweight offline timing/alignment/calculation regression tests |
| `protocol.json` | Fixed experiment decisions and preselected examples |
| `reports/REPORT.md` | Reviewable completed historical report |
| `reports/overall_metrics.csv`, `reports/gameweek_metrics.csv` | Full-precision scores; n=0/undefined GW1 is explicit |
| `reports/model.json`, `reports/coefficients.csv` | Portable full-precision intercept/ordered coefficients and definitions |
| `reports/examples.json` | Full input values and every contribution for the 3 players |
| `reports/diagnostics-*.png` | Scatterplots and residual distributions |
| `reports/manifest.json`, `reports/diagnostics.json` | Reproducibility hashes, versions, splits, training/correlation/participation diagnostics |
| `outputs/evaluate/predictions.csv`, `outputs/evaluate/coverage.csv` | Ignored detailed per-player predictions and exclusion reasons |
| `development/`, `outputs/develop/` | Ignored development-only outputs |

Generated small reports are committed for review; raw data and detailed prediction
exports remain outside Git. Re-running regenerates the same experiment; the saved
manifest identifies any changed protocol, code or prepared inputs. Model JSON is
for inspection and arithmetic, not an application inference integration.

Method reference: [scikit-learn's OLS guide](https://scikit-learn.org/1.7/modules/linear_model.html#ordinary-least-squares).
Data credit: [Vaastav Anand's FPL historical archive](https://github.com/vaastav/Fantasy-Premier-League),
using the existing repository's pinned source and archived official deadline provenance.
