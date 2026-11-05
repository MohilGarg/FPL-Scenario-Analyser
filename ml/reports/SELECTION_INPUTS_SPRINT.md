# Selection, availability and provider disagreement — 5 November 2026

The new selection-evidence adapters preserve what was known and when it was retrieved. They do
not turn prose into medical predictions, infer injury return dates, or reconstruct missing past
registration. The fitted challenger and its historical tests are documented separately. Current
production forecasting remains unchanged.

## Implemented evidence and current coverage

The original immutable GW7-9 bootstraps already contained official FPL `ep_next`. Recovering those
original values creates a valid common-capture comparison without downloading or backdating a new
historical forecast. On the 614 candidates fixed by original statistical expected minutes >=45,
original statistical/official/equal-internal-official RMSE is **3.0645 / 3.5986 / 3.1888** points.
On all 2,004 rows, statistical/official RMSE is **1.9988 / 2.3054**. Official alone worsens all three
deadlines; there is no justification to weight it more heavily. The three-deadline uncertainty is
weak, and original 192-draw forecasts here differ from the new 1,024-draw development replay.
See [EXTERNAL_COMPARISON_SPRINT.json](EXTERNAL_COMPARISON_SPRINT.json). Cat and Sideline have no
settled common-capture outcomes yet; no weights are learned with fewer than eight distinct settled
common deadlines. GW10 disagreement below describes forecasts, not their accuracy.

`assistant/selection_inputs.py` provides pure normalizers; `ml/selection_inputs.py` collects
prospective local evidence outside web requests. Captures are immutable, content-addressed JSON
under ignored `snapshots/ml/selection/`. They are not personal squad data.

| Input | Implemented behavior | Verified current state |
| --- | --- | --- |
| Official FPL availability | Preserves season/player IDs, official status and next-GW chance, `news_added` publication time when supplied, retrieval time and news presence. Does not copy or interpret news prose. | **670 rows**, retrieved **12:30:09 UTC**. 193 have news/non-available flags, including doubtful, injured, suspended and unavailable statuses; these are not all injuries. 363 rows have a supplied news timestamp; absent timestamps remain null. |
| Sideline Quant playing-time forecasts | Reuses the existing validated fixture-grain source and preserves expected minutes, start/no-show/cameo probabilities, probability definitions, computation and original retrieval times. | **670 rows**, computed **03:35:03 UTC**, retrieved **12:30:44 UTC**. These are provider forecasts, not confirmed lineups or outcomes. |
| Official club facts | Strict manual structured import supports ruled out, available for selection, uncertain selection, suspended and confirmed lineup. Requires an explicitly verified official HTTPS domain, a season-specific FPL ID, publication time and confidence class. | Adapter/tests implemented; no fabricated or unreviewed club report was imported. No LLM interprets press-conference prose. |
| Cross-competition calendar | Optional documented football-data.org v4 CL/PL adapter, explicit season-specific provider-to-FPL club mapping, one-hour cache, bounded payloads/timeouts/retries, attribution and private credentials. | **Not configured:** no token and verified club map. No live calendar coverage or congestion improvement is claimed. Other cups remain explicitly uncovered. |

Official next-round chance is scoped to the next Gameweek, not a six-week injury prognosis.
The fitted challenger caps next-GW appearance once; later forecasts for currently flagged players
are marked availability-unknown, with separate conditional-if-available scenarios. No recovery
date is invented. A confirmed lineup obtained after a deadline fails the same cutoff test as any
other late input: publication and original retrieval must both precede the forecast cutoff, and
the cutoff must precede the applicable deadline. An old publication date cannot backdate a new
download.

Calendar features count known club matches and days since a known match. They do not establish
that any particular player participated, and `coverage_complete=False` prevents a missing cup
feed from becoming a false assertion of no congestion. Provider `lastUpdated` is retained as an
update timestamp, not treated as proof of first publication.

## Access, attribution and research limits

The official FPL availability extract and manual club facts remain private local evidence.
Sideline model-only selection forecasts retain their existing CC BY 4.0 attribution and source
definitions; underlying official news is outside that license. See the
[external source register](EXTERNAL_SOURCES.md).

