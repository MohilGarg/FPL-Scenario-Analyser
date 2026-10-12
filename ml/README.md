# Historical FPL research — Phase 1

This area discovers, audits and prepares historical player outcomes. It has no imports from
`src/fpl_forfeit`, no application entry points and no training, tuning or deployment code.
The assistant, shared forecasting engine, API and frontend are untouched.

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

`python -m ml.prepare fetch` downloads four revision-pinned files per season (19.1 MB total).
It caches bytes and SHA-256 checksums, resumes completed files, bounds each file to 16 MiB and
total input to 64 MiB. It never clones the source repository or fetches per-player directories.
`python -m ml.prepare prepare` verifies the cache and works entirely offline. To process a
subset use `--seasons 2023-24 2024-25`. Source updates require a deliberate edit of
`source-lock.json` and a fresh `--raw-dir`; cached revisions cannot be mixed.

## Structure and outputs

```text
ml/
  source-lock.json             # reviewed source repository, immutable commit and file list
  requirements.txt            # isolated Parquet dependency
  sources.py                  # bounded retrieval, provenance and cache verification
  dataset.py                  # pure parsing, match mapping, GW aggregation and feature gate
  audit.py                    # column/missingness, identity and score reconciliation audit
  prepare.py                  # ingestion/preparation CLI
  validate.py                 # source fidelity and dataset checks
  tests/                      # small offline regression tests
  training/README.md          # reserved; no models implemented
  evaluation/README.md        # reserved; no engine adapters or comparisons implemented
  reports/DATA_AUDIT.md        # reviewed audit, missing features and Phase 2 recommendations
  reports/generated/          # ignored: full audit.md and audit.json
  data/raw/                   # ignored: original CSV bytes and manifest.json
  data/processed/             # ignored:
    source/<season>/*.parquet  # every original field name/token, including duplicates/blanks
    match_records.parquet     # unique player-fixture records, typed outcomes and match identity
    player_gameweeks.parquet  # observed player-GW targets, fixture lists and gated history
    features.parquet          # identifiers/gate metadata plus explicit historical feature list
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

The source files lack reliable historical deadlines and timestamped pre-deadline snapshots.
By default **all `pre_gw_*` feature values are null**, with `feature_status=no_deadline`.
The useful Phase 1 output is the historical target/match archive, not a claim of a complete
point-in-time training set. Prices, availability, ownership, source `xP`, final team strength and
end-of-season aggregates are never added to the feature list.

A separately verified deadline calendar can enable the restricted historical feature builder:

```csv
season,gameweek,deadline_utc,source_url
2024-25,1,2024-08-16T17:30:00Z,https://example.test/verified-calendar
```

This illustrates the schema only; supply a verified calendar covering the intended evaluation
period, with actual source URLs. Run `python -m ml.prepare prepare --deadlines path/to/deadlines.csv`.
Missing deadlines keep affected rows null. Offset-aware deadlines must increase by numeric GW
and precede any known target kickoff. The supplied calendar is hashed in the audit.

For a supplied cutoff, historical features use **only earlier GWs in the same season** with a
finished fixture and a recorded kickoff **more than 24 hours before** the cutoff. Same-GW DGW
legs, later GWs and unknown kickoff times cannot enter. The 24-hour delay is a conservative
readiness proxy, not a recorded result-publication timestamp. Retrospective source corrections
remain unverified. `feature_status=deadline_supplied_results_lag_24h` states that limitation.

The feature list contains prior fixture/GW counts, cumulative known outcome statistics and the
mean points across the latest three *observed eligible* GWs, rather than three calendar GWs.
It includes zero-minute observations and never averages only players who appeared. Missing
history or any missing contributing statistic yields null for its aggregate. Eligible legs of
a prior GW can form partial history if another leg is still too recent. The two history-count
fields describe coverage. No season carryover or blanket zero imputation is performed.

Future training code must use `feature_columns.json` / `FEATURE_COLUMNS` rather than selecting
every numeric column in `player_gameweeks.parquet`. See the audit for Phase 2 prerequisites.
