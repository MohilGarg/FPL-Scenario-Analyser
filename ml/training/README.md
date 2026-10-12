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
