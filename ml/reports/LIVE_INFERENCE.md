# Live Ridge inference and prospective recording

Implemented from `6267d67` on 13 October 2026. The Private Assistant now loads the existing
Phase 2b Ridge and compares its eligible next-GW predictions with the statistical forecast.
Statistical predictions remain the default. No model was fitted, tuned or selected during this
integration, and no public scenario calculation was changed.

## Model and feature contract

`src/fpl_forfeit/assistant/ridge_v1.json` packages the already fitted Ridge, training medians,
means/scales, coefficients, intercept and provenance. Version:
`ridge-baselines-v1-train-2023-25`; alpha 100; 53,975 training rows from 2023/24 and 2024/25.
The approved schema contains 216 columns; 146 survived training-only empty/constant filtering.
Model file SHA-256:
`59be0fbae464c188ec621aa5655cb45afc70441988fc9a63a06b9924571cdea1`.
It is a small JSON asset, included in the normal Python wheel. Deployment needs neither
scientific dependencies nor local ignored artifacts.

The portable predictor applies the saved median imputation and standardisation, then the
original linear expression. Negative point estimates are retained. Missing values stay null
until training-fitted imputation. A parity check against the original joblib artifact agrees to
1e-12 across 64 inputs with missing values.

`assistant/ml_features.py` freezes the historical `ml.dataset` feature definitions at the
starting revision. Tests compare every output column against that historical implementation.
`api.parse_ml_history` converts official `element-summary/<id>/` fixture records into those
inputs, checking identity, round, kickoff, opponent, completion and duplicate consistency.
Only earlier GWs in the same season whose finished fixture kicked off **more than 24 hours
before the actual input cutoff** are eligible. The result lag is an availability proxy, not a
proof that every later official correction had already happened. Historical late revisions
remain a training limitation. Live captures preserve the actual source version obtained.

Features retain last 3/5/10 match and observed-GW means, minutes, starts, appearances, returns,
season-to-date sums and coverage/missingness indicators. Observed GWs are not padded with
unobserved blank/registration rows. Historical DGW legs stay separate before GW aggregation.
Player/team IDs reset by season; historical teams come from each fixture, not the current club.
The cache uses season, element ID, official player code and finished-fixture signature.
`history_past`, bootstrap season totals, source `xP`, prices, current position, current-GW
outcomes, availability and upcoming opponent/home/away are never substituted into X.

## Supported forecasts and honest fallbacks

The forecast horizon is the next six **published future official deadlines**, or fewer at
season end. Actual published fixture assignments are preserved in source captures. No future
schedule is reconstructed retrospectively.

| Situation | Behaviour |
| --- | --- |
| Next GW, one published timed fixture, eligible core history, available player | Raw Ridge expected unmultiplied player-GW points |
| Horizon GWs 2-6 | Statistical fallback: intervening match histories do not yet exist |
| New player, early season or no eligible current-season history | Statistical fallback; prior-season totals are not imported |
| Missing history/core statistics or contradictory fixture records | Statistical fallback with explicit reason |
| Last eligible observed club differs from current club | Transfer fallback; no invented new-club adjustment |
| Non-available official status or chance below 100% | Statistical fallback; availability is not a trained Ridge feature |
| Blank or Double GW | Statistical fallback; no zeroing/doubling of raw Ridge predictions |
| Incomplete schedule, unknown kickoff or passed/missing deadline | Statistical fallback |
| Model load/inference or official-history failure | Statistical planning remains available |

Completed histories may include zero-minute appearances and negative disciplinary points.
Partial history uses the historical denominators and coverage indicators; it is not silently
expanded to a full window. Recent fixtures inside the buffer stay excluded. In-season histories
can include older clubs once a move has been observed; the fallback detects unobserved moves,
not every possible transfer-related distribution shift.

There are no Ridge opponent-specific, fixture-count, price, injury, defensive-points or
appearance-probability adjustments. Phase 3a team features were never trained into this model
and are not activated here. The older scoring regimes and dropped defensive-action columns
limit the model's current-season calibration. A six-GW ML-only forecast and a compatible
ML-to-simulator adapter require additional modelling and validation; they are not implemented.

## Private UI and failure isolation

Predictions offers statistical, ML with statistical fallbacks, and ML-minus-statistical views.
Each total shows how many GWs have direct Ridge coverage. Differences are null for fallback
rows, and difference totals cover only supported GWs. Statistical probabilities and ranges are
hidden when viewing ML points. Details carry model/hash, cutoff, deadline, history timestamp,
feature coverage and fallback reasons. The authenticated status endpoint supports bounded
progress polling; loading is coalesced and uses at most four history workers.

My Team can compare **fixed, unmultiplied starting XI marginal subtotals** when all eleven
players have Ridge predictions. This follows linearity of expectation for that fixed subtotal;
it does not include captain/vice, substitutions or bench points and is not a simulated lineup
score. All optimisers, captaincy, transfers, chip values and joint uncertainty remain statistical.
No predictions are integrated into the public analyser or submitted to FPL.

## Immutable prospective archive

The background scheduler starts only when private authentication is configured and checks every
30 minutes while the application process runs. A normal private Predictions request also
initiates inference/recording. Official histories cache for up to six hours unless finished
fixtures change; explicit Refresh invalidates them. The statistical forecast keeps its existing
cache. The first roster load needs one bounded request per current player, not a historical
dataset download; widespread request failure trips an outage circuit.

