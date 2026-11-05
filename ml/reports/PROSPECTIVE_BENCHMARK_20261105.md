# Original pre-deadline forecasts: GW7–GW9, evaluated 5 November 2026

This report uses original immutable GitHub release bundles, independently verified on download.
It does not reconstruct historical inputs or download forecasts after their target deadline.
Official-final unmultiplied element points are the target, including zeros and negative scores.
All paired methods use the same player/GW observations and the same actual captured cutoff.
These three newly inspected deadlines are now development evidence, not an untouched final test.

## Final available archived run

| Method | Paired rows | MAE | RMSE | Mean prediction minus outcome |
| --- | ---: | ---: | ---: | ---: |
| Statistical | 2,004 | 1.0902 | 1.9988 | −0.1431 |
| Ridge with labelled statistical fallbacks | 2,004 | 1.0462 | 1.9898 | −0.2040 |
| Fixture component model | 2,004 | 1.1096 | 2.0188 | −0.1556 |
| Statistical on direct-Ridge subset | 1,399 | 1.4782 | 2.3394 | −0.2091 |
| Direct Ridge | 1,399 | 1.4152 | 2.3284 | −0.2964 |

A fixed pre-deadline selection pool uses statistical expected GW minutes **at least 45**,
without consulting actual minutes/points. Its 614 rows have statistical MAE **2.3503**, RMSE
**3.0645**, bias **−0.1929**; Ridge-with-fallbacks MAE **2.2716**, RMSE **3.0828**, bias
**−0.4843**. Hybrid RMSE is worse by 0.0183, approximate 95% interval [−0.0549, +0.0614].
The attractive full-roster MAE therefore does not establish better forecasts for realistic
selection candidates. The rule is a transparent proxy, not a definitive set of FPL choices.

The all-roster population is 667/668/669 players in GW7/8/9. No actual/prediction was missing
and no deadline mismatch was excluded. Direct Ridge covers 1,399/2,004 rows (69.8%); its
remaining rows are statistical fallbacks, so the full-roster hybrid is not pure Ridge.
The fixture model's full paired coverage does not imply that all component inputs are known;
its existing fallbacks and reconstructed-training limitations still apply.

| GW | Capture completed UTC | Hours before deadline | Statistical RMSE | Hybrid Ridge RMSE | Fixture RMSE |
| --- | --- | ---: | ---: | ---: | ---: |
| 7 | 17 Oct 04:34:33 | 5.424 | 2.0224 | 2.0293 | 2.0566 |
| 8 | 23 Oct 13:31:02 | 3.983 | 2.0830 | 2.0958 | 2.1245 |
| 9 | 31 Oct 05:51:11 | 5.147 | 1.8860 | 1.8352 | 1.8668 |

Ridge reduces MAE in all three weeks, but reduces RMSE in only one and increases its downward
bias. Its aggregate RMSE gain is 0.0090 points, with approximate paired whole-GW bootstrap
95% interval **[−0.0508, +0.0128]** for hybrid minus statistical RMSE. The fixture model is
worse by 0.0201, interval **[−0.0192, +0.0414]**, improving one of three GWs. Resampling uses
2,000 draws, seed 20261105, with each whole deadline kept together. Three deadlines give very
weak uncertainty estimates and cannot support promoting either challenger.

The existing statistical model has appearance Brier **0.1292**, log loss **0.4510**; start
Brier **0.0928**, log loss **0.2948** on all 2,004 rows. Its predicted appearance band
0.2–0.3 averages 0.2455 against observed 0.5229 (109 rows), and 0.1–0.2 averages 0.1437
against 0.3209 (296 rows). This is a concrete minutes/appearance calibration weakness.
Probability values of zero/one use 1e−12 numerical clipping only in log loss. Start/appearance
events mean any start/appearance during the GW. P60 is not derived from aggregated DGW minutes:
two 30-minute appearances are not a qualifying 60-minute fixture.

