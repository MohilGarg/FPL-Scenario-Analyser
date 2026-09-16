# FPL Forfeit Analyser: contributor guide

## Purpose

This repository answers one narrow question for a 12-person Fantasy Premier League friends'
league: who can still finish last in the current Gameweek, and what realistic remaining-match
scenarios make that happen? Last place triggers a forfeit. Do not broaden it into a generic FPL
dashboard.

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
- Preserve the four website views: Overview, Scenarios, Differentials and Managers. The website is
  light-first with an accessible optional dark theme; keep both palettes high-contrast and restrained.
- Broad early-Gameweek states should favour effective differentials over expensive, misleadingly
  narrow scenario enumeration.