Each archive capture contains raw bootstrap, fixtures and per-player history envelopes with
source URLs/times; published deadlines; both point forecasts; version/seed/sample information;
feature coverage/fallbacks; model/preprocessing; and SHA-256 provenance. The feature cutoff and
prediction-generation completion time are separate. **Every source and the completed forecast
must precede all target deadlines.** A record completed after the nearest deadline is rejected.

Atomic exclusive publication preserves the first capture in each UTC six-hour bucket; buckets
become hourly during the final two hours before the nearest deadline. Existing captures are
never overwritten, including across processes. The archive stops adding captures after 512 MiB
rather than deleting history. Compressed football inputs remain ignored, not committed.
Actuals are recorded only for tracked GWs after official `finished=true` and
`data_checked=true`. Unknown player points remain unknown. The first official-final actual
capture is retained; later corrections require an explicit separately versioned review.
No personal squad, league, account, session or credential data is recorded.

**Deployment limitation:** the existing free Render service sleeps without inbound traffic and
loses local files on spin-down, restart or redeploy. Its process timer and filesystem cannot
guarantee unattended durable deadline capture. Use the CLI on an existing always-on scheduled
runner with persistent storage/backups for a defensible prospective dataset. No new paid service
or persistent disk has been provisioned. This follows [Render's free-service documentation](https://render.com/docs/free).
A missing pre-deadline capture remains missing; it is never recreated after the deadline.

## Commands and deployment

Run from the repository root using the application environment (Python 3.12+):

```powershell
$env:PYTHONPATH = 'src'
python -m fpl_forfeit.assistant.prospective --archive-dir snapshots/ml
python -m fpl_forfeit.assistant.prospective --replay snapshots/ml/forecast-2026-27-GW07-20261013-06.json.gz
```

For a durable runner, schedule the first command every 30 minutes, keep its archive outside an
ephemeral filesystem, and check its exit status. It uses only public official football APIs and
does not need the Assistant password. A failed inference/recording run exits nonzero. In the
web process, failures instead surface in authenticated model status while statistical planning
continues. The saved replay verifies source hashes and reproduces both methods without network
or fitting at the matching code revision. The first schema-v1 capture remains supported; new
captures use schema v2 with canonical numeric-key hashes and separate cutoff/completion times.

Configuration:

| Variable | Default / purpose |
| --- | --- |
| `FPL_RIDGE_MODEL` | Packaged `ridge_v1.json`; optional reviewed model file with exact schema |
| `FPL_ML_ARCHIVE_DIR` | `snapshots/ml`; writable public-football archive location |
| `FPL_ML_AUTORECORD` | `1`; set `0` to disable only the web process scheduler |

The normal `pip install .` Render build includes the model. The existing single-worker start
command, password hash and HTTPS-origin setup remain unchanged. Source imports/tests do not
require model binaries from ignored directories. To re-export the existing **own local**
research artifact, with the pinned scientific environment:

```powershell
$env:PYTHONPATH = 'src'
ml/.venv/Scripts/python.exe -m ml.export_ridge
ml/.venv/Scripts/python.exe -m unittest ml.tests.test_live_ridge_export -v
```

The exporter loads the trusted local Phase 2b joblib, verifies alpha/schema and performs no
fitting. Never load a remote/untrusted pickle. If that artifact is absent in deployment, use the
committed portable model; no rebuilding or historical downloads are needed. Original offline
reproduction instructions and locked dependencies remain in `ml/training/README.md`.

## Verified live run

Official 2026/27 inputs captured on **2026-10-13 at 10:05:50 UTC** covered all **667 players**
and 380 fixtures. The next published deadline was **GW7, 2026-10-17 10:00 UTC**.
All 667 history requests succeeded. **470 players received direct GW7 Ridge estimates; 197
used availability-related statistical fallbacks.** GWs 8-12 used multi-step fallbacks.
Example at that capture: player 1 had Ridge **3.2269** versus statistical **2.9896** points.
These are actual inference outputs, not measured future accuracy.

The ignored first capture is 794,624 compressed bytes. An offline replay reproduced all
667 players across both forecast methods without fitting or network. GW7 was not officially
final, so no actual scores were recorded. Final-only recording is covered by synthetic tests.

Validation includes full historical/live feature parity, original/portable model parity,
future-outcome exclusion, strict result lag, missing inputs, transfers, availability, blanks,
doubles, missing fixture timing/deadlines, model failures, immutable captures and official-final
actuals. All **274 application Python tests**, the production frontend build, installed-wheel
smoke test and private browser checks passed. A refresh regression also prevents older
background jobs or cached predictions from being paired with a newer forecast generation.
Browser checks cover model selection/defaults, fallback
labels, differences, hidden statistical ranges, fixed-XI wording, authentication and existing
planning interactions at mobile/desktop sizes and both themes.

The next improvement is to collect durable prospective snapshots and score these frozen
predictions before changing models. Scoring-rule drift, new players, injuries, one-step support
and absent fixture effects remain explicit limits; no fresh superiority claim over the
statistical simulator is justified yet.
