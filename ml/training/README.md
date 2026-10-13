# First baseline experiments — Phase 2b

Use Python 3.12/3.13 in the isolated ML environment, from the repository root:

```powershell
ml/.venv/Scripts/python.exe -m pip install -r ml/training/requirements.lock.txt
ml/.venv/Scripts/python.exe -X utf8 -m unittest discover -s ml/tests -v
ml/.venv/Scripts/python.exe -X utf8 -m ml.training.experiment select
ml/.venv/Scripts/python.exe -X utf8 -m ml.training.experiment test
ml/.venv/Scripts/python.exe -X utf8 -m ml.training.experiment report
ml/.venv/Scripts/python.exe -X utf8 -m ml.tests.verify_experiment
```

On Linux/macOS use `ml/.venv/bin/python`. The base Phase 2a prepared data and cached deadline
proofs must already exist; run `ml.prepare run` first if needed. `requirements.txt` records direct
dependencies; `requirements.lock.txt` pins the complete tested stack. Nothing is added to the
application's dependencies. The research environment uses CPU only and caps workers at two.

`configs/baselines-v1.json` defines one upfront configuration per approach: recent three eligible
observed-GW points average, Ridge alpha=100 and a 200-tree/15-leaf/depth-4 LightGBM L2 regressor.
There is no hyperparameter search, early stopping, clipping or outcome-driven feature selection.
`data.py` reads season-filtered, explicitly whitelisted inputs. Only the existing 216 approved
predictors can enter X; target/quality/position/observed minutes are separate metadata.

`select` fits preprocessing/models on 2023/24 and scores 2024/25, then writes the validation
choice and config/code/data/version fingerprints. `test` verifies those fingerprints, refits all
fixed approaches on 2023/24+2024/25, persists models and a freeze marker, then loads the 2025/26
population for prediction/scoring. It never passes validation/holdout arrays to LightGBM early
stopping. The test reports all three approaches without changing the validation-selected winner.

Preprocessing drops training-empty/constant columns, learns training medians and standardises
using training means/scales. The same transform serves Ridge and LightGBM. Held-out values cannot
activate previously dropped columns or change medians/scales. Median filling is a modelling
transform, not a change to the original data. Existing coverage indicators remain the only
missingness predictors; no additional unapproved fields are introduced.

Default outputs are ignored under `ml/artifacts/baselines-v1/`:

```text
selection.json / holdout_started.json / test.json  # frozen protocol, metrics, provenance, times
validation_models/ / final_models/                # preprocessing.json, ridge.joblib, lightgbm.txt
                                                 # recent_average.json, all coefficients/importances
validation_predictions.parquet / test_predictions.parquet
metrics.csv / comparison.md                       # full subgroup/error/uncertainty tables
comparison.png / error_distributions.png          # static exportable charts
```

Re-selection/retesting in a run with a holdout marker is refused. For an exact reproduction use
the unchanged config/code/data/versions and a **fresh** `--output-dir` on both `select` and `test`.
This is a reproduction, not permission to tune against revealed 2025/26 outcomes. `report` and
`verify_experiment` only use the fixed run and never refit. The verification command reloads own
locally generated models, checks hashes/selection and reproduces every validation/test prediction.
One original rendering source is preserved locally when only chart layout is corrected after
scoring; fitting/data/metrics hashes and frozen fitted models remain unchanged.

The retrospective dataset, 24-hour availability proxy, unknown revisions, cold starts, Ferguson
quarantine, position-group verification and consumed holdout are discussed in
[`BASELINE_EXPERIMENTS.md`](../reports/BASELINE_EXPERIMENTS.md). No production engine adapter or
prediction integration is provided. For future modelling, propose a new validation experiment
before implementation and reserve genuinely new data for its final test.

## Appearance and conditional points - Phase 2c

The fixed logistic/Ridge experiment uses **2023/24 fitting and 2024/25 validation only**.
2025/26 is a consumed historical reference; there is no new test command. Its reference Ridge
uses exactly the Phase 2b validation configuration, population and preprocessing. See
[`TWO_STAGE_EXPERIMENT.md`](../reports/TWO_STAGE_EXPERIMENT.md) for actual results and limitations.

```powershell
ml/.venv/Scripts/python.exe -X utf8 -m ml.training.two_stage_experiment run
ml/.venv/Scripts/python.exe -X utf8 -m ml.training.two_stage_experiment verify
ml/.venv/Scripts/python.exe -X utf8 -m ml.training.two_stage_experiment report
ml/.venv/Scripts/python.exe -X utf8 -m unittest discover -s ml/tests -v
```

`configs/two-stage-v1.json` fixes logistic C=1 and conditional Ridge alpha=100; no search,
class weighting, clipping or validation-fitted calibration. Shared preprocessing fits only
2023/24. The return model fits only appearing training rows. A training-only mean of all
zero-minute points supplies the nonappearance contribution in the total-expectation formula.
Prediction functions accept X alone, with outcome/minutes/position/quality metadata kept separate.
The last-five-GW rate and constant-return substitutions are fixed diagnostics, not tuned models.

Default ignored outputs in `ml/artifacts/two-stage-v1/`:

```text
started.json / results.json / verification.json  # fixed config, data/code/source hashes, metrics, times
models.joblib                                  # shared preprocessor, logistic, conditional/reference Ridge
preprocessing.json / components.json           # train-fitted values, intercepts and nonappearance mean
coefficients.csv / validation_predictions.parquet
comparison.md / metrics.csv                    # positions, retrospective minutes groups, starter rule
calibration.png / model_comparison.png / render_metadata.json
```

`run` refuses an existing run marker. To reproduce the fixed experiment, use a fresh ignored
directory such as `--output-dir ml/artifacts/two-stage-v1-reproduction` on each command.
`verify` checks configuration/code/data/package/artifact fingerprints, reloads locally produced
models, reproduces every validation prediction and metric, and optionally matches the saved
Phase 2b **validation** Ridge predictions. It never accesses the Phase 2b test outputs.
`report` only renders saved results; rendering changes are separately fingerprinted.
There is no prediction integration or deployment.
