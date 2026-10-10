# Validation and refinement — 10 October 2026

The existing shared-fixture architecture, five-view SPA, Analytics and Pages/Render hosting were
preserved. Synthetic inputs below are tests, not historical live snapshots or accuracy evidence.

## Bugs and refinements

- Greedy scorer/assist pairing rejected a valid two-goal condition set. Maximum matching now
  pairs distinct players with overlapping windows, independently of condition order.
- A goal plus a late-substitute assist with a one-goal score failed in **42/100 seeds**. Selected
  attacking windows now permit the assist; explicit incompatible windows reject before sampling.
  The fixed late-assist regression passed 250 seeds; the pairing regression passed 200.
- Manager order changed seeded samples through set iteration. Player iteration and conditions
  are canonical. Common hit shifts preserve score differences and risk.
- A fully owned eleven could acquire an anonymous scorer. Anonymous allocation now respects
  available slots and known reds. Bonus competitors also vary with participating owned players.
- Starting GKs used the outfield 55/60/70/80/90 duration mixture. They now receive 90 minutes
  unless constrained. Save points are restricted to GKs.
- DNP could contradict a zero-minute disciplinary appearance; this is rejected. The substitute
  limit applies separately to each team. An active live substitute can explicitly receive feasible
  final minutes, while reds cannot be overridden.
- Exact scenarios could revoke a subbed defender's earned clean sheet or add further events.
  The assumed off-field baseline now preserves points without capping later opposition goals.
- DGW fixture importance repeated aggregate player uncertainty. It now uses separate fixture
  contributions and exposure within the bottom contest.
- Beneficiary names misidentified duplicate names and could appear in both lists. Entry IDs and
  disjoint groups above/below contest-average exposure now determine membership.
- Four concurrent identical summaries computed four times. They coalesce once. Parsed states
  are reused; legal multipliers are cached per manager appearance/minute pattern within simulation.
  Point totals, captaincy, chips and hits still use the shared scorer.
- Replay forecasts lacked recording evidence. Version 2 adds creation time, input checksum,
  model/seed, assumptions, coverage and recorded/replay provenance. Evaluation rejects replays,
  legacy records without recording evidence and stale/late creation.
- Overview goal/blank/clean-sheet shortcuts preselect the event, player, fixture and team. Scenario
  conflicts and forecast errors are presented separately; forecasts remain visible on API failure.

## Tests actually executed

All **176 Python tests**, the frontend build, Ruff and whitespace checks passed. Tests cover full,
five-, three- and one-fixture states with legal 12/50-manager squads; live minutes/subs, retained
clean sheets, DNP/captain/vice takeover, Triple Captain, Bench Boost, hits, DGWs, completion,
configured bounds and both tie rules. API cases cover single/double goals, two teammates, goal plus
assist, clean sheets, late appearances, 2–1 scores, combined/conflicting conditions and caches.

Actual Playwright checks passed: neutral new/recent-visitor roots, ID/URL parsing, all five views at
320/390/1440 pixels, both themes, completed results, candidate-only searches, event preselection,
combined conditions/removal/reset and risk-request error recovery. Five/three/one-fixture synthetic
leagues displayed risk/decisive players and completed a goal comparison across all 12 managers.
A recorded official 14-manager/nine-fixture league passed too. Analytics real-network browser
enrichment was not rerun; Analytics Python tests and five-view browser navigation remained passing.

Official public FPL retrieval succeeded. In current input, **667 player totals** matched canonical
scoring. In completed GW5, **667 totals/explanations** and **12 available manager squads** matched
official points, hits, final pick multipliers and the domain scorer. **Two of 14 current members
lacked GW5 squads**, so full historical league retrieval correctly rejected the result. This explicit
partial point audit is not a complete historical league or a calibration test.

