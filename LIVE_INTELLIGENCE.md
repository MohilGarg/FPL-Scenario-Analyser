# Public live intelligence v4

The Overview now presents current/projected standings and risk, screened football events,
remaining fixture groups and actually recorded risk movement. The existing Explorer and Exact
Scenarios remain separate. All calculations use the shared football model and FPL scoring;
no private squad, optimisation, authentication or private route has been added.

## Statistical inputs and benchmark

`api.py` normalises official bootstrap xG, xA and xGI as optional finite, nonnegative numbers.
Missing values stay missing; a genuine zero stays zero. `forecasting/statistics.py` blends
0.65 expected count with 0.35 observed count, then applies the existing 450-minute positional
prior. These weights are experimental, not fitted or demonstrated optimal. xGI is metadata,
never another observation added to xG+xA. Missing expected counts retain observed-return rates.
Team goals retain completed season results, eight-match shrinkage and a home/away adjustment.

At least three known starts in the last eight completed histories enable a recent starting
fraction with four prior matches. Starter durations use observed positive minutes with four
prior starts; nonstarts give a separate cameo probability and duration distribution. Official
availability scales appearances once. Uncovered players retain season estimates/explicit priors.
Confirmed licensed starters override starting status; bench players still have uncertain cameos.

Each league source primes at most 12 player histories, prioritising captain/vice and playing
time. Three workers and at most 48 pending history jobs bound work across leagues. FPLClient
coalesces each player request and caches it for six hours. The expensive outlook waits at most
two seconds for available histories, freezes that enriched generation for ten minutes, and
reports coverage/pending counts. The initial summary never waits for histories. Failures retain
FPL-only fallbacks. Only fixtures completed in the original source generation enter histories.
Enriched raw inputs, including normalised external appearances, can be saved/replayed.

`outlook(..., model_version="shared-fixtures-v3")` and
`ForecastInputs(..., model_version="shared-fixtures-v3")` retain the observed-return and
season-duration benchmark, without recent histories/xG/external lineup overrides. Default v4
uses the same reusable fixture simulator and multi-Gameweek player projection interface.

## Event associations

`intelligence.py` screens up to 12 differential players around the eight lowest current scores,
with captain/Triple Captain, starters, Bench Boost and possible bench exposure weights. It
examines up to 32 player goals, no-further-return outcomes, cameos and team clean sheets. Final
selection requires impact on a sampled bottom contender (at least 1% final risk or within 15
projected points of the bottom). Up to five diverse cards are selected; fewer are shown when
evidence is insufficient. This is bounded screening, not exhaustive event discovery.

An event mask partitions the existing full simulation draws. Goals mean at least one further
goal; a blank means no further goals/assists while other points still apply; a cameo means any
substitute minutes; a team clean sheet means the shared final opposing score is zero. Each
card reports matching frequency, baseline and conditional last-place risk, risk change,
conditional mean score swing and whether the projected loser changes. Correlated outcomes,
captaincy, autosubs and other fixtures stay present in those conditional samples.

Both matching/nonmatching groups need 40 samples. A risk change must exceed two percentage
points and three estimated standard errors, using the shared partition rather than independent
baseline noise. The displayed three-SE bands describe Monte Carlo sampling only. Screening
multiple events, rare outcomes and model misspecification can still bias associations; this is
not a calibrated confidence guarantee or causal attribution. No independent simulation runs
per card. Clicking a card selects its fixture/outcome in the existing Explorer, which explicitly
labels the recalculation a **forced-event intervention**, not a Bayesian conditional estimate.

## Fixtures and checkpoints

Fixture contributions are remaining category-point deltas under each draw's final effective
manager multipliers. They include shared-player cancellation, chips and sampled substitutions.
They do not assign already-earned bench/captain points to a new fixture or form a safety proof.
Importance uses association between relative match contribution and final last-place outcomes,
then typical relative contribution spread; owned-player counts are never the ranking criterion.
Per-match details show owned players, captains, expected manager contributions and 80% ranges.

Chronological checkpoints group overlapping two-hour kickoff windows, including chains of
overlap. Unknown kickoff times group all remaining fixtures without inventing order. Doubles
keep separate fixture identities and whole-Gameweek appearance decisions. A labelled prefix
of at most 400 existing draws bounds checkpoint work. Pending captaincy/autosubs resolve only
when that team's Gameweek fixtures have completed at the checkpoint. The terminal checkpoint
uses the original final score, avoiding another scoring pass. Final forfeit probability retains
the full 1,600 draws and the same configured tie rule.