[Football-data.org's v4 documentation](https://www.football-data.org/documentation/quickstart)
defines the implemented match endpoint. Its [coverage page](https://www.football-data.org/coverage)
lists Champions League and Premier League in the free tier, but an API token is still required.
Its [terms](https://www.football-data.org/about) require attribution and confidential developer
credentials. Tokens are supplied by `FOOTBALL_DATA_TOKEN`, never written into snapshots or Git;
calendar imports are not allowlisted for public release redistribution.

Public expected-lineup pages were investigated, including
[Fantasy Football Scout's team news](https://www.fantasyfootballscout.co.uk/team-news). An accessible
page does not establish a stable timestamped bulk export or redistribution permission. No new
commercial lineup scraper or subscription bypass was added. Sideline's already documented state
forecasts are the usable prospective probabilistic input. Official club calendar downloads can
also require login, as [Liverpool's fixture page](https://www.liverpoolfc.com/matches/mens-team/fixtures)
shows; that access is not bypassed. OpenFootball's
[public-domain Europe repository](https://github.com/openfootball/champions-league) did not list
a current 2026/27 season when inspected, so it was not substituted for a current complete calendar.

No trustworthy historical vintages of these new inputs were established. They are not joined to
old injuries or outcomes. Their incremental information beyond recent minutes and official flags
must be measured on future original captures. Missing calendar configuration is a specific blocker,
not a reason to postpone the usable official/provider evidence.

## Availability interaction: cap versus multiplication

[AVAILABILITY_AUDIT_SPRINT.json](AVAILABILITY_AUDIT_SPRINT.json) replays the new fitted model on
original timestamped GW7–9 official inputs and histories. It is **retrospective development**:
the fitted challenger did not exist at those deadlines. There are only three settled deadlines,
all single-fixture GWs. These are proposal-level diagnostics before legal-lineup sampling, not
original prospective forecasts or a points ablation.

If a history-based model already predicts nonappearance, treating the official chance as an
independent second risk can suppress appearance twice. The experimental cap computes
`P(appear) = min(history-based P(appear), official chance)` and rescales non-absent states together.
The comparator multiplies the history-based non-absent probabilities by the official chance.
Neither operation establishes that official percentages are calibrated conditional probabilities.

| Pre-deadline group | N player-GWs | Appearance Brier, cap / multiply | Start Brier, cap / multiply | Appearance log loss, cap / multiply |
| --- | ---: | ---: | ---: | ---: |
| All covered players | 2,004 | 0.09419 / 0.09447 | 0.08096 / 0.08075 | 0.35832 / 0.36000 |
| Official uncertain/unavailable flag | 605 | 0.04457 / 0.04550 | 0.02280 / 0.02212 | 0.33273 / 0.33827 |
| Reliable starters with uncertain flag | 44 | 0.20284 / 0.21017 | 0.19912 / 0.19606 | 0.54880 / 0.57209 |

The 605-row group includes officially unavailable players, not only injury doubts. Reliable
starters use exactly five preceding cutoff-safe records, mean minutes at least 60 and start rate
at least 0.8. In its 44 uncertain rows, observed appearance was 0.3864; cap predicted 0.4049 and
multiplication 0.3353. Cap improves appearance scores, while start Brier worsens slightly. The
mixed result and tiny number of deadlines do not establish a generally superior availability
policy or better FPL decisions. Publication/retrieval cutoffs are retained in the JSON.

## What current providers disagree about

[SOURCE_DISAGREEMENTS_SPRINT.json](SOURCE_DISAGREEMENTS_SPRINT.json) uses the independently
retrieved [immutable GW10 capture](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw10-rolling-20261105T123201Z-v2),
completed **12:32:01 UTC** from committed code `cffb0b6`. All four original internal methods and
both external sources cover 670 players. The fixed selection rule above identifies **166** of
them. No GW10 outcome was available; every number below is disagreement between predictions,
not an error against truth. The new fitted model was not inserted into this original release.

| Prediction comparison | All 670: mean absolute points disagreement | Fixed 166 candidates |
| --- | ---: | ---: |
| FPL Cat versus statistical | 0.3988 | 0.5711 |
| Sideline versus statistical | 0.4275 | 0.6503 |
| FPL Cat versus original minutes-v1 simulation | 0.3386 | 0.4993 |
| Sideline versus original minutes-v1 simulation | 0.3781 | 0.5732 |
| FPL Cat versus Sideline | 0.2093 | 0.3129 |

For selection candidates, archived minutes-v1 averages **72.86 expected minutes**, **0.8573 start**
and **0.8900 appearance**; Sideline averages **75.78**, **0.8474** and **0.9115** respectively.
More provider minutes alongside a slightly lower start probability suggests duration/substitute
assumptions also differ. It does not prove that minutes alone explain the points differences.
Sideline's `p_start60` means starting and reaching 60, so it is compared only with the replayed
raw v1 joint start-and-60 mass, not with an all-appearance 60-minute probability.

On the same candidates, Sideline-minus-minutes-v1 component means are **+0.1346 defensive points**,
**−0.1061 clean-sheet points**, **+0.0642 assists** and **+0.0563 goals**. Those offsetting differences
make defensive contribution and clean-sheet/attacking assumptions useful ablation targets. They
do not identify which provider is correct. Component totals, probability semantics and original
timestamps remain separate from any simulation-distribution integration.

Replayed raw-v1 proposals average 73.06 minutes and 0.8588 start for the candidates; their archived
legal simulation averages 72.86 and 0.8573. Across all players the corresponding shifts are larger:
30.88 to 26.99 minutes and 0.3614 to 0.3156 start. This single-cutoff comparison illustrates why
proposal calibration cannot be assumed to survive the legacy team-selection constraints. The
archive has 192 draws, so these differences contain Monte Carlo variation; the sprint's separate
controlled joint benchmark uses larger samples and repeated seeds.

FPL Cat was computed at 09:15:08 UTC and Sideline at 03:35:03 UTC, with both retrieved by the
common feature cutoff. Different source ages are explicit; a shared permissible information
cutoff does not make all providers' computation times equal. Source means, medians and learned
weights remain point-only comparisons. No new weights were fitted from an unsettled GW10.

## Reproduce and verify

From the repository root, use the application environment for evidence collection and the pinned
isolated ML environment for the retrospective audit:

```powershell
$env:PYTHONPATH='src;.'
.venv/Scripts/python.exe -m ml.external_forecasts collect
.venv/Scripts/python.exe -m ml.selection_inputs
.venv/Scripts/python.exe -m unittest tests.test_selection_inputs -v
ml/.venv/Scripts/python.exe -m ml.audit_availability --archive ml/artifacts/sprint-collection-audit/archive
```

The reusable disagreement CLI reads a specified original `capture.zip`, validates its bundle
hashes and produces aggregate-only output. For the remote-verified GW10 capture used here:

```powershell
.venv/Scripts/python.exe -m ml.source_disagreements ml/artifacts/activation-capture-20261105/remote-verification/fpl-2026-27-gw10-rolling-20261105T123201Z-v2/capture.zip --output ml/artifacts/source-disagreements/reproduction.json
```

Its default output is ignored `ml/artifacts/source-disagreements/result.json`. Existing evidence
is not overwritten. Regenerating the tracked aggregate requires explicit `--report`:

```powershell
.venv/Scripts/python.exe -m ml.source_disagreements ml/artifacts/activation-capture-20261105/remote-verification/fpl-2026-27-gw10-rolling-20261105T123201Z-v2/capture.zip --report
```

The CLI reproduced all **366 numeric aggregates exactly**, with identical original source times,
record hash, generation commit and replayed-v1 source fingerprint. The aggregate now additionally
records the analysis-script fingerprint, single-fixture replay coverage and missing-source status.
Raw proposal replay is restricted to single fixtures; no DGW selection probabilities are invented.

The audit needs the original verified GW7–9 release bundles and official-final actuals, not later
reconstructed flags. Optional calendar collection additionally requires a privately configured
token and a JSON club mapping such as `{"season":"2026-27","teams":{"57":1}}`, whose IDs must be
verified against both providers for that season:

```powershell
.venv/Scripts/python.exe -m ml.selection_inputs --club-map snapshots/club-map.local.json --competitions CL PL
```

The disagreement aggregate is calculated by `ml.recording.archive.read_bundle` from the immutable
GW10 asset, intersecting all source/player keys and applying the predeclared history rule before
comparison. Its record SHA256 and original source/computation times are included in the JSON.
For any pair, signed disagreement is `second − first`; mean absolute and root-mean-squared
disagreement describe that pair, never forecast MAE/RMSE against an outcome. Raw source rows,
manual reports, credentials and player-level diagnostics remain outside Git.
