# Durable prospective recording for 2026/27

Continues `d8422ba`. A standard GitHub Actions Linux runner now records public official FPL
inputs and the **unchanged** statistical/Ridge forecasts into immutable GitHub Release assets.
Render's filesystem, Assistant login and availability are not dependencies. No fitting,
parameter changes, new features, simulator changes or public UI changes are included.

## Schedule and population

`.github/workflows/record-fpl-forecasts.yml` checks at minute **17 every UTC hour**, on relevant
code pushes, and through **Run workflow**. It retrieves the current official calendar and
selects the next future GW at the actual execution time. It never assumes a kickoff offset.
The fixed policy in `ml/recording/config.json` begins at **GW7** and applies to **2026/27 only**:

| Slot | Collection can start when time until published deadline is | Purpose |
| --- | --- | --- |
| advance | More than 24 hours, up to 120 hours | Several days of opportunities to secure an early capture |
| update | More than 6 hours, up to 24 hours | Refreshed previous-day information |
| final | More than 90 minutes, up to 6 hours | Several opportunities closer to the deadline |

Each slot retains its **first completed published capture**. A job delayed into another window
uses that window; it never labels a new collection as the missed earlier slot. No collection
starts in the final 90 minutes. Publication requires at least 20 minutes remaining, and both
the original forecast completion and GitHub's asset/publication timestamps must precede its
stored deadline. The job has a 25-minute timeout and concurrent runs are serialised without
cancelling an in-progress capture. These margins reduce dependence on a last-minute cron run.