Checkpoint-bottom probability means bottom **then**, separately from final forfeit risk. Remaining
uncertainty is the SD of final-minus-checkpoint manager scores averaged across contenders, with
80% residual intervals. It includes later effective-multiplier changes; it is neither a calibrated
forecast error nor a guarantee uncertainty decreases. All fixture details open in the Overview
and link into the existing Explorer. No extra navigation tab or football model is introduced.

## Durable recording and local fallback

`history.py` stores `evaluation.prediction_record` observations in optional SQLite, including
league/GW, source/recorded times, model/seed/sample count/tie rule, members, effective and projected
scores, probabilities, coverage, input checksum/provenance and fixture/player observations.
Only contemporaneous official-FPL records can enter: replay/demo/synthetic records are rejected,
and source-to-record delay must be 0–600 seconds. `(league, GW, source, compatible series)` is
unique. Settings/model/membership produce separate series; the chart never connects them.
League reads are isolated and can be cut off at the displayed source generation.

Every cold unmodified full public outlook records one observation when configured. Summary
and forced-event requests do not record probabilities. A cache hit never creates another
observation. The read-only `/api/league/{id}/risk-history?gameweek=6` endpoint never fetches FPL.
SQLite transactions/WAL, explicitly closed connections and tests across store re-instantiation
support process restarts. Retention is 1,000 records per league/GW and 30,000 globally; each
response returns at most 240. Archive JSON snapshots separately for long-term evaluation.
Missing/unavailable storage has an explicit status and leaves ordinary forecasts available.

