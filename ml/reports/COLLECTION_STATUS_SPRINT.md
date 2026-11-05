# Collection and deployment status — 5 November 2026

## Final activation update

[PR #3](https://github.com/MohilGarg/FPL-Scenario-Analyser/pull/3) merged at **14:05:57 UTC**
as `a9a3be7d47b62ccd821d7c19bf710eb4b4a01c79`. It changes only the recording workflow,
pinning **`c9fe62487ff44b927d7364bb72c58a906253c5f0`**. The statistical application default
and draft research/application PR #1 are unchanged. Both PR #3 validation jobs passed.

[Activated run 42041732410](https://github.com/MohilGarg/FPL-Scenario-Analyser/actions/runs/42041732410)
actually checked out `c9fe624`, passed **95 runtime/recording/selection tests**, and completed
the record/evaluation steps successfully at **14:06 UTC**, with no current due slot or
supplemental window and no errors. This is a push-triggered execution, not an unattended
scheduled-band capture. PR #1's application/research jobs also passed on the pinned implementation
([run 42041123139](https://github.com/MohilGarg/FPL-Scenario-Analyser/actions/runs/42041123139)).

A separate original six-method GW10 capture was generated from that **clean committed checkout**
and published as [rolling 20261105T140817Z](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw10-rolling-20261105T140817Z-v2).
Independent download verified GitHub immutability, all hashes, asset size and publication time;
the downloaded bytes exactly equal the original generated candidate. No earlier release was changed.
An offline replay reproduced **670 players and all six forecast methods exactly**, including
challenger fixtures and embedded metadata, without network or fitting.

| Final sprint capture | Verified value |
| --- | --- |
| Feature cutoff | 5 Nov 14:04:47.253603 UTC |
| Capture completion | 5 Nov 14:08:17.866751 UTC |
| Durable publication | 5 Nov 14:08:49 UTC |
| Target deadline / lead | 6 Nov 18:30 UTC / 28.362 hours |
| Bundle | 2,758,728 bytes; SHA256 `50bfa8a03d219feafb0fa7d0bd36e729e7944af6a5cfe6f62d35c023f6a1e049` |
| Internal methods | Statistical, Ridge with labelled fallbacks, fixture components, original minutes v1, fitted minutes, tuned empirical minutes |
| Coverage | 670 players in each method; both new methods ready |
| Extra durable challenger horizon | GW10 only, bounding storage; live conditional horizon remains separate |
| External coverage | Official `ep_next`, FPL Cat and Sideline each 670 players |
| Source times | Cat computed 13:55:07/retrieved 14:03:25; Sideline computed 03:35:03/retrieved 14:04:14 UTC |

The capture is explicitly labelled manual and outside automatic start bands. It is eligible
under the future T-24h limit but would be **4.362 hours old** at that limit if no later capture
arrives. Source computation freshness is distinct; Sideline's original computation is older.
Neither this new manual proof nor the green activated run establishes reliable unattended
execution in the narrow bands. Previously observed multi-hour cron gaps still apply.

The [public verification aggregate](FITTED_CAPTURE_VERIFICATION_SPRINT.json) records commit/model
hashes, per-method coverage and source times. Local originals, download receipts and replay logs
remain ignored under `ml/artifacts/fitted-capture-20261105/` and `snapshots/ml/checks/`.

## Earlier activation audit (13:44 UTC snapshot)

The following read-only audit was completed at **13:44 UTC** and is retained to distinguish
the first activation from the later fitted-model pin above. This separates collection activation,
remote evidence, PR validation and application deployment. The new fitted models in
the working tree are not implied to be active merely because collection is active.

## Active code and approved activation

Before this sprint, `main` was `91b8d79f7d44d18c1e12355ee714ed2fa84d4b37`.
Its successful scheduled collector used the original three v1 slots. The source/minutes
and supplemental-cutoff implementation existed only in PR #1.

The user-approved [PR #2](https://github.com/MohilGarg/FPL-Scenario-Analyser/pull/2)
was merged at **12:28:34 UTC**, merge commit
`ef653f43445e75b6e4832ad0d56998d92b988b7b`. It changes workflow files separately
from modelling and does not merge PR #1 or promote a prediction default.

The default-branch recording workflow now checks out the immutable evidence code
`cffb0b6d43471775ed54aa0e6ed087c7c418752f`. Its commands unset `GITHUB_SHA`, so
the embedded `code_commit` identifies the actual checked-out capture code rather
than the newer workflow merge commit. The pin supports statistical, Ridge,
fixture-component and `minutes-states-v1`, plus validated external forecasts.
It **does not contain the new fitted sprint challengers**. Those require a separate
reviewed pin update after their code/artifacts are committed.

[Run 42027541714](https://github.com/MohilGarg/FPL-Scenario-Analyser/actions/runs/42027541714)
executed the activated pinned workflow successfully at **12:29 UTC**. Its checkout
log independently identifies `cffb0b6`; it reported no due slot, no due supplemental
window, no errors and no legacy gaps. This is a successful manual workflow run,
not proof that a new scheduled cutoff window has already been captured.

## Remote immutable evidence

The separately requested original activation capture is published as
[GW10 rolling 20261105T123201Z](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw10-rolling-20261105T123201Z-v2).
A fresh independent download at **13:43:45 UTC** verified its release, bundle,
source and model hashes. It is published, `immutable=true`, and not a draft.

| Evidence | Verified value |
| --- | --- |
| Original capture completion | 5 Nov 12:32:01.146404 UTC |
| Feature cutoff | 5 Nov 12:31:09.591512 UTC |
| Asset created / release published | 5 Nov 12:34:22 / 12:34:29 UTC |
| Target deadline | 6 Nov 18:30:00 UTC |
| Lead at completion | 29.966 hours |
| Capture code | `cffb0b6d43471775ed54aa0e6ed087c7c418752f`, clean tree |
| Bundle size | 2,523,937 bytes |
| Bundle SHA256 | `f6caad7ff9dbd843c54c9606960704e529e2ddf4370e52290376239d504dde36` |
| Internal methods | Statistical, Ridge, fixture-components-v1, minutes-states-v1 |
| Official roster / each method | 670 players |

The context truthfully identifies an **explicit manual activation capture outside
the automatic bands**. Its actual timestamp is used; it is neither backdated nor
labelled a fresh final/T−24h run. Local verification receipts remain outside Git at
`ml/artifacts/activation-capture-20261105/verification.json` and
`ml/artifacts/activation-capture-20261105/read-only-audit/audit.json`. The remote
immutable release provides restart-independent evidence; local files are conveniences.

| Source | Rows | Computed UTC | Retrieved UTC |
| --- | ---: | --- | --- |
| Official FPL `ep_next` in original bootstrap | 670 | Unknown | Original official retrieval completed 5 Nov 12:30:09 |
| FPL Cat | 670 | 5 Nov 09:15:08 | 5 Nov 12:30:17 |
| Sideline Quant | 670 | 5 Nov 03:35:03 | 5 Nov 12:30:44 |

Cat/Sideline normalized batches retain attribution and redistribution permission.
The official bootstrap retains its actually observed expected-points fields;
unknown computation time stays unknown. Restricted manual imports and external raw
response caches are absent from the release.

GW10 is not settled. Its T−24h cutoff is **5 Nov 18:30 UTC** and final cutoff is
**6 Nov 18:30 UTC**; both were still future at this audit. The currently available
activation run is eligible for either limit but would be **5.966 hours old at
T−24h** and **29.966 hours old at final** if no later capture is obtained. These are
provisional ages, not claims about which final run will eventually be selected.
At original completion, Cat's computation was about 3.28 hours old and Sideline's
8.95 hours old. Source computation freshness is distinct from retrieval/capture age.

## Cutoff freshness and operational limits

Both settled evaluations were rerun against currently verified immutable remote
metadata, reusing hash-verified local originals. No remote state was changed.

| Settled GW | Final capture lead | T−24h capture lead | Age relative to T−24h limit |
| --- | ---: | ---: | ---: |
| 7 | 5.424 h | 91.256 h | 67.256 h |
| 8 | 3.983 h | 119.323 h | 95.323 h |
| 9 | 5.147 h | 116.654 h | 92.654 h |

These old T−24h rows satisfy the information limit but are stale advance-run
proxies. No new provider forecasts are inserted into those old releases. The
activated policy retains legacy slots and adds at most one capture-start opportunity
in each **26–24.5 h** and **2–0.75 h** band per GW, with actual completion and durable
publication checked independently. It retains the 512 MiB archive cap.

The workflow is configured for minute 17 of every UTC hour. Observed scheduled
executions before activation were **4 Nov 18:01**, **4 Nov 23:44**, and **5 Nov 06:54**;
this is not evidence of reliable hourly delivery. No post-merge scheduled capture
in either narrow supplemental band had been observed at this audit. A live read-only
check at **13:44:32 UTC** found no current due window, errors or legacy gaps. Therefore
the new bands are operationally enabled, while their unattended freshness and
reliability remain to be demonstrated. Cron delays can still leave a visible cutoff
gap; no capture may be recreated later to conceal it.

## PR checks, local validation and deployment

The merged `validate-pr.yml` has `pull_request` triggers for application, tests,
frontend, ML, packaging and workflow paths. It has `contents: read`, no release or
deployment steps, application tests/build/JS syntax, and a separate scientific job
using `ml/training/requirements.lock.txt`.

The [PR-triggered run 42028662733](https://github.com/MohilGarg/FPL-Scenario-Analyser/actions/runs/42028662733)
passed both `application` and `research` for PR #1's committed head
`23d68e872cf5b19783c38f2516d7f7e1bb5aa833` at **12:37 UTC**. It does not validate
later uncommitted modelling changes; those need their own local checks and a new
PR-triggered run after push.

Collection code is pinned independently of the web application. PR #2 did not
change application source, authentication or the statistical production default.
This audit does not assert a particular Render deployment/restart occurred; hosting
deployment state was not inspected. Local training/replay success, a green PR check,
remote collection and production model promotion are four separate claims.

## Reproduce the audit

From the repository root, with `PYTHONPATH=src`, Python 3.12+ and the existing
GitHub CLI authentication:

```text
gh pr view 2 --json state,mergedAt,mergeCommit
gh run view 42027541714 --log
gh pr checks 1
python -m ml.recording check --root ml/artifacts/collection-status-check
python -m ml.recording sync --root ml/artifacts/collection-status-sync
python -m ml.recording evaluate --cutoff final
python -m ml.recording evaluate --cutoff t24h
```

`check`, remote retrieval and evaluation are read-only against GitHub. `sync`
downloads and verifies originals without replacing a different local record. The
next reviewed collector pin must include the fitted challengers before they can
accumulate prospective evidence; this report does not silently activate dirty code.
