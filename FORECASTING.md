# Forfeit outlook, simulation and what-if model

The live analysis has three layers. A cheap summary supplies confirmed effective points,
bounded safety and 96-sample mean final scores first. A separate request runs 1,600 reproducible
shared-fixture samples for last-place estimates and sensitivity. The Scenario Explorer forces
football events and recalculates that model across every manager. The existing constraint-guided
exact solver remains a separate, candidate-specific late-Gameweek tool.

The statistical outputs are experimental **uncalibrated internal model estimates**. They are
useful comparisons under stated assumptions, not verified probabilities or player advice.
No xG model, external projection feed, paid API, transfer or captain recommendation is involved.

## Shared scoring and official data

`api.py` parses official bootstrap season statistics, season fixture results, current fixture
scorelines, live per-fixture scoring explanations and picks. `models.py` contains typed domain
inputs. `scoring.py` scores hypothetical fixture categories; `substitutions.py` resolves live and
hypothetical manager multipliers, chips, bench order, formation constraints and hits.

Every sample starts from official awarded **aggregated event points**. For each unfinished fixture,
the engine adds the difference between hypothetical final category points and the currently
observed category points. Completed fixtures, including the earlier fixture in a Double Gameweek,
are never rescored. Captain and vice-captain decisions use the entire Gameweek's minutes. A
zero-minute disciplinary appearance blocks an autosub; captain handover follows the separate
no-minutes rule. Officially final events use official entry event points minus that event's hits.
All finished matches can still have provisional points until FPL marks the event final.