Two actual GW6 forecasts were recorded at 15:34:50 and 15:43:18 UTC. Replay reproduced scores,
risks and checksum exactly and retained replay provenance. The timeline included both records and
all 14 members. GW6 was unfinished, so evaluation against its actual final remains pending. Final
capture/evaluation wiring was tested with synthetic final input. Scoring was rechecked against
the official [points table](https://www.premierleague.com/en/news/2174909) and
[captain/autosub rules](https://www.premierleague.com/en/news/4661029).

## Numerical observations

The fixture audit checked **10,000 draws and 28,082 goal events**, with zero tested event/score/
window/clean-sheet/bonus consistency violations. Home goals: mean **1.5653**, variance **1.5723**,
configured Poisson mean 1.54. Away: mean **1.2429**, variance **1.2185**, configured mean 1.26.
GK starts were **92.02%** against the model's 92%; clean sheets given a start were **28.78%** against
28.37% opponent-goalless probability. This checks implementation, not calibration of input rates.

Another **64,000 league samples** tested stability/tie accounting: 4,000 per seed at 1/29/441 plus
a strict-tie run for each phase.

| Fixtures left | Largest manager risk span across seeds | Largest mean-score span | Exact examples for current bottom |
| --- | ---: | ---: | ---: |
| 10 | 0.87 percentage points | 0.55 points | 1 |
| 5 | 2.31 percentage points | 0.39 points | 2 |
| 3 | 1.63 percentage points | 0.40 points | 3 |
| 1 | 1.45 percentage points | 0.31 points | 1 |

Those exact searches used a 3,000-node budget and were incomplete: examples are not exhaustive
proofs. Inclusive probability sums were 108.48–111.02% because multiple managers can tie last;
strict sums were 89.84–91.98% because ties yield no sole last. Neither is renormalised. Sampling
intervals exclude model error. Sampled 0% never changes the separate proof/search statuses.

## Measured performance

One timed local Windows/Python 3.13 run per cold operation; network is excluded unless stated.
Synthetic states had 147/284 owned players. Timings showed substantial local variability and
production free-host CPU, cold starts and network latency can differ.

| Workload | 96-sample projection | Cold summary | 1,600-sample simulation before → after | 1,600-sample what-if | Cached outlook |
| --- | ---: | ---: | ---: | ---: | ---: |
| 12 managers, 5 remaining | 0.114 s | 0.320 s | 2.598 → 1.897 s | 2.951 s | 1.10 ms |
| 50 managers, 5 remaining | 0.698 s | 1.112 s | 21.092 → 9.067 s | 9.498 s | 2.86 ms |
| Saved official 14 managers, 9 remaining | 0.440 s | 0.403 s | 5.468 → 5.452 s | 5.722 s | 0.90 ms |

Four identical concurrent summaries computed once instead of four times: 1.82 → 0.43 s (12
managers), 5.90 → 1.61 s (50), 2.09 → 0.62 s (official input). Four identical full forecasts also
computed once. Four separate leagues took 1.16 s for cold summaries and 8.89 s for cold outlooks.
Single-candidate exact timings were 0.46–1.63 s; the five-fixture test reached its node budget,
while the other tested candidates had no path in their domains. Cached what-ifs took 0.89–3.49 ms.

Fresh public 14-manager/nine-fixture retrieval measured **2.681 s fetch + 0.149 s parse/summary =
2.830 s total**. Coverage reported team history, three owned players using priors and zero missing
live breakdowns. Peak traced Python allocations for a separate 50-manager/**400-sample** test
increased **1.88 → 3.73 MiB** with caches/fixture uncertainty; this is neither RSS nor a 1,600-sample
memory measurement.

## Official data readiness and remaining limits

Inspected official bootstrap contains xG/xA/xGI totals/per-90, starts/minutes, availability/news,
saves/DC and cards. Current inputs use observed goals/assists, starts/minutes, availability, saves/
DC/cards, team season goals/conceded rates and live scoring explanations. xG/xA/xGI remain unused;
no xG model was added. Cached official `element-summary` history could improve recent starts,
cameo frequency and durations. Aggregate minutes do not identify live on/off times. Per-fixture
official BPS could improve live bonus; current bonus is approximate and omits BPS ties.

Remaining model limits: independent team Poisson counts, coarse outfield durations/anonymous
lineups, uncertain priors, ambiguous DGW sub-threshold categories, conservative live freezing,
no events after regulation minute 90, and omitted future reds/penalties/own goals. What-ifs are
forced-event comparisons, not Bayesian conditionals; changed constraints can also introduce
comparison sampling noise. Exact search has a finite vocabulary; zero-minute-card cases remain
unresolved. Synthetic/property tests do not establish forecasting accuracy.

Potential future licensed providers, with no integration/new credentials:
[Sportradar lineups](https://developer.sportradar.com/soccer/reference/soccer-sport-event-lineups)
and [live timelines](https://developer.sportradar.com/soccer/docs/soccer-ig-live-match-retrieval)
describe starters/substitutions subject to coverage. [Stats Perform documentation](https://developers.statsperform.com/)
describes Opta feeds with lineups, substitutions and match events. They could address precise on/off
times, confirmed lineups and event/BPS inputs; coverage, licensing, latency and FPL-ID mapping would
need validation. No FotMob scraping, third-party integration or paid feed was added.

## Reproduction and next priorities

```powershell
$env:PYTHONPATH='src;.'
python -m unittest discover -s tests -v
python frontend/build.py --api-base-url http://127.0.0.1:8000
python tools/validate_simulation.py --output .browser-artifacts/simulation-validation.json
python tools/benchmark_forecast.py --output .browser-artifacts/benchmark.json --snapshot snapshots/YOUR-input.json
python tools/serve_validation.py --snapshot snapshots/YOUR-input.json
# Second terminal: python -m http.server 8080 --directory frontend/dist
python frontend/check.py --scenario-league YOUR_LEAGUE_ID --phase-leagues 900005 900003 900001
```

Next work, in impact order: (1) record many real Gameweeks at several times and evaluate against
official finals; (2) add bounded official recent-minute/start history and per-fixture live BPS,
then measure improvement on held-out results; (3) improve rare events, stoppage time and live
substitutions as reliable inputs become available. More samples alone cannot repair model error.
Five/three/one-fixture usefulness is supported now: projections/risk/exposure arrive before exact
searches, and selected football events update the entire league. The browser checks exercised it.
