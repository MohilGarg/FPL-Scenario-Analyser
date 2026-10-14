# Historical FPL research — Phases 1, 2a, 2b, 2c and 3a

This area discovers, audits and prepares historical outcomes/features and runs small offline
baseline experiments. Those experiments remain isolated from the application. The live Ridge
integration now lives in `src/fpl_forfeit/assistant/`; `export_ridge.py` packages the existing
fitted model for that runtime. See [LIVE_INFERENCE.md](reports/LIVE_INFERENCE.md) for the private
Assistant integration, deployment, recording commands and limitations. No new experiment was
run for the integration.

Durable recording for the remainder of 2026/27 uses `ml/recording/` and the GitHub Actions
workflow, with immutable GitHub Release assets outside Git history. See
[PROSPECTIVE_RECORDING.md](reports/PROSPECTIVE_RECORDING.md) for scheduling, provenance, recovery
and `python -m ml.recording evaluate`. This runner never trains or changes the model.

## Run from the repository root

Use an isolated **Python 3.12 or 3.13** environment for the pinned Parquet wheel:

```powershell
py -3.13 -m venv ml/.venv
ml/.venv/Scripts/python.exe -m pip install -r ml/requirements.txt
ml/.venv/Scripts/python.exe -X utf8 -m ml.prepare run
ml/.venv/Scripts/python.exe -X utf8 -m ml.validate
ml/.venv/Scripts/python.exe -X utf8 -m unittest discover -s ml/tests -v
```

On Linux/macOS use `python3.13 -m venv ml/.venv` and `ml/.venv/bin/python`.
`pyarrow` is the only additional dependency; CSV, HTTP, validation, aggregation and tests use
the standard library. Application dependencies are not changed. `fetch` only needs Python's
standard library and can run separately from the Parquet environment.

`python -m ml.prepare fetch` downloads four revision-pinned outcome files per season (19.1 MB total)
and six pinned deadline archive/provenance files (0.6 MB total).
It caches bytes and SHA-256 checksums, resumes completed files, bounds each file to 16 MiB and
total input to 64 MiB. It never clones the source repository or fetches per-player directories.
`python -m ml.prepare prepare` verifies the cache and works entirely offline. To process a
subset use `--seasons 2023-24 2024-25`. Source updates require a deliberate edit of
`source-lock.json` and a fresh `--raw-dir`; cached revisions cannot be mixed.

## Structure and outputs

```text
ml/
  source-lock.json             # reviewed source repository, immutable commit and file list
  deadline-source-lock.json    # three archived official API captures, hashes and official checks
  requirements.txt            # isolated Parquet dependency
  sources.py                  # bounded retrieval, provenance and cache verification
  dataset.py                  # pure parsing, match mapping, GW aggregation and feature gate
  deadlines.py                # verify archived API proof and reconstruct actual deadline calendar
  readiness.py                # separate evaluation QA and rolling/statistical coverage report
  audit.py                    # column/missingness, identity and score reconciliation audit
  prepare.py                  # ingestion/preparation CLI
  validate.py                 # source fidelity and dataset checks
  fixture_features.py         # deadline-gated team form, club proxy and research-only schedules
  prepare_fixtures.py         # offline feature sidecars, coverage and validation
  fixture-config.json         # fixed history/smoothing policy; no model fitting
  fixture-source-review.json  # fixture version/provenance investigation and schedule gaps
  schemas/fixture-context-v1.json # frozen player v1 plus opt-in historical team-context inputs
  tests/                      # small offline regression tests
  training/                   # fixed Ridge/LightGBM/recent-average experiments and versioned config
  evaluation/                 # metrics, grouped uncertainty, tables and charts; no production adapter
  artifacts/                  # ignored: fitted models, preprocessing, predictions and charts
  reports/DATA_AUDIT.md        # reviewed audit, missing features and Phase 2 recommendations
  reports/TRAINING_READINESS.md # reviewed Phase 2a findings, provenance and limitations
  reports/BASELINE_EXPERIMENTS.md # reviewed actual Phase 2b validation/holdout results
  reports/TWO_STAGE_EXPERIMENT.md # Phase 2c appearance/conditional-points validation comparison
  reports/FIXTURE_FEATURES.md  # Phase 3a safe history, schedule limitations and recommendations
  reports/FIXTURE_COVERAGE.csv # small reviewed per-season/GW coverage report
  reports/generated/          # ignored: audit.md/json and readiness.md/json
  data/raw/                   # ignored: original CSV bytes and manifest.json
  data/processed/             # ignored:
    source/<season>/*.parquet  # every original field name/token, including duplicates/blanks
    match_records.parquet     # unique player-fixture records, typed outcomes and match identity
    player_gameweeks.parquet  # observed player-GW targets, fixture lists and gated history
    features.parquet          # identifiers/gate metadata plus explicit historical feature list
    deadlines.csv             # verified dates, API evidence, capture time, hashes and source URLs
    quality.parquet           # retrospective evaluation quarantine; never a predictive feature
    preparation_metadata.json # hashed calendar and source evidence for validation
    feature_columns.json      # exact allowed model inputs; identifiers are not inputs
    sample.csv                # 18 real rows, including doubles and zero-minute outcomes
    sample.json               # the same sample with the full prepared schema
```

