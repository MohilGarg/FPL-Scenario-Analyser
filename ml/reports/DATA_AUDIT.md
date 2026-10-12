# Historical FPL data audit — Phase 1

Reviewed 12 October 2026. Source: [Vaastav Anand's FPL archive](https://github.com/vaastav/Fantasy-Premier-League),
locked to commit [`9779cdbc0c07f6c900c2d0c181ddf6bb9c800f88`](https://github.com/vaastav/Fantasy-Premier-League/tree/9779cdbc0c07f6c900c2d0c181ddf6bb9c800f88).
Only `gws/merged_gw.csv`, `players_raw.csv`, `teams.csv` and `fixtures.csv` were downloaded per season:
12 files, **19,084,171 bytes**. No external football sites were scraped.

## Coverage and preparation

| Season | Source history rows | Retained player-fixtures | Player-GWs | Players | DGW player-GWs | History columns |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023/24 | 29,725 | 29,725 | 28,742 | 865 | 983 | 41 |
| 2024/25 | 27,605 | 27,283 | 26,919 | 784 | 364 | 49 |
| 2025/26 | 29,757 | 29,747 | 29,338 | 841 | 409 | 46 |

All three have **GWs 1–38 and 380 finished fixtures**. Total output: **86,755 player-fixture
records and 84,999 player-GWs**. These include registered players' zero-minute records and are
not counts of appearances. Source coverage is not a complete historical eligible-player roster.
Absence of a row is unknown; blank GWs and pre-registration weeks are not backfilled as zero.
All retained GW groups reconcile to the observed club's final fixture list; no incomplete targets
were detected in this revision. This is final-result coverage, not archived live-state coverage.

The [source README](https://github.com/vaastav/Fantasy-Premier-League/blob/9779cdbc0c07f6c900c2d0c181ddf6bb9c800f88/README.md)
says weekly updates stopped after 2024/25, with start-of-season, January and end-of-season updates
thereafter. The downloaded 2025/26 history nevertheless contains every GW. Its coverage must not
be mistaken for weekly timestamped bootstrap snapshots.

## Column availability and missingness

The following counts cover source history rows **before** AM exclusion/deduplication. `0 missing`
does not prove accurate values or pre-deadline availability. Every original column/token is
preserved in `data/processed/source/<season>/`; generated `audit.md` lists every history column
individually and `audit.json` profiles every column of all four source files.

| History fields | 2023/24 missing | 2024/25 missing | 2025/26 missing |
| --- | --- | --- | --- |
| `total_points`, `minutes`, `starts`, `goals_scored`, `assists` | 0 each | 0 each | 0 each |
| `expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded` | 0 each | 0 each | 0 each |
| `clean_sheets`, `goals_conceded`, `saves`, `bonus`, `bps` | 0 each | 0 each | 0 each |
| `own_goals`, `penalties_missed`, `penalties_saved`, `red_cards`, `yellow_cards` | 0 each | 0 each | 0 each |
| `influence`, `creativity`, `threat`, `ict_index` | 0 each | 0 each | 0 each |
| `name`, `position`, `team`, `element`, `fixture`, `round`, `GW`, `opponent_team`, `was_home`, `kickoff_time`, `team_a_score`, `team_h_score` | 0 each | 0 each | 0 each |
| `value`, `selected`, `transfers_in`, `transfers_out`, `transfers_balance`, `xP` | 0 each | 0 each | 0 each |
| `clearances_blocks_interceptions`, `defensive_contribution`, `recoveries`, `tackles` | Absent | Absent | 0 each |
| `modified` (boolean; not a publication timestamp) | Absent | 0 | 0 |
| Seven `mng_*` Assistant Manager fields | Absent | 14,178 / 27,605 each | Absent |

All 41 base history columns exist in every season. The later extra fields account for the
49/46-column schemas. Earlier defensive-action fields remain null in normalized Parquet,
rather than fabricated zeros. `expected_goal_involvements` overlaps xG+xA and must not be added
to them as a separate quantity. FPL assists and xA measure different things; neither is a
one-to-one substitute for the other. Player xGC/clean-sheet counts describe the player's exposure,
not simply the final team's conceded goals or whether the team finished with a clean sheet.

`players_raw.csv` has 88/103/105 columns and 865/804/841 elements, respectively. It contains prices,
status/news, availability probabilities, set-piece orders, cumulative statistics, per-90 metrics,
form, points per game and selection percentages. Useful fields exist **only as the downloaded
season snapshot**, not as trustworthy historical pre-GW values:

| Snapshot field | 2023/24 missing | 2024/25 missing | 2025/26 missing |
| --- | ---: | ---: | ---: |
| `chance_of_playing_this_round` and `chance_of_playing_next_round` (each) | 208 / 865 | 201 / 804 | 211 / 841 |
| `status`, `now_cost`, `form` (each) | 0 | 0 | 0 |
| `news` | 548 / 865 | 561 / 804 | 563 / 841 |
| `penalties_order` | 813 / 865 | 754 / 804 | 781 / 841 |
| `team_join_date` | Absent | 99 / 804 | 19 / 841 |
| `opta_code` | Absent | 0 | 0 |

Empty news is not necessarily defective; null playing chances do not mean zero chance.
Fixture difficulty, team strengths and completed fixture scores are preserved for auditing,
but their snapshot values are not automatically pre-match features.

## Scoring and definition changes

- **2023/24 → 2024/25:** goalkeeper goals changed from 6 to 10 FPL points. Penalty-save BPS
  changed from 15 to 9; conceding GK/DEF goals gained a −4 BPS penalty; goalline clearances,
  fouls won and shots on target gained BPS rewards. These are BPS changes, distinct from
  direct FPL points. [Official 2024/25 changes](https://www.premierleague.com/en/news/4058895).
- **2024/25:** 20 Assistant Manager elements contribute 322 history rows. Their AM scoring
  is not player scoring; exclude them from targets and preserve their source records.
- **2025/26:** outfield defensive contributions award 2 points per fixture at 10 CBIT for DEF,
  or 12 CBIRT for MID/FWD, capped at 2 per match. These statistics are absent from earlier
  histories. [Official defensive-contribution explanation](https://www.premierleague.com/en/news/4361991/)
  (updated in 2026 and explicitly identifies the 2025/26 introduction).
- **2025/26:** assists rules relaxed intended-target/deflection and handball requirements.
  Historic assists cannot be reliably relabelled under the new rules from these CSVs.
  [Official assist changes](https://www.premierleague.com/en/news/4362187/whats-new-in-202526-fantasy-changes-to-assists-rules).
  BPS also changed shot-location saves, penalty saves, goalline clearances, penalty goals and
  tackles. [Official BPS changes](https://www.premierleague.com/en/news/4362127).

Targets retain recorded actual FPL points; no current scoring-rule reconstruction is applied.
DGW targets sum fixture **points**, retaining separate fixture statistics. Do not rescore a
GW's aggregated saves, concessions or defensive actions: rounding and thresholds apply per match.
Season/rule regime must be considered in evaluation, and unknown historical actions cannot be
invented to simulate newer rules.

## Identity and quality findings

- Ten **exact duplicate rows** in 2025/26: Ben Gannon-Doak GW1 and Junior Kroupi GWs 1–9.
  Original copies remain in source Parquet; normalized matches count each fixture once.
  Conflicting duplicates fail ingestion instead of selecting an arbitrary row.
- Evan Ferguson, 2024/25 element 123: match points sum to **27**, versus **28** in the season
  snapshot. The target retains recorded fixture points; this unresolved correction/coverage
  discrepancy is flagged rather than repaired from a final total.
- No fixture/opponent mapping conflicts, reported club-name mismatches, round/GW disagreements,
  missing/duplicate player codes or temporary codes were found in these downloaded files.
- Across these seasons, **841 element IDs** map to different player codes; **11 team IDs** map
  to different club codes. IDs must be scoped by season. Use `code`/team `code` as candidate
  longitudinal identities with manual review, not name joins. Names contain accents and variants.
- Match team is reconstructed from `fixture`, `was_home` and `opponent_team` and checked against
  season teams. Final `players_raw.team` is never backfilled into earlier matches after transfers.
  Final metadata still cannot prove a player's club/eligibility before a historic deadline.
- All 832 **2025/26 GW35** `xP` values are zero. The source warns `xP` may be post-match
  `ep_this`; this field is excluded completely. See the [source warning](https://github.com/vaastav/Fantasy-Premier-League#xp-column--potential-lookahead-for-ml-models).

## What can be reconstructed before a Gameweek?

| Information | Phase 1 treatment |
| --- | --- |
| Earlier completed-match points/minutes/starts/goals/assists/xG/xA/xGC/saves/bonus/cards; 2025/26 defensive actions | Historical aggregates are possible after a verified deadline/calendar cutoff; late source corrections remain an as-of-data limitation. |
| Recent appearance history and season-to-date sums | Rebuild from eligible earlier fixture rows; never use final season totals or current-GW matches. Missing coverage stays null. |
| Upcoming opponent, venue, fixture count and rest | Final schedule retained as metadata only. Need a schedule version available at the deadline to establish postponed/blank/double-GW knowledge. |
| Prices, ownership, transfers, form, availability, news and set-piece roles | Snapshot/post-GW timing unverified. Do not use same-GW fields or final bootstrap values as pre-match data. Even shifting these fields requires evidence of capture times. |
| Position/club/roster membership | Useful identity metadata; require historical registration and transfer evidence before using them to define prediction populations or blanks. |

**No official deadline calendar is supplied by the selected archive files.** Default output has
`feature_status=no_deadline` and all predictive feature values null. An optional sourced deadline
CSV enables only an explicit whitelist of earlier-GW result aggregates, requiring each kickoff
to precede the cutoff by more than 24 hours. This readiness delay is a conservative proxy;
it is not a verified finalisation timestamp. The full deadline contract is in `ml/README.md`.

Leakage hazards include same-GW outcomes/xG/BPS/ICT, either leg of the target DGW, final season
totals/per-90/form/ranks, retrospective player clubs and availability, source `xP`, final fixture
assignments/strengths and late stat revisions. Feature tests check target/future mutations,
season resets, numeric GW ordering, missing cutoffs/timestamps and DGW exclusion. Do not use a
random row split, or expose `observed_*`, target-quality metadata or all numeric columns to a model.

## Sample prepared player-Gameweek rows

| Season | Element | Player | GW | Fixture IDs | Target points | Minutes | Match xG sum |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: |
| 2023/24 | 6 | Kai Havertz | 1 | [2] | 2 | 90 | 0.05 |
| 2023/24 | 5 | Gabriel | 34 | [340, 281] | 12 | 180 | 0.00 |
| 2024/25 | 4 | Kai Havertz | 1 | [2] | 12 | 90 | 0.45 |
| 2024/25 | 8 | Jakub Kiwior | 33 | [326, 331] | 13 | 180 | 0.04 |
| 2025/26 | 1 | David Raya | 26 | [252, 310] | 3 | 180 | 0.00 |
| 2025/26 | 1 | David Raya | 38 | [373] | 0 | 0 | 0.00 |

All have `feature_status=no_deadline`; minutes and xG here are **observed outcomes**. The generated
18-row `sample.csv` and full-schema `sample.json` are available locally under `ml/data/processed/`.

## Missing or unreliable features

Verified historic deadlines; timestamped fixture schedules; pre-deadline prices/ownership/form;
historic injury/news/availability and set-piece roles; complete eligible-player/transfer timelines;
defensive actions for 2023/24–2024/25; exact result-publication/revision times; substitution/on-field
event timings; the detailed Opta actions needed to reproduce historic BPS; and trustworthy source
`xP`. These gaps prevent a fully faithful replay of the current statistical engine's information.

## Recommendations for Phase 2 (not implemented)

1. Obtain a small, sourced deadline calendar and verify schedule/roster snapshot availability.
   Resolve or explicitly quarantine the Ferguson discrepancy; review longitudinal player/club
   mappings. Set a documented treatment for blanks, new signings and incomplete histories.
2. Define a single prediction population, unmultiplied player-GW target and information cutoff.
   Build audited lagged/rolling features from completed matches, retaining coverage indicators
   and a scoring-regime flag. Fit imputation/normalisation only on training periods.
3. Use 2023/24 development, chronological 2024/25 validation and untouched 2025/26 holdout, with
   expanding-window GW checks. Acknowledge the defensive-scoring/assist shift and lack of
   older defensive-action labels. Report all-player and appearance/DGW/position subgroups.
4. Start with simple baselines before small ML models. Compare MAE/RMSE, bias and calibration
   using the same cutoffs and observations; preserve zero-minute players and separate unknowns.
5. Design an **offline** adapter to the existing `ForecastInputs`/shared simulator for a fair
   statistical-engine comparison, using reconstructed prior player/team results and explicit
   unavailable-input fallbacks. Do not reuse final season bootstrap totals or silently change
   the historical target to current scorer rules. Archive comparable forecasts and provenance.

Repository inspection: `models.py` has normalized Player/Fixture/appearance inputs;
`forecasting/models.py`, `statistics.py` and `simulation.py` share fixture draws, season xG/xA
and observed-rate estimates, recent appearances, team goal rates and uncertainty.
`points.py` scores current fixture events; `api.py` supplies official fields. Historical comparisons
will need the same information set at the cutoff, not a new live application data path.

## Validation

17 focused offline ingestion/leakage/Parquet tests passed. Basic validation checks cache hashes,
unique/numerically ordered keys, fixture-target reconciliation, default null features, restricted
feature-table columns and exact source-token Parquet round trips. The full application suite and
frontend build are intentionally outside this isolated Phase 1 change, as requested.
