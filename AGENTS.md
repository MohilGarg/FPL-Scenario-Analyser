# FPL Forfeit Analyser: contributor guide

## Purpose

FPL Scenario Analyser combines live last-place scenario analysis with descriptive private
mini-league analytics. The live workflow answers who can still finish last this Gameweek and which
realistic remaining-match scenarios cause it. Analytics describes completed Gameweeks and current
squad ownership in a separate area. Do not add predictive advice, transfer/captain recommendations,
xG models, price predictions, optimisation or generic global-rank dashboards.

## Correctness priorities

1. Official completed/live FPL element points and transfer costs.
2. Captain, vice-captain, Triple Captain and Bench Boost multipliers.
3. Zero-minute handling and legal automatic substitutions.
4. Shared-player effective exposure.
5. Conservative safe-manager pruning.
6. Distinct, football-consistent, plausibility-ranked scenario examples.
7. One tie-for-last rule applied consistently across safety, solving and final display.

Keep API parsing in `api.py`; domain code must remain testable with synthetic data or saved
snapshots and no network. Never interpret a human-facing “blank” as zero points. Normal appearance
and clean-sheet points still apply.

## Mathematical language

Football scores and negative events have no literal finite bound. Never silently call a manager
mathematically safe without naming the configured event envelope. The default practical safety
envelope is -10 to +35 for each player's total remaining contribution across the current Gameweek,
including a Double Gameweek. It is intentionally conservative about bench players and autosubs and
is not a literal theoretical or historical limit. `UNRESOLVED` must never be presented as `SAFE`.

## Development

- Target Python 3.12+ and use type hints.
- Prefer the standard library unless a dependency creates clear value.
- Run `python -m unittest discover -s tests -v` after domain changes.
- Add regression tests for every scoring-rule fix.
- Preserve raw FPL snapshots outside Git (`snapshots/*.json` is ignored).
- Scenario plausibility affects ranking only, never validity.
- Keep demo states deterministic and route them through the same Python analysis pipeline as live
  data. Do not hard-code analysed demo results in JavaScript.
- Preserve the four live views: Overview, Scenarios, Differentials and Managers, plus the visually
  separate Analytics view. All five stay in the same SPA. The website is
  light-first with an accessible optional dark theme; keep both palettes high-contrast and restrained.
- Broad early-Gameweek states should favour effective differentials over expensive, misleadingly
  narrow scenario enumeration.
- Serve a cheap league summary first. Additional scenario searches are candidate-specific; never
  increase the search limit for every manager when one user asks to see more.
- Keep comparison maths and core conditions in Python. Shared exposure must be compared across the
  selected managers, not inferred from the frontend's filtered differential table. Label assumptions
  around pending autosubs/captaincy, and distinguish pairwise conditions from whole-league proofs.
- Historical viewing is final results for current league members in the current season. Use official
  event points less that event's hits and official pick multipliers. Do not reconstruct old live states
  without saved snapshots or silently drop members with missing historical squads.
- Demo Mode, tie rules and theme belong in Settings. Do not foreground demos on completed results.
  Preserve the existing deterministic demos, but focus work on useful product features rather than
  expanding synthetic test/demo infrastructure. Detailed real live validation is a later step.
- Preserve SAFE / AT RISK / NO MODELLED PATH / UNRESOLVED as distinct states. Safety bounds must also
  allow already-scored bench and vice-captain points to become effective after a pending absence.
- Run the full Python suite and frontend build; use `frontend/check.py` for the optional actual-browser
  check of parsing, mobile/desktop layout and interactions. Keep screenshots outside `frontend/dist`.
- The root website must remain neutral: never load a default or remembered league automatically.
  Recent leagues are explicit shortcuts; league IDs/URLs and query routing remain supported.
- Analytics calculations belong in `analytics/calculations.py`, retrieval/caching in
  `analytics/service.py`, never in route handlers or JavaScript. Preserve the tested live engine.
- Historical scoring uses official final pick multipliers and aggregated event player points,
  reconciled against official raw event points. Unknown data is null with explicit coverage.
  Hits are deducted once, separately from counted positional/player contributions. Bench Boost
  is not unused bench; autosubbed points must not be counted as unused. Distinguish total multiplied
  captain points from additional multiplier points. Record every chip use, including repeats.
- Historical rankings require all current members' scores. Analytics uses competition ranks and
  includes all tied last and bottom-three cutoff scores, explicitly separate from the live tie rule.
- Load cheap score history first; enrich historical squads with bounded/coalesced background jobs,
  progress, long-lived resource caches and lazy section payloads. Do not refetch every squad on tab
  changes. Cache settings and limitations are documented in `ANALYTICS.md`.