Directories containing outputs are created automatically and excluded from Git. Custom output
paths are supported through `--raw-dir`, `--output-dir` and `--report-dir`; keep those paths ignored
too. No generated datasets, Parquet or environments should be committed.

## Dataset contract

The primary key is `(season, element, gameweek)`. `player_key` encodes `season:element`;
`player_code` is longitudinal identity metadata requiring review. Match keys are
`(season, element, fixture_id)`. `source_row` references the original CSV row (header is row 1).
Only GK/DEF/MID/FWD enter player targets; all Assistant Manager data remains in source Parquet.

`target_points` is the sum of recorded unmultiplied FPL `total_points` across the GW's fixtures.
Captaincy, chips and manager transfer hits do not apply to this player target. `observed_*`
columns are current-GW outcomes and must **never** enter a same-GW prediction. Fixture lists,
position, player names/codes and final schedule coverage are metadata, outside the feature list.
`target_complete` checks finished fixtures, non-null points and fixture coverage for the observed
club against the final schedule; it does not certify every final FPL correction or historical
roster availability. Unknown target coverage produces null, not a partial sum presented as complete.

Exact duplicates are removed only from normalized data; conflicting duplicates, invalid keys,
round/GW disagreement and fixture/opponent contradictions fail. Missing statistics remain null.
A genuine zero-minute recorded fixture remains a zero-minute observation. Missing rows, blank
Gameweeks and pre-registration weeks are not synthesized using an end-of-season player list.

## Predictive features and timing

Phase 2a's default pipeline reconstructs 114 historical deadlines from three pinned copies
of timestamped official FPL API records. Finished events provide actual past deadlines; the
imminent final event is verified from `is_next` and capture-before-deadline evidence. Future
provisional events would remain gaps. Source bytes, original Internet Archive URLs, capture
times and selected official calendar cross-checks are preserved. No kickoff-offset inference
is used. See the readiness report for the mirror-verification limitation.

When calling `gameweek_dataset` directly without deadlines, **all `pre_gw_*` values remain null**.
Prices, availability, ownership, source `xP`, final team strength and final season aggregates
are never added to the feature list. The final API archives are read only for deadline records.

The deadline CSV schema includes these core fields plus verification/provenance columns:

```csv
season,gameweek,deadline_utc,source_url
2024-25,1,2024-08-16T17:30:00Z,https://example.test/verified-calendar
```

This example illustrates the core fields only. The default calendar is generated automatically.
`--deadlines path/to/deadlines.csv` remains an explicit caller-supplied override; it is reported
as unverified and does not establish baseline readiness. Unverified provisional archive rows
must have empty usable deadlines. Missing deadlines keep affected features null. Offset-aware
dates must increase by numeric GW and precede any known target kickoff. The calendar is hashed.

For a supplied cutoff, historical features use **only earlier GWs in the same season** with a
finished fixture and a recorded kickoff **more than 24 hours before** the cutoff. Same-GW DGW
legs, later GWs and unknown kickoff times cannot enter. The 24-hour delay is a conservative
readiness proxy, not a recorded result-publication timestamp. Retrospective source corrections
remain unverified. `feature_status=deadline_supplied_results_lag_24h` states that limitation.