The rules were checked against the official [2026/27 scoring table](https://www.premierleague.com/en/news/2174909),
[rules](https://www.premierleague.com/en/news/4661029),
[defensive contributions](https://www.premierleague.com/en/news/4361991/whats-happening-with-defensive-contribution-points-in-202627-fantasy)
and [bonus changes](https://www.premierleague.com/en/news/4679946/).

## Initial model, with explicit fallback inputs

* Team goals: independent home/away Poisson counts. Observed completed season scoring/conceding
  rates are shrunk towards the season league mean by eight matches. A fixture uses the average of
  its team's scoring and its opponent's conceding rates, with multipliers 1.1 home / 0.9 away.
  Without team history the fallback is 1.4 goals per team before that adjustment. Rates are capped
  to 0.25–4.0 for this model, not for the mathematical safety envelope.
* Player shares: observed season goals and assists per 90, shrunk by 450 minutes towards position
  priors. Goal priors are GK .003, DEF .06, MID .20, FWD .35 per 90; assist priors are .005, .07,
  .15, .10. These are transparent starter priors, not evidence of calibration. Goal times are
  shared; a scorer must be on the pitch, and an assist cannot go to that same player. Unowned
  roster members contribute an anonymous allocation weight scaled to the remaining on-field
  slots. A fully owned eleven cannot acquire an additional anonymous scorer.
* Appearances: starts divided by observed team matches, shrunk by four matches towards .8,
  with official availability percentages applied. Without history, .8 is used. Starting owned
  outfield players receive sampled 55/60/70/80/90-minute windows. Starting goalkeepers receive
  90 minutes unless minutes are explicitly selected. Non-starters can make short late cameos.
  Windows allow at most eleven simultaneous owned players and one goalkeeper per team.
  Anonymous teammates fill the remaining positions; this is not a verified starting-lineup model.
* Clean sheets and conceded deductions: shared goals affect players present at that goal's time.
  A defender ordinarily substituted after 60 can retain a clean sheet; a red-carded defender
  continues to count later conceded goals. New entrants cannot accumulate more minutes than the
  remaining regulation time. Live clocks use official fixture minutes where available, otherwise
  the maximum observed per-fixture player minutes. Stoppage time is excluded.
  The simulation does not model extra events once the observed regulation clock reaches 90.
* Live substitution uncertainty: public explanations do not supply on/off timestamps. Players
  whose minutes trail the observed clock by more than two are conservatively held at their current
  minutes. This can understate the involvement of a player who came on earlier and remains active.
  The exact solver uses the same conservative assumption, preserving the player's points without
  capping later opposition goals. In the explorer, explicitly selected final minutes can state
  that an active live substitute continues playing; a red-carded player cannot be extended.
* Bonus: a shared performance-weighted 3/2/1 approximation, including unowned competitors. It
  uses goals, assists, clean sheets and a random residual. It does **not** reproduce Opta BPS or
  BPS ties. Anonymous competitors vary with the modelled participating owned players rather than
  adding four fictitious competitors to a fully owned match. This still approximates anonymous
  substitutions and performance. Previously awarded live bonus is provisional and can change.
* Saves and defensive actions: Poisson rates from observed season totals with five-match priors
  (GK three saves, DEF seven defensive actions, MID/FWD five). DC awards cap at two per match at
  the official 10/12 thresholds. Yellow cards use an observed rate shrunk towards .14 per 90.
* Future red cards, penalties and own goals are omitted from simulation. Already awarded events
  are preserved. The exact solver has a separate, explicitly bounded vocabulary including these
  events; it does not use simulation frequencies or plausibility costs as probabilities.

Missing live breakdowns are frozen and counted in `coverage`. Zero contributions are filled only
when all official aggregate minutes **and points** are accounted for by other fixture explanations.
Missing data is never interpreted as a football blank. Model assumptions and coverage are shown
in the interface; baseline projection remains available if the risk request fails.
Single-fixture live aggregate statistics fill sub-threshold saves and defensive actions when a
zero-point explanation omits them. In a DGW those counts cannot always be separated by fixture;
unexplained sub-threshold live progress can then be understated rather than guessed.

## Estimates, ties and sensitivity

Mean final score is the projection; median and 10th–90th percentile score intervals are also
returned. The first 96-sample summary projection is replaced by the full 1,600-sample mean when
ready. Sample count and seed are visible. A fixed seed ensures repeatability for the same input;
it does not measure calibration or guarantee stability under changed inputs.

With `ties=include`, every tied-bottom manager counts as last, so probabilities can sum above
100%. `ties=strict` counts only outright last, so their sum can be below 100%. Outright-last and
tied-bottom probabilities are returned separately under both rules. Wilson 95% intervals describe
**sampling error only**. They exclude systematic model error. A zero observed frequency never
changes SAFE / AT RISK / NO MODELLED PATH / UNRESOLVED. SAFE still means only the configured
practical -10/+35 total remaining player-contribution envelope.

Decisive players compare mean effective exposure across the sampled bottom contest, including
possible autosubs and vice-captain handover. Their indicative swing is the 10th–90th percentile
remaining-player range multiplied by that exposure spread. The model also reports association
between above-average player returns and sampled last place; this is explicitly not a causal or
necessary condition. Fixture leverage uses each fixture's separate score contributions and
exposure differences within the bottom contest, avoiding repeated aggregate DGW ranges.
Beneficiary membership uses entry IDs, so duplicate manager names do not change attribution.
Higher/lower exposure groups compare with the contest average and cannot overlap.

## Scenario Explorer

Conditions support further goals/assists, no further attacking returns, nonappearance, team
clean sheets/concessions, exact final scorelines, final fixture minutes and late substitute
appearances. Conditions refer to real league-owned players and unfinished fixtures from the
saved generation. A blank permits normal appearance, clean-sheet, save, defensive and bonus points.

The model enforces selected player returns exactly, raises team goal totals as needed, allocates
distinct assists coherently and resamples other events. A goal plus assist by one player requires
two team goals. Scorelines cannot erase recorded goals. Completed results cannot be changed.
Contradictory conditions yield an actionable 422 response and the interface retains the baseline.
Maximum matching pairs forced scorers and assisters with shared playing windows. Unspecified
attacking-player windows are restricted to permit selected late assists; explicit disjoint windows
are rejected before sampling when the selected score cannot support separate goals. Equivalent
condition orders and reordered managers produce identical seeded samples. Each team, separately,
can have up to five explicitly selected substitute appearances.
Overview shortcuts preselect the player, fixture and goal/blank/clean-sheet event in the explorer.
Minute limits come from Python's fixture clock; a live cameo defaults to a duration that fits the
remaining time, and a player who has already featured cannot be selected as a new substitute.

These are **forced-event model comparisons**, not calibrated Bayesian probabilities conditional
on an event occurring. Other outcomes remain sampled. Same seed and sample count reduce some
comparison noise, but changed constraints consume random values differently: small score/risk
changes can be sampling noise. Tables show point and probability-point deltas against the full
unmodified baseline. Conditions are cleared when the league, source generation or tie rule changes.

Exact examples separately expose all outcomes and baselines, are sufficient only under those
assumptions, and do not claim necessity or minimum event count. Relevant dimensions are prioritised
using candidate-relative effective exposure weighted towards nearby opponents. Aggregate attacking
counts and defensive concession caps constrain fixture consistency; minute limits prevent a late
entrant reaching 60. A zero-minute disciplinary appearance currently leaves the exact search
unresolved; projections and simulation still use the correct scoring.

## API, caching and hosting

`GET /api/league/{id}?detail=summary` provides cheap scores/safety/projections.
`GET /api/league/{id}/outlook?source={fetched_at}&ties=include` runs/reuses the full risk model.
`POST /api/league/{id}/what-if?ties=include` takes:

```json
{"source":"the exact league.fetched_at value", "conditions":[
  {"fixture_id":123, "kind":"goal", "player_id":456, "value":1}
]}
```

Use `gameweek` for a selected event and `demo` for an existing deterministic demo. The source
generation is mandatory. In-memory sources are retained for at least ten minutes; expired sources
return 409 and require refresh. Forecast cache keys include source, tie rule, count and conditions.
Locks coalesce concurrent identical summaries and forecasts; cache responses are deep copies. Parsed
states are reused within retained generations. Within a simulation, legal multipliers are reused
for each manager's identical appearance/minute presence patterns, with points still scored afresh.
Forecast cache
holds at most 64 payloads. One backend worker shares these caches. There is no new hosting service,
database, persistent-disk requirement or paid dependency. POST is now allowed by CORS.
On Windows, API retrieval automatically uses system curl when present to avoid an observed
Python/OpenSSL process crash; Linux retains urllib. Both retain TLS verification, error mapping
and timeouts. `FPL_HTTP_TRANSPORT` can explicitly select `auto`, `urllib` or `curl`.

Browser requests have timeouts, cancellation and generation/revision checks. A stale response cannot
be attached to a newer league update. Scores/projections stay useful while risk or exact results load.
Current public classic leagues of arbitrary IDs are supported within the existing 50-manager
service limit. GitHub Pages serves only the SPA; Python remains on Render or another API host.

## Real snapshot evaluation

No past live states have been invented and no calibration accuracy is claimed. Capture future
real gameweeks at several times, then save their official final snapshot:

```powershell
python -m fpl_forfeit.evaluation record YOUR_LEAGUE_ID --directory snapshots
# Repeat record later during the same Gameweek, then compare the saved forecasts:
python -m fpl_forfeit.evaluation timeline snapshots/first-forecast.json snapshots/second-forecast.json --output snapshots/timeline.json
python -m fpl_forfeit.evaluation replay snapshots/league-...-input.json --samples 1600 --output snapshots/replayed.json
python -m fpl_forfeit.evaluation final YOUR_LEAGUE_ID --gameweek 6 --directory snapshots
python -m fpl_forfeit.evaluation evaluate --final snapshots/league-...-final-input.json snapshots/first-forecast.json snapshots/second-forecast.json --output snapshots/evaluation.json
```

Record filenames include real UTC source time, league and Gameweek. The evaluator requires an
official final event, matching league/Gameweek, all current members and forecasts recorded before
final retrieval. Version 2 records also include forecast creation time, input checksum, model version,
seed, assumptions, coverage and explicit recorded/replay provenance. Evaluation requires creation
within ten minutes of the captured source and before final retrieval. Replays and legacy records
without recording evidence are excluded; they remain useful for reproducibility, not proof of an
earlier prediction. It reports pooled and per-snapshot binary Brier/log loss, score error and
reliability bins. Timeline comparisons require matching league, event, model, membership and tie
rule. Repeated snapshots of one Gameweek are correlated; assessment across
many independent gameweeks is required before any calibration claim. Raw snapshots stay ignored
by Git. The existing `--snapshot` and `--save-snapshot` CLI paths remain available.

See [VALIDATION.md](VALIDATION.md) for measured tests, timings, official-data coverage and data readiness.
