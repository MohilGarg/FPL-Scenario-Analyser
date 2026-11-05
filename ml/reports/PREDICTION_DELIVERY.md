# Prediction-system delivery — 5 November 2026

**Follow-up sprint:** [fitted minutes and controlled points evidence](MINUTES_SPRINT.md),
[selection sources and disagreements](SELECTION_INPUTS_SPRINT.md), and
[verified collection activation](COLLECTION_STATUS_SPRINT.md). PRs #2 and #3 are merged; collection is
active independently of the draft research PR. The new selectable private fitted/empirical
challengers improve historical minutes, but do not establish better candidate points on the
three original captured deadlines. The statistical default and promotion rule are unchanged.
Current local validation passes **364 application tests, 94 ML tests**, the frontend build,
JavaScript syntax, changed-file lint and actual Edge desktop/mobile/theme/auth interactions.
The new immutable 670-player GW10 release includes all six internal methods and replays exactly
offline; the main collector has executed the fitted-model pin successfully with 95 runtime checks.
The earlier delivery measurements below are retained as the original sprint record.

The working branch implements prospective evidence, external ingestion, private minutes-state
forecasts and honest chronological comparisons. The public analyser and authentication boundary
are preserved; no forecast default is promoted.

## Read the evidence

- [Paired historical benchmark, OLS equation, Ridge search, minutes ablations and promotion rule](PREDICTION_BENCHMARK.md).
- [Real immutable GW7–9 prospective results and cutoff limitations](PROSPECTIVE_BENCHMARK_20261105.md).
- [External source register, access, definitions, attribution and blockers](EXTERNAL_SOURCES.md).
- [Durable collection, release verification, cutoffs and recovery](PROSPECTIVE_RECORDING.md).
- [Private interface, experiments, deployment and limitations](../../ASSISTANT.md).

The original 54,595 validation/consumed-test predictions were reproduced. New chronological
experiments score 26,881 development player-GWs, including cold starts. Ridge tuning produces
essentially unchanged points RMSE. Minutes-state proposals improve full-population minutes RMSE
by 3.03 minutes (paired 95% interval −3.39 to −2.80), but regular-starter accuracy barely changes
and their appearance calibration worsens. Prospective GW7–9 do not establish a Ridge or fixture
model improvement. These are measured limitations, not reasons to silently change a default.

On 5 November, the first official/FPL Cat/Sideline captures each mapped all 670 current players
for GW10. These are prospective forecasts awaiting settled official outcomes. External historical
availability has not been proved; no newly downloaded data is backdated. Provider forecasts,
consensus, final schedules and observed playing time are never substituted for official targets.

## Reproduce

Use Python 3.12+ and the existing pinned isolated research environment. On Windows, replace
`python` below with `.venv/Scripts/python.exe` for application checks and
`ml/.venv/Scripts/python.exe` for offline scientific work. On Linux use the corresponding `bin/python`.

```powershell
$env:PYTHONPATH='src;.'
python -m ml.external_forecasts collect
python -m ml.prediction_benchmark reproduce --output-dir ml/artifacts/prediction-reproduction
python -m ml.prediction_benchmark run --output-dir ml/artifacts/prediction-development
python -m ml.evaluate_playing_time --output ml/artifacts/prediction-development/playing_time.json
python -m ml.report_prediction_benchmark --directory ml/artifacts/prediction-development
python -m ml.recording check
python -m ml.recording evaluate --cutoff t24h
python -m ml.recording evaluate --cutoff final
python -m unittest discover -s tests -v
python -m unittest discover -s ml/tests -v
python frontend/build.py --output frontend/dist
python frontend/check_assistant.py --browser-executable 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
```

Use a fresh benchmark output directory. Preparation prerequisites and locked dependencies are in
[training/README.md](../training/README.md). Durable release inspection requires the existing
GitHub CLI authentication; scheduled publication uses its scoped workflow token. Local source
collection needs no Assistant password. Source ingestion failures remain isolated. The private
web runtime adds no scientific dependencies or request-time training.

Validation on the delivered code: **348 application tests and 91 isolated ML tests pass**;
the frontend build, JavaScript syntax and changed-file Ruff checks pass. The actual Edge check
covers authentication/CSRF/logout, experimental selectors, lineups, transfers/chips, mobile and
desktop layouts, and both themes. A fresh real GW10 capture mapped all 670 players and replayed
statistical, Ridge, fixture-component and minutes forecasts exactly offline. That local original
capture remains ignored; production cron and restart-independent GW7–9 release archives were
verified separately. New workflow behavior starts after merge.

## Remaining experiments

Historical roster registration, injury vintages, prices, lineup publication and corrected-source
vintages are not proven. Consequently strict historical injury/price groups, future-horizon
availability, new-player selection and multi-GW external comparisons remain unmeasured.
FPL Form permits personal imports; FPL Review lacks a verified bulk export/timestamp contract.
No access controls are bypassed. Market odds and additional underlying-statistics feeds were not
added without verified timestamp/rights contracts.

External mean/median and past-only shrunk weights are implemented, but source ablations need
future common-cutoff outcomes. No external joint distribution is fabricated from point means.
Current football dependencies remain shared matches, compatible scorer/assist allocations,
clean sheets, legal lineups and a new DGW availability state. Rare-event/bonus models,
cross-competition congestion, dynamic injury recovery, decision-level calibration and adaptive
Monte Carlo sampling still need evidence. The benchmark's explicit promotion gate prevents these
experiments from becoming defaults prematurely.