The feature list now includes **3/5/10-match and 3/5/10-observed-GW** sums/means for xG, xA,
minutes, starts, goals, assists and points; appearance/start rates; and season-to-date sums and
rates calculated from eligible matches. Match windows follow actual kickoff order, including
rearranged fixtures. GW windows group only eligible fixtures, then follow numeric GW order.
They are observed GWs, not padded calendar GWs. GW appearance/start rates measure the fraction
of observed GWs with any appearance/start; mean starts/minutes/points are GW totals.
Appearance means recorded minutes > 0; a start means recorded starts > 0.

All windows include zero-minute records. A partial window uses its actual count as denominator,
with `_count`, `_fixture_count` and `_short_history` indicators. Each rolling statistic exposes
`_missing_count`; any missing contributing value makes its sum/mean null. No history gives null
statistics plus `pre_gw_no_history=True` and zero coverage counts. A prior DGW's eligible leg can
form partial history while its later leg is excluded; no future schedule is consulted to decide
window completeness. `_short_history=False` means N observed eligible units, not proof of a full
historic roster or all originally scheduled matches. No season carryover or zero imputation occurs.

`quality.parquet` holds end-of-season discrepancy metadata outside the feature table. All 38
Evan Ferguson 2024/25 GWs are excluded from evaluation because the erroneous fixture cannot
be localized. Recorded points are retained; the source's 27-vs-28 discrepancy is not repaired.
Future training/evaluation must join and apply this quality mask. The readiness report excludes
affected rows from its baseline-candidate count, without changing any historical feature.

Future training code must use `feature_columns.json` / `FEATURE_COLUMNS` rather than selecting
every numeric column in `player_gameweeks.parquet`. See the audit for Phase 2 prerequisites.

## Run the first baseline experiments

See [training/README.md](training/README.md) for the isolated pinned dependency stack, two-stage
training/holdout commands, prediction reproduction and artifact locations. Only the existing
82,472 candidate observations enter experiments. The first run used validation-only model
selection, then a frozen holdout evaluation; 2025/26 is now a reported, consumed test period.
Subsequent experiment design must not use its outcomes to tune or select models/features.

Phase 2c's fixed appearance/conditional-points experiment compares against the same Ridge on
2024/25 only. It has no test stage and does not reuse 2025/26 arrays or outcomes. The report and
reproduction commands are linked from [training/README.md](training/README.md).

## Prepare historical team context - Phase 3a

```powershell
ml/.venv/Scripts/python.exe -X utf8 -m ml.prepare_fixtures run
ml/.venv/Scripts/python.exe -X utf8 -m ml.prepare_fixtures validate
ml/.venv/Scripts/python.exe -X utf8 -m unittest ml.tests.test_fixture_features ml.tests.test_preparation ml.tests.test_readiness -v
```

This offline step uses the existing raw/processed archive and adds ignored files under
`data/processed/fixture_context/`. It leaves the 216-column v1 preparation/training contract
intact. The versioned opt-in v2 schema combines v1 with 152 last-observed-club history indicators;
`ml.prepare_fixtures.approved_player_inputs` checks identity/order/cutoffs and joins whitelisted
sidecar columns. Its output includes four join keys, which must still be excluded from X.
The sidecar's club ID/status and separate quality/research tables are never predictive inputs.
Schedule snapshots and current player-club registration have not been verified; final fixture
assignments and paired opponent strengths remain explicitly research-only. No new models run.
See [FIXTURE_FEATURES.md](reports/FIXTURE_FEATURES.md) for definitions and actual coverage.

## Fixture-aware private forecasting — Phase 4

[FIXTURE_ML.md](reports/FIXTURE_ML.md) documents the trained components, 29-column contract,
chronological diagnostics, explicit retrospective schedule proxies, live six-GW adapter and
reproduction commands. `fixture_model_config.json` versions the fixed experiment. Fitting stays
in the isolated ML environment; the Assistant loads the small portable JSON without scientific
dependencies. The old Ridge and statistical methods remain available and unchanged.