GitHub explicitly warns that scheduled runs can arrive late or be dropped. This design supplies
overlapping opportunities, retries, gap reporting and manual dispatch, **not a timing SLA**.
GitHub also disables inactive public-repository schedules after 60 days. One tiny monthly commit
on `ml-recording-status` keeps repository activity separate from `main`; it contains only an
operational timestamp, not a dataset. Do not merge that branch. Monitor the workflow's most
recent run as well as its summaries. [GitHub scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The runner stops collecting outside 2026/27, and after the operational bound **15 July 2027**.
That bound is not an inferred FPL deadline. Once GW38 actuals are safely archived, disable this
season's workflow to stop redundant checks. API season rollover before missing actuals are
collected is an explicit recovery limitation.

## Storage and provenance

Published tags follow `fpl-2026-27-gw07-advance-v1`, with analogous update/final/actual tags.
These are archive prereleases and do not become the latest application release. Each has a
single `capture.zip`, containing:

- The exact gzip JSON written by the existing `ForecastArchive`: raw official bootstrap,
  fixtures, player history envelopes, both forecast methods, model/preprocessing, coverage,
  fallbacks, seed/sample counts and all original source/cutoff/completion timestamps.
- A small manifest with record byte hash/size, model versions/hash, frozen recording-policy
  hash, primary GW/deadline, code commit and workflow run/attempt/event provenance.

Actual bundles contain the unchanged recorder's raw official `event/<gw>/live/` response,
per-player final points and the official final event flags. The manifest references the
bootstrap source/time that supplied those flags. Missing points stay null; zero and negative
disciplinary points are preserved. Actual values are checked against the raw response.
Only GWs tracked by earlier published forecasts are collected, after both `finished` and
`data_checked` are true. The first official-final capture is retained; late FPL corrections
require an explicitly separate review, not replacement of this archive.

GitHub release immutability is **required**. Uploads go to a draft, are downloaded and validated,
then the draft is published and its `immutable=true` flag checked. The associated code tag and
asset bytes are protected after publication. GitHub asset SHA-256 is verified in addition to
internal source/record hashes. Evaluation rejects mutable, mismatched or late publications.
Release notes remain editable and are only routing hints; scoring uses the immutable bundle.
[GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).

Release assets are outside Git history and Actions artifact expiry. They remain available while
the repository/releases are retained. This is not an independent backup: an owner can delete a
release or repository. Download verified copies periodically. Immutable tags cannot simply be
reused to disguise a deletion. Local candidates/downloads/reports live under ignored
`ml/artifacts/prospective-2026-27/`; no credentials, personal squads or joblib binaries are
uploaded or committed. The existing small portable Ridge metadata remains embedded for replay.

## Cost and limits

The repository is public and uses `ubuntu-latest`, a standard GitHub-hosted runner. Such runs
are free; no larger runner, paid database or external storage subscription is used. No Actions
caches or dataset artifacts are uploaded. Hourly checks fetch only calendar/catalogue and
release metadata; full histories are requested only for an unfilled eligible slot. Scoring
downloads only newly final GWs during scheduled runs. The explicit evaluation/sync commands
can download historical bundles on demand. [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

There are at most 96 forecast slots and 32 primary-GW actual slots for GW7-38, apart from
tracked horizon actuals within the same season. Expected storage is modest compressed football
JSON, increasing as histories grow; the runner enforces **16 MiB per bundle, 128 MiB decompressed
per record, and 512 MiB total archive assets**. It never automatically deletes older captures
to make room. A reached bound fails visibly and requires review. GitHub permits up to 1,000
assets per release and files below 2 GiB; our single-asset limits are deliberately much lower.
[Release storage limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas).

Changing repository visibility, runner type or GitHub pricing requires a fresh cost review.
Actions logs/summaries may expire according to repository settings; immutable release bundles
are the durable record. No failure dataset artifacts incur additional Actions storage charges.

## Setup and operation

For this repository, Actions is enabled, the default branch is `main`, and release immutability
has been enabled. The workflow explicitly grants **`contents: write`** for releases and the
monthly operational branch. The normal short-lived **`GITHUB_TOKEN`** supplies this permission;
**no repository secret, PAT, FPL credential or Assistant password is required**. Checkout does
not persist its credential. Only the original repository/default branch can run the writer.
The implementation and tests run on Python 3.13 with the standard library; there is no install
or training step. GitHub's bundled `gh` and `curl` provide authenticated storage and HTTP.

For another installation, enable Actions and **Settings > General > Releases > Release
immutability**, allow the workflow's contents-write permission, and review the frozen season,
GW start, repository and scheduling policy before using it. Changing this versioned policy
requires a new archive policy/tag version, not silently editing old hashes. Locally, `gh auth
login` is needed for publishing; public downloads can also use that existing CLI login.

Enable GitHub Actions failure notifications in your account's notification settings (prefer
failed runs only). No email/Slack integration or external notification credential is configured.
Network/inference/storage/keepalive failures produce failed runs and error annotations. Missing
individual slots produce warnings and appear in summaries. A whole missing primary-GW forecast
causes failed runs while future recording continues. It is never backfilled.

To acknowledge a permanent whole-GW gap after review, set the optional non-secret repository
variable `FPL_ACKNOWLEDGED_MISSING_GWS`, for example `7,8`. This suppresses repetitive failure
alerts for those GWs; warnings and evaluation exclusions remain, and no forecast is created.
It is empty by default. Incomplete reserved drafts are separate errors and require review.

From the repository root, with `PYTHONPATH=src` (PowerShell: `$env:PYTHONPATH='src'`):

```text
python -m ml.recording check
python -m ml.recording record
python -m ml.recording evaluate
python -m ml.recording sync
```

On Windows, use `--gh-executable "C:/Program Files/GitHub CLI/gh.exe"` if `gh` is not on PATH.
`check` is read-only. `record` applies current deadline gates; it is not a backfill command.
`sync` downloads/checks all published bundles and receipts without replacing existing bytes.
`evaluate` downloads only selected captures with official-final actuals, caches verified copies,
and writes ignored `evaluation.md/json`. It reports MAE, RMSE and prediction-minus-actual bias
for identical all-roster observations (**ML with fallbacks**) and the direct-Ridge paired
subset. The predetermined rule is **final > update > advance**, using only the primary GW.
It never selects a capture using results. Unknown scores/predictions are excluded explicitly,
and changed final deadlines cannot legitimise a late capture. This is evaluation, not tuning.

## Failure recovery

1. Inspect **Actions > Record prospective FPL forecasts > failed run/summary**. Check current
   official deadline, the reserved release and GitHub/FPL service status. Use **Run workflow >
   record**, or `gh workflow run record-fpl-forecasts.yml -f mode=record`, while a valid window
   remains. Collection time is the new actual time, never the nominal cron time.
2. Fully uploaded draft bundles resume automatically from their original bytes, including an
   ambiguous successful upload. They cannot be exchanged for a newer prediction. An empty or
   incomplete draft preserves its reserved original hash and fails explicitly.
3. If you retained the original local candidate, use
   `python -m ml.recording recover --bundle /path/to/original/capture.zip`. Recovery accepts only
   the reserved hash, before the deadline and publication safety margin. No `--clobber`, asset
   deletion or published-release edits are used by this code.
4. If upload failed before any complete asset reached GitHub and the ephemeral runner ended,
   its original bytes may be lost. The run's hash/error and draft reservation remain; **do not
   regenerate that historical candidate**. Preserve the gap. Later slots still get fresh,
   separately named captures. Incomplete or expired drafts may be removed manually only after
   reviewing their state; deleting a complete/published capture is not a recovery technique.
5. After the deadline, retry only storage checks/actuals. No earlier input is obtained from a
   later API response or invented timestamp. If cron is disabled, re-enable it in Actions;
   inspect the monthly operational branch and keep notification preferences current.

To replay a verified forecast bundle:

```text
python -m ml.recording extract --bundle /path/to/capture.zip --root ml/artifacts/replay-gw07
python -m fpl_forfeit.assistant.prospective --replay ml/artifacts/replay-gw07/forecast.json.gz
```

Use the generation code commit recorded in its manifest/release tag. The replay helper now
sorts serialized GW identifiers numerically (canonical JSON puts `10` before `7`); this fixes
schema-v2 replay and does not alter forecast generation. The first capture requires this small
replay fix when using its original generation commit. Replay checks both forecast methods
without fetching data or fitting models. These commands never overwrite a different extraction.

## Validation status

All **39 focused standard-library tests** pass for delayed windows, season resets, immutable storage,
source/asset hashes, provider timestamps, retries, original-draft recovery, final-only actuals,
unknown/zero/negative points, paired selection, changed deadlines and permanent gap reporting.
Existing live feature/model/fallback tests also pass. A current official-data planner check on
13 October 2026 identified **GW7 advance** with no gaps.

**Actual unattended runner/storage verification succeeded:** the automatic
[initial push run](https://github.com/MohilGarg/FPL-Scenario-Analyser/actions/runs/38415889769)
retrieved all 667 histories and generated the existing forecasts, completing at
**2026-10-13 14:44:39 UTC**. The first publication failed because GitHub's by-tag API omitted
the draft. A tested draft-listing fix allowed the
[next automatic push run](https://github.com/MohilGarg/FPL-Scenario-Analyser/actions/runs/38416397509)
to recover the **same uploaded bytes**, publish them at **14:47:39 UTC**, and create the
monthly operational branch. No new model inference replaced the original.

The [GW7 advance release](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw07-advance-v1)
reports `immutable=true`. Its 825,929-byte bundle has SHA-256
`f253b5f4c5560dd5821b2f9ec901e69a7c134b1dc574147373d0e0b88ecbd270`.
A fresh local download independently passed asset/source hashes, matched the approved model
parameters exactly, and replayed **all 667 players across both methods without network or
fitting**. GW7 has **470 direct Ridge predictions and 197 statistical fallbacks**. The stored
deadline is **17 October 2026, 10:00 UTC**; no past GW was backfilled. Portable-model byte hashes
can differ across platform newline formatting; canonical parameter data matched exactly.

The workflow is active and its automatic push/recovery path is verified. **The first hourly
cron firing and subsequent deadline transitions have not yet been observed** at this check.
GW7 is not final, so real prospective error metrics and the first official-final actuals
transition also remain unverified in production; those paths have focused tests. No extra
repository secret or immediate manual setup remains. Failure-notification preferences,
periodic archive backups and eventual season-end workflow disabling remain operator tasks.