Statistical central-80% intervals cover **90.07%** of observed points, mean width **2.7819**,
proper interval score **5.1622**. Fixture intervals cover **90.37%**, width **2.7969**, interval
score **5.3668** (2,004 rows each). Inclusive coverage is affected by discrete points and many
nonappearing players; it is not proof of calibration for selection candidates. The proper score
is width plus ten times a tail miss at alpha=0.2. New captures additionally retain 0.1/0.5/0.9
quantiles for pinball loss; older captures have no invented quantiles. P60 can now be evaluated
when official per-fixture `explain` minutes establish the event, otherwise remains unknown.

## Deadline minus 24 hours

| Method | Paired rows | MAE | RMSE | Bias |
| --- | ---: | ---: | ---: | ---: |
| Statistical | 2,003 | 1.1159 | 2.0209 | −0.1385 |
| Ridge with fallbacks | 2,003 | 1.0796 | 2.0214 | −0.1990 |
| Statistical on fixture-covered subset | 1,336 | 1.1163 | 2.0157 | −0.1480 |
| Fixture component model | 1,336 | 1.1203 | 2.0211 | −0.1681 |

The latest eligible original captures are **91.3, 119.3 and 116.7 hours** before the respective
deadlines. They satisfy the 24-hour information limit but are stale proxies, not fresh T−24h
forecasts. The old v1 workflow froze the first advance-window run; no later data was backfilled.
Fixture components were absent from the original GW7 advance capture, reducing their paired
coverage. Hybrid RMSE difference is +0.0005, 95% interval [−0.0485, +0.0313], improving one
of three deadlines. This illustrates why final and early forecasts must remain separate.

## What has and has not improved

The reproducible improvements are honest cutoff selection, durable new-method/source capture,
coverage, calibration and paired uncertainty evaluation. New minutes states and external
ensembles were not in the original GW7–GW9 releases; their accuracy is **unmeasured** here.
New sources require future original cutoffs and official outcomes. This report does not claim
the new models improve decisions or accuracy. Existing statistical default remains appropriate
until the separately documented prospective promotion rule is met.

A real new GW10 pre-deadline capture completed **5 November 2026, 10:47:09 UTC**, using feature
cutoff **10:44:41 UTC**. Its 2,523,580-byte bundle holds all 670 official players, the statistical,
Ridge, fixture and experimental `minutes-states-v1` forecasts, plus 670 attributed normalized
rows each from FPL Cat and Sideline Quant. It passed source/forecast integrity checks and an
offline replay of all four methods and fixture projections. This is an original local capture
under ignored `ml/artifacts/prediction-upgrade-capture-20261105`, explicitly marked as generated
from a dirty development tree with each Python source hash recorded. It is not a committed-build
or production-deployment claim and has no settled target yet. The generation's original policy
hash remains supported after cadence was bounded for storage; original bytes were not changed.

No all-zero global player expansion is fabricated for historical data; this prospective
population comes from each actual pre-deadline official roster. Historical dataset coverage
and reconstructed schedule leakage remain separate issues in the historical reports.

## Reproduction

From repository root with `PYTHONPATH=src`, Python 3.12+, and `gh` authenticated for downloading
public release assets:

```text
python -m ml.recording evaluate --cutoff final
python -m ml.recording evaluate --cutoff t24h
```

On this Windows host use `.venv/Scripts/python.exe` and
`--gh-executable "C:/Program Files/GitHub CLI/gh.exe"` if needed. Network access must be
available; Linux GitHub Actions uses its existing short-lived token. Commands cache verified
original bundles under ignored `ml/artifacts/prospective-2026-27/archive` and write detailed
`evaluation.json/md` and `evaluation-t24h.json/md`, including per-GW metrics, calibration bins,
exact cutoff ages, coverage/exclusions and bootstrap intervals. They never fit or select models.

Archive sources: [GW7 final](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw07-final-v1),
[GW8 final](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw08-final-v1),
[GW9 final](https://github.com/MohilGarg/FPL-Scenario-Analyser/releases/tag/fpl-2026-27-gw09-final-v1),
and the respective original advance and official-final actual releases in the same archive.
