# FPL Scenario Analyser: contributor guide

## Purpose and scope

A finished, deliberately small product with two parts:

1. **Mini-league analysis (public).** Live last-place analysis (who can still finish last this
   Gameweek and why) and descriptive season analytics from completed Gameweeks.
2. **FPL Assistant (private).** Predictions, legal XI, captaincy, transfer comparisons and conditional
   chip values, only in `assistant/` behind server-side authentication.

Do not add predictive advice, transfer/captain recommendations or optimisation to the public parts.
Do not add model research, training pipelines, forecast-capture/recording infrastructure, archives,
releases of captured data, or scheduled evidence-collection workflows. The assistant ships its small
portable model JSON files in `src/fpl_forfeit/assistant/`; replacing one is a reviewed code change.
Keep documentation in `README.md` (public app, safety envelope, analytics, deployment) and
`ASSISTANT.md` (private assistant); do not add further top-level docs.

Navigation is Live, Season (analytics) and Assistant in one SPA. Live is a single page: bottom standings
with live points, SAFE / AT RISK per manager with the stated envelope, the players that separate the
managers at risk, and a click-through to each squad; a completed Gameweek shows only the final result.
Do not reintroduce public Monte Carlo simulation, exact-scenario enumeration, what-if explorers, pairwise
comparison or demo states. Prefer removing complexity over adding it, and never hard-code analysed
results in JavaScript.

## Correctness priorities

1. Official completed/live FPL element points and transfer costs.
2. Captain, vice-captain, Triple Captain and Bench Boost multipliers.
3. Zero-minute handling and legal automatic substitutions.
4. Shared-player effective exposure.
5. Conservative safe-manager pruning.
6. One tie-for-last rule applied consistently across safety, the final result and display.

Keep API parsing in `api.py` (raise `FPLDataError` for unexpected FPL data; internal bugs must not be
reported as FPL failures). Domain code must be testable with synthetic data or saved snapshots and no
network. Never interpret a human-facing "blank" as zero points. Normal appearance and clean-sheet points
still apply.

## Mathematical language

Football scores have no literal finite bound. Never call a manager mathematically safe without naming
the configured event envelope. The default practical envelope is -10 to +35 for each player's total
remaining contribution across the current Gameweek (including a Double Gameweek); it is conservative about
bench players and autosubs and is not a theoretical or historical limit. SAFE means some opponent cannot
catch the manager under that envelope; AT RISK only means last place cannot be ruled out, never a
probability. Safety bounds must allow already-scored bench and vice-captain points to become effective after
a pending absence.

## Analytics rules

- Calculations in `analytics/calculations.py`, retrieval/caching in `analytics/service.py`, never in route
  handlers or JavaScript.
- Historical scoring uses official final pick multipliers and aggregated event player points, reconciled
  against official raw event points. Hits are deducted once. Bench Boost is not unused bench; autosubbed
  points are not unused. Distinguish total multiplied captain points from additional multiplier points.
  Record every chip use.
- Unknown data is null with explicit coverage; never drop members with missing squads silently.
- Each Gameweek ranks only managers who have a score that week (at least two); show how many were ranked.
- Analytics ranks use competition ranks and include all tied last / bottom-three cutoff scores, separate
  from the live tie setting.
- Load cheap score history first; enrich squads in bounded, coalesced background jobs with progress and
  lazy section payloads. Do not refetch every squad on tab changes.

## Assistant rules

Every `/api/assistant/*` data or operation route is protected before validation or computation; only
session status and login are public. Never put private squads/results in public caches, embed credentials in
assets, or enable production access without a configured password hash and HTTPS origin. Login rate limiting
keys on the real client IP via `FPL_TRUSTED_PROXY` and the rightmost `X-Forwarded-For` entry only. Reuse the
shared football model and FPL scorer.

## Development

- Target Python 3.12+ with type hints; prefer the standard library.
- Run `python -m unittest discover -s tests -v`, `ruff check .`, `ruff format --check .` and the frontend
  build (`python frontend/build.py`) before finishing; CI enforces the same. Use `frontend/check.py` for the
  optional real-browser check (`--live-payload` replays a saved in-progress state). Keep screenshots
  outside `frontend/dist`.
- Add a regression test for every scoring-rule fix.
- Preserve raw FPL snapshots outside Git (`snapshots/*.json` is ignored).
- The site is light-first with an accessible optional dark theme. The root website stays neutral: never
  load a default or remembered league automatically; league IDs/URLs and query routing remain supported.
- Commit completed work on a branch, push, open a PR, and verify local and remote are in sync before
  reporting completion.
