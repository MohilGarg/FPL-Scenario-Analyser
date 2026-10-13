# Historical fixture and team features - Phase 3a

Reviewed 13 October 2026, continuing `937e116`. **Lagged team form is ready for an explicit
future modelling experiment. Upcoming fixture context remains research-only: verified
deadline schedule coverage is 0/114 Gameweeks.** No models were fitted or selected, and
the existing assistant, simulator, backend and website are unchanged.

## Source investigation and schedule gap

The existing pinned archive has **380 completed, scored fixtures and 20 teams per season**,
with no missing final kickoff times or GW assignments. Fixtures contain scores, venue, opponents,
final dates/events, FPL difficulty and event statistics. Snapshot difficulty and `teams.csv`
strength fields are excluded. Match records agree with fixtures on club, round, kickoff and
completion; no contradictions were found in the 86,755 normalized player records.

[Vaastav's repository](https://github.com/vaastav/Fantasy-Premier-League/tree/9779cdbc0c07f6c900c2d0c181ddf6bb9c800f88)
has older fixture versions, but the sampled Git history describes post-GW updates. Commit times
do not verify API collection times, freshness or intervening schedule changes. The pinned
[deadline mirror](https://github.com/beeradb/FPL-Armband/tree/4cbbe8b206686a6def6751966c640fbc673333a7/data/captures)
has 38 capture directories per requested season, containing bootstrap/deadline records and
manifests; **no fixture-endpoint payloads**. Three midseason manifests were inspected.
This investigation did not establish a complete immutable schedule history; it does not prove
that no such source could be recovered with further work.

The official Everton-Liverpool example illustrates the gap: the
[15 October schedule announcement](https://www.premierleague.com/en/news/4148310) listed
7 December 2024, 12:30 GMT; the [postponement notice](https://www.premierleague.com/en/news/4188928)
kept the GW15 deadline at 11:00 GMT; and the [13 January rearrangement notice](https://www.premierleague.com/en/news/4221175)
announced 12 February 2025, 19:30 GMT. Final archive fixture 144 belongs to **GW24**. These
dated articles do not supply precise immutable capture times or the complete FPL event schedule
at every intervening deadline. The final GW24 assignment cannot be substituted into December's
prediction inputs. Official season fixture pages can also be updated after initial publication.

References, reviewed revisions and findings are preserved in
[fixture-source-review.json](../fixture-source-review.json). Only bounded repository metadata
and small manifests were inspected; no extra football dataset was added or websites scraped.
The existing archive's [license](https://github.com/vaastav/Fantasy-Premier-League/blob/9779cdbc0c07f6c900c2d0c181ddf6bb9c800f88/LICENSE)
is MIT for software and explicitly distinguishes third-party data ownership; it is not treated
as an independent license grant for Premier League data.

## Approved historical indicators

Every team/GW cutoff is reconstructed from the same verified calendar. Only **same-season,
earlier-GW, completed fixtures with both scores and kickoff + 24 hours strictly before the
deadline** enter. Match windows follow actual kickoff order, so a rearranged old-round match
cannot enter before it is played. Either target-DGW leg is excluded. Final event IDs serve only
as an additional exclusion gate, never as evidence of an upcoming schedule.

There are **150 team indicators**: overall/home/away season-to-date and last-3/5/10-match goals
scored/conceded sums and means, full-match clean-sheet frequencies, smoothed rates, attack and
concession ratios, match counts and short-window flags. Team clean sheets are score-based,
not player 60-minute FPL clean-sheet points. Ratios are unadjusted descriptive form relative to
the eligible league; they are not fitted ratings or opponent-adjusted causal strengths.

[fixture-config.json](../fixture-config.json) fixes five pseudo-matches of smoothing:

`smoothed_rate = (observed_sum + 5 * pre_deadline_league_rate) / (observed_match_count + 5)`

League goal/clean-sheet priors use **only eligible results at that cutoff**, with corresponding
home/away denominators. Before any results, fixed neutral priors are 1.35 goals and 0.25 clean-sheet
frequency. Attack/concession ratios divide smoothed rates by their corresponding league rates;
**higher concession ratio means weaker defence**. A zero league scoring denominator uses a
neutral ratio of one. Zero-history raw statistics remain null, counts are zero and smoothed
values are explicitly prior-only. No previous-season history is inherited, including for
promoted clubs or reused numeric IDs. Counts/flags describe observed coverage, not proof that
every originally scheduled fixture or result vintage is available.

Known, buffered PL matches in the preceding 7/14 days and days from the last eligible kickoff
to the deadline provide historical congestion/recency indicators. They exclude domestic cups,
European fixtures and results inside the buffer. They are **not total football workload or
rest until the next fixture**.

The player sidecar contains **152 additional approved columns**: the team indicators associated
with the player's **last eligible observed club**, plus club-record age and a missing-club flag.
It never uses the target-GW club, final `players_raw.team`, current-GW opponent or future records.
This is a stale-club proxy, not verified current registration. Retrospective comparisons find
**25 / 31 / 29** proxy disagreements by season; those labels are separate quality metadata and
never repair features or filter rows. First observed histories remain unknown.

[fixture-context-v1.json](../schemas/fixture-context-v1.json) preserves the original 216-column
player schema and defines an **opt-in 368-column v2 whitelist**. Existing training implementations
and prepared v1 files remain unchanged. `approved_player_inputs` checks identical row keys,
ordering and cutoffs, then selects only approved columns; identity, outcomes and research/quality
fields are excluded. A future training adapter must explicitly opt into this version.

## Coverage and retrospective context

| Season | Team/GW rows | With observed history | Full 3 / 5 / 10-match histories | Player rows | Club proxy available |
| --- | ---: | ---: | --- | ---: | ---: |
| 2023/24 | 760 | 740 | 698 / 658 / 560 | 28,742 | 27,877 |
| 2024/25 | 760 | 740 | 700 / 660 / 560 | 26,919 | 26,135 |
| 2025/26 | 760 | 740 | 700 / 660 / 560 | 29,338 | 28,497 |

All 114 deadlines are verified under the earlier mirror/provenance limitation. GW1 team rows
are prior-only; all GW1 player-club features are missing. Home/away raw-rate coverage is lower
early in the season: **728/727, 730/730 and 730/730** team/GW rows, respectively. Corresponding
player coverage is **27,522/27,456; 25,820/25,834; 28,164/28,140**. Smooth rates can be non-null
without venue history; counts and flags distinguish this from observed evidence.

The reviewed [FIXTURE_COVERAGE.csv](FIXTURE_COVERAGE.csv) provides all **114 season/GW coverage
rows**. Generated `feature_coverage.parquet` records non-null and observed-history coverage for
every feature, season and GW. Before existing evaluation quarantine, 82,509 player rows have a
club proxy. Future experiments must retain the existing cold-start policy and Ferguson exclusion:
38 inconsistent 2024/25 rows, one also a cold start, leave the original **82,472 candidates**.
No new targets, roster weeks or blank-player rows are fabricated.

| Information | Treatment |
| --- | --- |
| Lagged team scores, home/away form, clean sheets and past PL congestion | Approved with deadline/buffer, coverage and revised-vintage limitations |
| Last observed player-club history | Approved as a labelled proxy; current membership is unverified |
| Final opponent, venue, GW fixture count, blanks/doubles and kickoff | Separate `research_only_final_assignment` tables; no approved input columns |
| Opponent's pre-deadline strength | Available for all clubs, but pairing it to a final upcoming opponent is research-only |
| PL rest from the prior eligible kickoff to a final upcoming kickoff, and between DGW legs | Research-only; final schedule may differ from what was announced |
| Fixture difficulty, snapshot team strengths, future schedules, player xP | Excluded |

Final-schedule tables preserve two club-side records per fixture and all team/GW rows, including
blanks and multiple legs. The archive has **23/23, 10/10 and 10/10** retrospective blank/double
team-GWs by season. For example, 2024/25 GW15 has two final blanks and GW24 two final doubles;
these counts do not certify deadline knowledge. Missing assignments/identities make retrospective
blank labels unknown, rather than asserting zero fixtures.

## Team xG/xGA and remaining limits

No team xG/xGA fields appear in the fixture columns or fixture-stat event lists. All **760 team-match
groups per season** have 11 recorded starts and known player xG, which is encouraging but does
not verify every substitute's inclusion, rounding or an authoritative team total. Player xG sums
are preserved only in `team_xg_quality.parquet`, with unverified substitute coverage. Missing
player xG makes the research sum null. **Team xG/xGA contributes no approved features.**
Player xGC describes individual exposure and must not be summed as team xGA.

Historical goals also come from final corrected files; the 24-hour buffer is an availability
proxy, not a recorded score-publication/revision time. Other gaps include pre-deadline club
registration/transfer timelines, cups/Europe and immutable fixture announcements or API captures.
2025/26 is prepared for coverage only, with no new model evaluation or model-selection use.

## Reproduce and next experiment

From the repository root, using the existing Parquet environment:

```powershell
ml/.venv/Scripts/python.exe -X utf8 -m ml.prepare_fixtures run
ml/.venv/Scripts/python.exe -X utf8 -m ml.prepare_fixtures validate
ml/.venv/Scripts/python.exe -X utf8 -m unittest ml.tests.test_fixture_features ml.tests.test_preparation ml.tests.test_readiness -v
```

Preparation works offline on the existing cached archive and takes about **29 seconds** on this
machine. Wide player rows are written in 512-row batches. Config/schema/code/source/data hashes,
deadline proof and output checksums are saved. All Parquet, quality metadata and samples remain
ignored under `ml/data/processed/fixture_context/` and `ml/reports/generated/fixture_context/`.
The small reviewed coverage CSV is a committed report, not a player dataset.

**52 focused feature/ingestion/readiness tests passed**, with no model fitting. They cover future
results and league-prior mutations, target/prior doubles, missing/blank schedules, the historical
derby rearrangement, buffer boundaries, promoted/short histories, season-scoped identities,
stale club proxies, incomplete results, conflicting kickoff records and the explicit v2 join.
Validation regenerates team/context/quality tables, checks hashes and scans all player sidecar
rows in bounded batches. Existing application tests/builds are outside this isolated phase.

**Next, not implemented:** predeclare a small Ridge ablation using a few lagged club goal,
clean-sheet, count and recency indicators against the unchanged Ridge benchmark on identical
2024/25 rows. Use chronological 2023/24 inner folds for any preprocessing/parameter decisions;
do not select on consumed 2025/26. Independently seek timestamped official fixtures snapshots
with capture-age and change provenance before experimenting with upcoming opponent/venue/rest.
Begin prospective collection for a genuinely new evaluation period.