The existing Render blueprint is Free. Its local SQLite/filesystem data is lost on restart,
redeploy or sleep, and persistent disks are unavailable on Free
([Render](https://render.com/docs/free#local-files-lost-on-redeploy)). Hosted recording therefore
stays disabled by default. No paid infrastructure or scheduled repository-data commits were
added. A local recorder on an existing machine is the working, no-cost fallback:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m fpl_forfeit.evaluation record 188263 --history-db snapshots/risk-history.sqlite3
$env:FPL_RISK_HISTORY_PATH = (Join-Path (Get-Location).Path 'snapshots/risk-history.sqlite3')
.\.venv\Scripts\python.exe -m uvicorn fpl_forfeit.web:app
```

The command saves raw/enriched inputs and the contemporaneous forecast JSON outside Git, and
adds the same observation to SQLite. Repeating it records new actual observations. An operator
can schedule selected leagues during relevant matches on their existing machine, conservatively
(e.g. every 15 minutes), rather than continuously polling many leagues. Back up the database
using SQLite's backup facility; copying a live WAL file alone is insufficient. Hosted recording
requires `FPL_RISK_HISTORY_PATH` on a genuinely persistent existing volume, or a future storage
adapter; an ephemeral path does not satisfy that requirement.

The Overview charts the four largest peak-risk contenders per compatible series, with source
times, recorded times and changes since the preceding compatible observation. Point/minute
changes and fixture/coverage updates are context, not an allocation of risk movement to a
single player. Several football events, sampling and input/model error can all contribute.
No past probabilities are manufactured from current data.

## Optional provider readiness

`lineups.py` implements an optional API-Football adapter; no real credentials/feed activation
were available. Tests use labelled payload fixtures. Production remains **unverified**.

The official [guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide)
documents Premier League ID 39, `x-apisports-key`, lineup/bench and substitution endpoints,
typically pre-kickoff lineups and much faster live event updates. Its timing examples vary;
coverage must be checked per competition/season and availability is not guaranteed.
[Pricing](https://www.api-football.com/pricing) lists 100 free requests/day with limited seasons.
This adapter deliberately uses a ten-minute cache and at most 90 requests/day **per process**,
three eligible fixtures per generation, five-second network timeouts and fail-soft fallbacks.
It is not a second-by-second substitution feed; freshness must be assessed before production.
Use the existing single API worker; multiple processes/restarts do not share its quota counter.

[Terms](https://www.api-football.com/terms) permit application/fantasy use but do not confer
competition/publication rights, and prohibit resale of raw data. Obtain provider and relevant
rights-holder approval for this public fantasy use before enabling it. No explicit general
retention period was found in the reviewed terms: confirm caching, derived public projections
and snapshot retention in writing rather than assuming unlimited rights. Raw provider responses
are held only in bounded memory; only validated normalised appearances may be saved locally.

Activation requires server-only `FPL_LINEUPS_AUTHORISED=1`, `FPL_LINEUPS_API_KEY`, and
`FPL_LINEUPS_MAPPING_PATH`. The ignored mapping file has explicit dictionaries:
`fixtures: {provider_id: fpl_id}`, `players: {provider_id: fpl_id}`,
`teams: {provider_id: fpl_id}` (JSON keys are strings). Validate and maintain them independently;
there is no automatic name matching. Fixture league, home/away teams and kickoff (five-minute
tolerance), complete 11-player starting lists, unique IDs and mapped player teams are checked.
Unknown IDs are skipped and never presumed absent. Substitution pairs are resolved against
known active/reserve IDs and rejected if ambiguous; on/off minutes and confirmation/source
metadata are normalised. Missing live FPL categories remain frozen even with external lineups.

## Validation and limits

Final verification: 217 Python tests pass, Ruff checks pass, the frontend builds, and Edge browser
checks pass at 320/390/1440px using the existing demos, 12-manager five/three/one-fixture states
and a saved real 14-manager input with a genuine SQLite observation. Screenshots and raw data
remain outside Git and `frontend/dist`.

`python -m tools.benchmark_forecast` measures the complete cached/coalesced service on the
same deterministic 12/50-manager, five-fixture inputs with 96/1,600 samples. Wall times vary
on this Windows host. A second baseline checkout in ignored temporary files controls for that
variation; the original pre-change measurements are also retained locally. Seconds:

| Operation | 12 original before | 12 baseline control | 12 v4 | 50 original before | 50 baseline control | 50 v4 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold summary | .126 | .177 | .243 | .328 | .489 | .481 |
| Cold service outlook | 1.636 | 2.480 | 2.403 | 3.342 | 5.090 | 6.921 |
| Cached outlook | .0010 | .0013 | .0016 | .0029 | .0034 | .0044 |
| Cold forced what-if | 1.685 | 2.235 | 2.117 | 3.383 | 4.815 | 5.922 |
| Four coalesced outlook requests | 1.729 | 2.366 | 3.088 | 3.559 | 4.770 | 9.154 |

All four simultaneous same-source requests still perform **one** computation. The additional
50-manager forecast work is material (~36% versus the nearby control service measurement),
and runs in the existing asynchronous second request. Checkpoint team completion is precomputed,
terminal checkpoint scoring is reused, and the 400-draw prefix bounds the added cost. Initial
summaries and cache hits remain inexpensive; no per-card/per-fixture simulation runs are added.
Network latency, Render cold starts and multi-league load are outside these local CPU measurements;
more real hosted load testing remains useful. The original/control/final JSON artifacts are ignored
in `.browser-artifacts/intelligence-before.json`, `intelligence-before-control.json` and
`intelligence-after-final.json`.

The Python suite covers expected-stat parsing/shrinkage, sparse/availability/history fallbacks,
coalescing, conditional sample calculations versus forced events, noise suppression, overlap,
doubles/checkpoints, live substitution mapping, provider budget/fallbacks and durable history
deduplication, isolation, restart and version/settings separation. Existing scoring, captain,
autosub, chip, hit, scenario and analytics regressions remain in the full suite. Browser checks
exercise event/fixture shortcuts, recorded-risk chart tooltips, mobile/desktop, both themes,
generation-bound errors, neutral home and the existing five views.

Real official input checks on 11 October 2026 found 76 owned players with xG/xA and 12 completed
histories, 14 managers and four remaining fixtures. The same timestamped input under v3/v4
changed mean manager scores by at most 1.16 points and risk by 5.12 percentage points; v4 produced
one screened event and three fixture groups. These are behavioural comparisons, **not accuracy**.
A current v4 observation and input were genuinely recorded locally for future evaluation.
Two earlier v2 records have valid timestamps; the legacy v1 record and replay are ineligible.
Official FPL did not mark GW6 final, so no outcome/error/calibration result can be reported yet.

Independent team Poisson counts, anonymous lineup allocation, approximate bonus, omitted future
reds/penalties/own goals and regulation-only clocks remain limits. Sparse retrieval coverage and
event screening are explicit. More data fields do not establish better predictive accuracy;
hold out many completed Gameweeks, compare v3/v4 contemporaneous records and report uncertainty
before tuning/claiming improvement. Private optimisation must reuse this engine and add its
own authenticated service/storage boundary, never reuse the public risk endpoint for private data.
