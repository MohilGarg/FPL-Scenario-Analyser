# Training-feature readiness — Phase 2a

This records readiness before training. Phase 2b's actual baseline experiments and their limits
are documented separately in [BASELINE_EXPERIMENTS.md](BASELINE_EXPERIMENTS.md).

Reviewed 12 October 2026, extending Phase 1 commit `0f08d1b`. No models, baselines,
LightGBM experiments or production integrations have been implemented.

**There is enough data to start retrospective outcome-only baseline experiments, subject to
the limitations below. A fully faithful point-in-time comparison against the production
engine is still not ready.** The feature windows enforce deadline/season exclusion; original
stat publication and revision times remain unavailable.

## Deadline reconstruction and provenance

The source is three timestamped copies of the **official FPL `bootstrap-static` API**, preserved
in [FPL-Armband's archive](https://github.com/beeradb/FPL-Armband/tree/4cbbe8b206686a6def6751966c640fbc673333a7/data/captures).
The mirror is pinned to commit `4cbbe8b206686a6def6751966c640fbc673333a7`; all six compressed
payload/manifest files have reviewed SHA-256 hashes in `ml/deadline-source-lock.json`.
Only **598,546 bytes** were added to the ignored raw-data cache, avoiding the full capture history.

| Season | Official API capture (UTC) | Verified deadlines | Unresolved GWs |
| --- | --- | ---: | --- |
| 2023/24 | [19 May 2024, 11:00:09](https://web.archive.org/web/20240519110009id_/https://fantasy.premierleague.com/api/bootstrap-static/) | 38/38 | None |
| 2024/25 | [25 May 2025, 10:49:26](https://web.archive.org/web/20250525104926id_/https://fantasy.premierleague.com/api/bootstrap-static/) | 38/38 | None |
| 2025/26 | [24 May 2026, 13:22:00](https://web.archive.org/web/20260524132200id_/https://fantasy.premierleague.com/api/bootstrap-static/) | 38/38 | None |

For each capture, GWs 1–37 are finished events with deadlines before the capture. GW38 is
`is_next`, unfinished, and its deadline is after the capture. The pipeline validates the
uncompressed manifest hash, season, archive timestamp/original endpoint, distinct IDs 1–38,
UTC ISO/epoch agreement and increasing dates. Future provisional dates would retain a reported
value for auditing but an **empty usable cutoff**. No dates are inferred from kickoff times.

These late captures supply finalized **calendar facts only**. Their end-of-season player,
availability, price and team statistics are never read into features. Deadline rows retain
the original archive URL, pinned mirror URL, capture time, body hash and evidence classification.

Selected dates were independently cross-checked against official Premier League articles:

- 2023/24 GW1: 11 August 2023, 17:30 UTC / 18:30 BST.
  [Official GW1 deadline](https://www.premierleague.com/en/news/3621421).
- 2024/25 GW1: 16 August 2024, 17:30 UTC / 18:30 BST.
  [Official GW1 deadline](https://www.premierleague.com/en/news/4070702).
- 2024/25 GW15: 7 December 2024, **11:00 GMT**, unchanged after Everton–Liverpool's
  postponement. The first played match starts at 15:00, demonstrating why a fixed kickoff
  offset would be wrong. [Official postponement/deadline notice](https://www.premierleague.com/en/news/4188928).
- 2025/26 GW1: 15 August 2025, 17:30 UTC / 18:30 BST.
  [Official GW1 deadline](https://www.premierleague.com/en/news/4373995).

**Verification limit:** original Wayback replay returned HTTP 503 during investigation. The
114 values were verified against pinned mirrored official API bodies and their provenance
manifests, rather than all independently re-downloaded from Internet Archive. Official editorial
cross-checks are selected dates, not every GW. No missing calendar timestamps were manufactured.

## Feature and quality coverage

| Season | Player-GWs | Rows with valid core features | Cold starts | Evaluation quarantine | Baseline candidates |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023/24 | 28,742 | 27,877 | 865 | 0 | 27,877 |
| 2024/25 | 26,919 | 26,135 | 784 | 38 | 26,098 |
| 2025/26 | 29,338 | 28,497 | 841 | 0 | 28,497 |
| Total | 84,999 | 82,509 | 2,490 | 38 | **82,472** |

"Valid core features" means a verified deadline, at least one eligible prior fixture and known
last-three-match means for xG, xA, minutes, starts, goals, assists and points, plus appearance/
start rates. Partial windows are permitted with explicit coverage flags. It does not mean all
possible features are known or that their original publication timing has been proven.
One of Ferguson's 38 quarantined rows is also a cold start, hence only 37 valid-feature rows
are removed from the candidate total. No predictive values are supplied for first observed histories.

Every rolling xG/xA/minutes/starts/goals/assists/points sum and mean, and both appearance/start
rates, has the same non-null coverage in this revision: **27,877 / 26,135 / 28,497** by season.
That equality follows from no missing core source values in eligible records. Full-window
coverage differs; the counts below apply individually to each of these statistics:

| Full observed window | 2023/24 | 2024/25 | 2025/26 |
| --- | ---: | ---: | ---: |
| 3 matches | 26,170 | 24,576 | 26,822 |
| 5 matches | 24,463 | 23,020 | 25,161 |
| 10 matches | 20,335 | 19,158 | 21,042 |
| 3 GWs | 26,157 | 24,573 | 26,819 |
| 5 GWs | 24,442 | 23,012 | 25,157 |
| 10 GWs | 20,221 | 19,145 | 21,037 |

Generated `readiness.md` gives every rolling statistic's non-null/full-window counts separately;
`readiness.json` also covers every cumulative feature and indicator. Earlier seasons' defensive
actions remain unknown, not zero. All source outcomes and fixture records remain preserved.

## Window and temporal definitions

- Only **earlier GWs in the same season**, finished fixtures and known kickoffs **more than
  24 hours before the actual deadline** enter. Both legs of the target GW are excluded.
  A later-played fixture with an earlier GW number is still excluded by its timestamp.
- Match windows use actual kickoff order, including rearrangements; GW windows aggregate
  eligible fixtures within each observed GW and use numeric GW order. No blank calendar GWs
  or pre-registration records are invented. A prior DGW may contribute only its eligible leg.
- 3/5/10-unit sums/means use actual observed window sizes. Appearance/start rates include
  zero-minute records. GW rates measure whether there was any appearance/start in that GW;
  GW means for minutes/starts/points use the summed eligible fixtures.
  Appearance means minutes > 0 and a start means starts > 0; these are minute/start statistics,
  not a reconstruction of zero-minute disciplinary appearances for autosub rules.
- Counts, fixture counts, short-history flags, per-stat missing counts and a no-history flag
  expose coverage. Any missing contributing statistic makes its sum/mean null. No history
  produces null statistics, not zero estimates. Season-to-date sums/rates use the same cutoff.
- Predictors are only `FEATURE_COLUMNS` / `feature_columns.json`. Targets, `observed_*`, final
  schedules/positions/clubs and evaluation-quality metadata are outside that list.

## Ferguson discrepancy and evaluation exclusion

For Evan Ferguson, 2024/25 element 123, the retained fixture points sum to **27**, while the
season snapshot reports **28**. The inconsistent fixture cannot be localized from this source.
`quality.parquet` therefore marks **all 38 player-GWs** in that season `evaluation_eligible=False`,
with reason `player_season_points_inconsistent_unlocalized`, both source totals and a retrospective
quality-timing label. Recorded fixture/GW points are unchanged. Baseline-candidate counts apply
this exclusion; future training and evaluation must join and honor the separate mask.
End-of-season QA is never fed into predictors or used to rewrite earlier feature values.

## Still unavailable or unsuitable

Pre-deadline prices, ownership/transfers and source form; injury/news/availability histories;
historical set-piece roles; versioned upcoming schedules; full eligible-player/transfer timelines;
older defensive contributions; exact substitution/event timing; detailed historic BPS components;
and source `xP`. Current-GW points, xG/xA, ICT and BPS remain outcomes, not same-GW predictors.
Final season totals/per-90/ranks, current API records and the archive's end-of-season elements
are unsuitable inputs. Earlier defensive features require missing-data treatment rather than
retroactive values under the newer scoring regime.

## Remaining leakage and readiness limits

The historical outcome CSVs are final corrected histories, not timestamped stat vintages.
The 24-hour result delay is a conservative availability proxy, not proof of result publication
or finalisation. Late revisions, abandoned matches and retroactive GW assignment can therefore
prevent a strict as-of replay. Historical roster/registration gaps and retrospective QA selection
can also change the evaluation population; report those exclusions explicitly. Scores from these
rows alone must not be presented as a verified production-engine comparison.

For a later baseline phase, predefine the prediction population, cold-start/blank handling,
quality exclusions, train-only imputation and chronological splits (2023/24 development,
2024/25 validation, untouched 2025/26 holdout). Use only cutoff-gated core results initially and
report scoring-regime changes and zero-minute/DGW subgroups. This phase supplies readiness
artifacts only and performs **no fitting, tuning, evaluation runs or deployment**.

## Validation and reproducibility

Run the isolated pipeline and checks from the repository root:

```powershell
ml/.venv/Scripts/python.exe -X utf8 -m ml.prepare run
ml/.venv/Scripts/python.exe -X utf8 -m ml.validate
ml/.venv/Scripts/python.exe -X utf8 -m unittest discover -s ml/tests -v
```

Downloads are pinned/bounded and cached; `prepare` alone works offline. All generated calendars,
features, quality metadata and reports remain ignored. Validation verifies source hashes and
token preservation, every deadline's archive proof, all feature rows against per-player cutoff
reconstruction, target reconciliation and quality metadata against original season totals.
Focused tests cover future-outcome mutations, target/prior DGW timing, season resets, missing
values, window denominators, provisional deadlines and archive corruption. The production
simulator, assistant, API and frontend are unchanged.
