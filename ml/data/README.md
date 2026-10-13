# Local historical data

`raw/` contains pinned original CSVs and their checksum manifest. `processed/` contains Parquet,
the feature allowlist and small generated CSV/JSON samples. Both are ignored and created by
`python -m ml.prepare run`. Use the repository-root commands in `ml/README.md`.

`python -m ml.prepare_fixtures run` creates the ignored `processed/fixture_context/` sidecars:

```text
team_deadline_features.parquet       # season/team/GW lagged goals, strengths and coverage
player_team_features.parquet         # original player/GW keys plus last observed club history
retrospective_fixture_context.parquet # final opponents/venues/kickoffs and lagged opponent form; research-only
retrospective_team_gameweeks.parquet  # final blanks/doubles/fixture IDs; research-only
team_xg_quality.parquet              # unverified player xG sums and coverage; audit-only
player_club_quality.parquet          # target-club vs lagged proxy disagreements; audit-only
feature_coverage.parquet             # every indicator, season and GW's non-null/history coverage
coverage_by_gameweek.csv             # compact per-GW coverage summary
approved_schema.json / metadata.json # approved inputs, pinned provenance, checksums and config
```

All source/v1 files remain unchanged. New sidecars preserve all existing rows; future evaluation
must join the existing `quality.parquet` and retain the Ferguson quarantine. A final-schedule
blank does not establish a player target or deadline fixture knowledge. Follow
`ml/reports/FIXTURE_FEATURES.md` before joining any context into future model inputs.
