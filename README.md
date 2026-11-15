# FPL Scenario Analyser

FPL Scenario Analyser combines public last-place analysis and descriptive mini-league analytics
with a separate password-protected FPL Assistant. The public analyser combines confirmed scores
with labelled experimental forfeit forecasts. Personal lineup and transfer planning stay private.

**Live site:** <https://mohilgarg.github.io/FPL-Scenario-Analyser/>

Paste a public classic-league ID or standings URL to see who is currently bottom, who can still
finish there, who is practically safe, and realistic football routes to the final outcome. No FPL
login is required.

## Website

The lock-marked **FPL Assistant** entry opens the Python-hosted private dashboard in the same tab.
It offers cached all-player predictions for the next 1/3/5/6 Gameweeks, public deadline squad
loading, exhaustive legal XI/bench/captain selection, manual transfer plans, a bounded
single-transfer shortlist and conditional Bench Boost/Triple Captain comparisons. Private APIs
use server-managed sessions and remain disabled until configured. See [ASSISTANT.md](ASSISTANT.md)
for password setup, hosting, limitations and measured checks. No FPL account changes are submitted.

The root URL is a neutral home page. Enter a classic league ID or paste its FPL standings URL.
No league loads automatically, even if you have visited before. Recent leagues appear as explicit
shortcuts showing name and ID. The site title returns home. Light mode is the default; dark mode,
the live tie rule and secondary demos remain in Settings.

### Live analysis

The five live views are:

- **Overview** — current/projected bottom managers, estimated last-place risks, projected scores
  and ranges, decisive players/fixtures and a separate bounded safe group;
- **Scenarios** — an interactive football-event explorer with combined conditions, score/risk
  changes and reset/removal, followed by separate exact examples and manager comparisons;
- **Differentials** — meaningful remaining effective multipliers after captaincy, chips and current
  substitution state;
- **Simulations** — interactive last-place probability bars, projected scores with existing
  10th–90th percentile ranges, projected fixture checkpoints and recorded risk trajectories.
  Select managers to compare, with consistent colours across charts. History needs at least two
  compatible saved observations; unavailable history is explained. Score distributions are omitted
  because the API exposes no bins or samples. This view requests no extra simulations;
- **Managers** — all effective scores and expandable 15-player Gameweek squads for transparency.
  Captain/vice/Triple Captain badges are inline; there is no redundant multiplier column.

### Analytics

The separately positioned Analytics tab stays inside the same website, with **Summary**, **Gameweeks**, **Managers**,
**Players** and **Head-to-head** sections:

- weekly forfeit leaderboard, completed Gameweek ordering, score coverage and league trends;
- manager average/median, weekly rank, consistency, cumulative scores and transfer deductions;
- verified unused bench points, Bench Boost contribution, captaincy and repeat chip history;
- counted positional points and player contributions across this mini-league;
- current ownership/effective ownership, unique owners/captains and a clickable squad-overlap matrix;
- historical head-to-head records, distributions, ownership and captaincy trends.

Charts support hover/tap/focus and a data table; tables are sortable and current players searchable.
Detailed totals display the number of verified squads, not silently assumed zeroes for missing data.
Historical rank counts include all ties and do not change with the live scenario tie setting.
Definitions, sources, caching and limits are in [Analytics reference](#analytics-reference).

Share a specific section, for example:

<https://mohilgarg.github.io/FPL-Scenario-Analyser/?league=188263&view=analytics&section=gameweeks>

The site automatically changes emphasis for early, late, live and completed Gameweeks. During live
fixtures it refreshes every 90 seconds while the tab is visible, matching the backend cache rather
than repeatedly hitting FPL. A completed Gameweek shows the final loser and bottom standings instead
of empty scenario sections.

Light mode is the default. **Settings** contains the persistent dark-mode option, tie rule and a
secondary **Demo Mode** selector. The layout is designed
for phones first and remains keyboard accessible on desktop.

League, Gameweek, view, selected scenario manager and strict tie-rule state are kept in the query string, so an
analysis can be bookmarked or shared. Recent league IDs are stored only in the browser.

## Previous forfeit results

Use the **Gameweek** selector beside Refresh to view a completed previous Gameweek in the current
season. The current Gameweek is the default. Final standings use FPL's official Gameweek points,
minus that week's transfer cost. Manager cards include the historical squad, chip and official pick
multipliers when available. For example:

<https://mohilgarg.github.io/FPL-Scenario-Analyser/?league=188263&view=overview&gw=1>

History covers **current league members**, not a reconstruction of past membership. Player team and
position labels come from the current season catalogue. If a member has no published historical
squad, the site explains that results are unavailable rather than silently omitting them. Past live
states/scenarios and previous seasons are not reconstructed.

## What counts as last?

The Settings control supports:

- **Tied for lowest counts** — the existing/default rule;
- **Strictly lowest only** — a tied bottom score is not a sole last-place result.

The rule is applied consistently to safety pruning, scenario validity, comparisons, CLI output and
completed-state display. A strict bottom tie has no sole loser.

## Safety and scenario model

The configurable practical safety envelope remains:

- minimum remaining contribution for one player: **-10**;
- maximum remaining contribution for one player: **+35**.

That range is applied once across the player's whole remaining Gameweek, including a possible Double
Gameweek. It is a conservative pruning envelope, not a theoretical football limit. Managers proven
safe within it are excluded from detailed scenario search. `UNRESOLVED` and `NO MODELLED PATH` are
never presented as `SAFE`.

The bounded scenario vocabulary includes appearances, clean sheets and their loss, goals, assists,
goal-plus-assist combinations, multiple assists, braces, hat-tricks, own goals, cards, penalty misses
and goalkeeper penalty saves. It includes returns with clean sheets and live returns with a lost
clean sheet. [Official FPL position-specific scoring](https://www.premierleague.com/en/news/2174909)
is used, including ten points for a goalkeeper goal.

Direct fixture consistency and the selected tie rule determine validity. A separate rarity/complexity
heuristic ranks valid examples; it is not a probability model. Diversity groups ignore incidental
bookings/minutes-only changes. A shared team clean sheet or loss is ranked once, not as an
independent coincidence for every owned teammate. Distinct combinations that reach a narrow points
condition are retained, including clean sheet plus booking and assist plus missed penalty.
Future bonus/BPS, defensive-contribution
awards, every goal-concession deduction and every possible multi-return combination remain outside
the bounded vocabulary. Official points already awarded are always retained.

Core conditions and comparisons describe the current effective exposures. Pending starters, autosubs
and vice-captain takeover are explicitly conditional. A comparison against one manager alone does
not prove last place against the whole league. **Why safe?** explains the score bound and the opponent
who guarantees safety; already-scored bench/captaincy points are included when they may move.

Broad early-Gameweek states provide mean projected standings immediately, then load a separate
1,600-sample shared-fixture risk model. The explorer works with any number of remaining fixtures.
Automatic exact enumeration is still reserved for narrower states; an explicit per-manager request
can generate focused examples with disclosed baselines.
This keeps requests bounded and avoids pretending a handful of examples explains a huge state space.
The league summary loads before scenarios. At-risk managers are searched individually afterwards;
**Show more scenarios** only extends that manager's search, up to twelve examples. `NO MODELLED PATH`
means the bounded vocabulary was exhausted; `UNRESOLVED` means it was deferred or search-limited.

See [Forecast model](#forecast-model) for the assumptions, tie convention and API contract. These are
uncalibrated estimates: a sampled zero chance is never a safety proof. Official awarded points,
chips, captaincy, legal autosubs and hits remain the scoring foundation for every layer.

## Architecture

```text
GitHub Pages (static HTML/CSS/JavaScript)
                |
                | processed JSON
                v
FastAPI service on Render
                |
                +-- live cache (90 seconds)
                +-- score history (15 minutes), final squads (24 hours)
                +-- bounded historical enrichment with progress
                +-- public FPL API retrieval
                +-- existing Python domain and scenario engine
                +-- shared-fixture projections, risk and forced-event what-ifs
                +-- separate Python descriptive analytics
```

FPL requests happen server-side to avoid browser CORS problems. Scoring and scenario logic is never
duplicated in JavaScript.

Player forecasts and joint fixture outcomes are reusable through `fpl_forfeit.forecasting`,
independently of mini-leagues. The public analyser consumes the same shared model.
The private assistant's forecasting, optimisation and secure setup are in [ASSISTANT.md](ASSISTANT.md).

## Run locally

Requirements: Python 3.12+ and internet access for real FPL data.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
uvicorn fpl_forfeit.web:app --reload --port 8000
```

In a second PowerShell window:

```powershell
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
python -m http.server 8080 --directory frontend/dist
```

Open <http://localhost:8080>. API documentation is at <http://localhost:8000/docs>.

Useful deterministic URLs:

```text
http://localhost:8080/?demo=early
http://localhost:8080/?demo=late
http://localhost:8080/?demo=live
http://localhost:8080/?demo=complete
```

These existing development examples are accessible through **Settings → Demo Mode**. All four
states use the real Python pipeline, not hard-coded frontend results. They are secondary to real
league analysis and are not a substitute for mid/late-Gameweek real-world validation.

## API

### `GET /api/league/{league_id}`

Query parameters:

- `scenarios=1..12` — examples returned per at-risk manager, default `3`;
- `ties=include|strict` — bottom-tie rule, default `include`.
- `gameweek=1..38` — optional completed Gameweek in the current season; omit for the current one.
- `detail=summary|full` — `summary` skips scenario solving; `full` remains the backwards-compatible default.

The version 2 response contains Gameweek status/phase, fixture counts, summary status groups, manager
score and squad details, safe bounds, core conditions, ranked scenarios with share text, and the
focused and whole-league effective-differential matrices, conditional exposure notes and available
Gameweeks. Caches are separated by league, Gameweek, tie rule, candidate and search settings and
bounded in memory. Invalid/unpublished/historical-unavailable Gameweeks return a friendly 409.

### `GET /api/league/{league_id}/managers/{entry_id}/scenarios`

Accepts `scenarios=1..12`, `ties`, optional `gameweek` and `source=<league.fetched_at>`. Returns only
the selected manager's result plus league/cache metadata. Safe managers and completed Gameweeks are not searched. The
frontend retains the summary if this deeper request fails, with a separate retry control.

### `GET /api/league/{league_id}/compare?a={entry_id}&b={entry_id}`

Accepts `ties` and optional `gameweek`. Returns scores, gap, shared/cancelling players, multiplier
differences and the pairwise points condition. All calculation lives in Python, including strict ties.
For development, these two endpoints also accept `demo=early|late|live|complete`.

### `GET /api/demo/{mode}`

`mode` is `early`, `late`, `live` or `complete`. It accepts the same query parameters and never calls
the external FPL API.

### `GET /api/health`

Returns `{"status":"ok"}` for deployment health checks.

### Analytics API

All routes are under `/api/league/{league_id}/analytics/`:

| Route | Result |
| --- | --- |
| `summary` | Compact league summary, forfeit leaderboard, coverage and trends |
| `gameweeks?gameweek=3` | One completed GW, including unavailable members; omit filter for all |
| `managers` or `manager/{entry_id}` | Manager statistics and historical score series |
| `players` | Current ownership/similarity and verified season player contributions |
| `player/{player_id}` | Selected player's historical ownership/captaincy series |
| `head-to-head?a=123&b=456` | Historical comparison over common recorded weeks |
| `details` | Starts/reuses bounded enrichment; returns `loading`, `busy` or `ready` plus progress |

Section responses never wait for all historical picks. The browser polls `details` while showing
score history, then refreshes the selected section. Missing/reconciliation-failed detail is null;
warnings explain which manager/GW is affected. No scenario searches are run by historical analytics.

## Existing CLI and snapshots

The website is the main product, but the tested CLI remains available. The CLI-only league `188263` is stored in
`.fpl-forfeit.json`, so the normal command is:

```powershell
python fpl.py
```

Examples:

```powershell
python fpl.py 123456 --scenarios 8
python fpl.py --strict-last
python fpl.py --json
python fpl.py --save-snapshot snapshots/gw07.json
python fpl.py --snapshot snapshots/gw07.json
```

## Deployment

Frontend and backend deploy separately; deploy the backend first so its URL can go into the Pages build.
Nothing needs a database, persistent disk, FPL login or secret (the optional private assistant needs a password hash).

**Backend (Render).** *New -> Blueprint*, connect the repository and apply the root `render.yaml`. It installs the
package, runs `uvicorn fpl_forfeit.web:app --host 0.0.0.0 --port $PORT`, health-checks `/api/health`, allows the
Pages origin through CORS, caches FPL results for 90 s and sets `FPL_TRUSTED_PROXY=1` so login rate limits use the
real client IP. Keep the single Uvicorn worker (caches and sessions are in-process). The free plan sleeps when idle;
the first request after sleep takes about a minute. A paid always-on instance removes that delay without code changes.
Any Python host works if it runs the same command and sets `FPL_ALLOWED_ORIGINS`.

**Frontend (GitHub Pages).** Set *Settings -> Pages -> Source* to GitHub Actions and run *Deploy frontend to GitHub
Pages*. It builds with `API_BASE_URL` (default `https://fpl-scenario-analyser-api.onrender.com`; override with a
repository variable, public configuration only). Re-run it manually after changing the variable.

**Smoke check.** `/api/health`, `/api/league/188263`, `/api/league/188263/analytics/summary`; the root site must
show the neutral input, never a remembered league.

**Local rehearsal.**
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
uvicorn fpl_forfeit.web:app --reload --port 8000
# second terminal
$env:API_BASE_URL="http://localhost:8000"; python frontend/build.py
python -m http.server 8080 --directory frontend/dist
```
Windows uses the system `curl.exe` transport automatically; override with `FPL_HTTP_TRANSPORT=urllib|curl`.

## Tests and build

### Scenario searches and playing-time context

The summary stays cheap. Late-Gameweek candidate searches run separately and request three distinct,
whole-league-checked examples by default. An early/broad state also supports an explicit **Generate 3
scenarios** request for one manager; non-searched players retain disclosed baseline contributions,
not implicit zero minutes. Fixed legal-lineup score-gap bounds prune impossible outcome combinations
before the existing final scorer verifies every example. The search budget is bounded; fewer than
three materially different routes can exist, and that shortfall is reported rather than filled with
duplicates. **Can finish last** requires a verified example. **Unresolved**, **No modelled path** and
**SAFE under the configured envelope** remain distinct.

Manager scenario requests accept `source=<league.fetched_at>` so every card uses the displayed scores.
Live snapshots still refresh on the normal 90-second cache, while source generations are retained for
10 minutes (at most 32 in memory). An expired source returns HTTP 409 with a refresh instruction,
not a silent mixture of scores or an endless refresh loop. Process restarts can expire sources early.

Before kick-off, ranking uses public FPL `status`, injury/news text, `chance_of_playing_this_round`,
season `starts` and `minutes`. Season fixtures provide a team-match denominator, including doubles;
that optional feed is cached for an hour and never replaces the fresher current-Gameweek score feed.
counts can differ after a club transfer or while FPL updates. Unknown values remain unknown. A price
adjustment is capped at 0.3 heuristic cost units and only supports existing minutes evidence; it cannot
make an expensive substitute a reliable starter. Healthy regulars' absence/cameo routes rank lower,
and injury flags lower the cost of no-appearance assumptions. Live observed minutes take precedence
over pre-match availability heuristics. None of this changes scoring, the safety envelope or route
validity, and it is not a trained probability model or a confirmed starting lineup.

Expand **Playing-time context** on a scenario, or the availability label in Managers/Differentials,
to inspect season starts, minutes, price and FPL news. FPL's chance of playing is **not** a chance of
starting. A no-appearance route requires **zero minutes**, not simply starting on the bench. Historical
views label this metadata as current, not reconstructed availability at a past deadline.

### Verification commands

```powershell
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
```

The suite covers scoring and autosub edge cases, hits and chips, Double Gameweeks, whole-Gameweek
safety bounds, tie rules, multi-return scenarios, diversity, manager comparisons, historical data,
all four existing demo phases, API structure, caching and frontend structure.
Analytics tests cover historical ties/ranks/coverage, averages, hits, bench/autosubs/Bench Boost,
captain/Triple Captain/vice takeover, repeat chips, positions, ownership, overlap, head-to-head,
league-player contribution, caching, API serialisation and invalid IDs.

Optional browser checks (with the local API and frontend running):

```powershell
python -m pip install playwright
python frontend/check.py
python frontend/check.py --simulations-only
python frontend/check.py --league 188263
python frontend/check.py --scenario-league 188263
python -m frontend.check_assistant --browser-executable 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
```

On Windows this uses installed Microsoft Edge; otherwise install a Playwright Chromium browser
(`python -m playwright install chromium`) or supply `--browser-executable`. Checks include real JS
league-ID/URL parsing, neutral home with and without recent leagues, six views at 320/390/1440px,
theme persistence, comparison, manager-only Show more and completed-state UX. The optional `--league`
checks real historical Analytics sections, shared URLs, charts, ownership search and overlap in
both themes. It makes public FPL requests through the backend. Screenshots go to a temporary directory, never the Pages
build. Playwright is not a production dependency.
`--scenario-league` additionally checks live candidate loading, displayed example counts and playing-time
context. All browser runs check that early-Gameweek generation searches only the selected manager.

## Analytics reference

Analytics is descriptive and separate from live scenario solving. Calculations live in
`src/fpl_forfeit/analytics/calculations.py`; retrieval and caching in `analytics/service.py`.

**Sources** (server-side, no FPL login): `bootstrap-static` (events, players), `leagues-classic/{id}/standings`
(membership, at most 50 managers), `entry/{id}/history` (event points, transfer costs, every chip event),
`entry/{id}/event/{gw}/picks` (final multipliers, captain/vice, chip, raw score) and `event/{gw}/live`
(official player totals, already aggregated across Double Gameweeks). Detailed contributions are published
only when all 15 player totals/multipliers exist and their counted sum matches the official raw event
score; otherwise they stay unavailable with a manager/GW warning.

**Accounting**
- *Weekly score:* official raw GW points minus that week's transfer cost, once.
- *Rank:* competition ranking (60, 50, 40, 40 -> 1, 2, 3, 3). All tied lowest scores count as last; the
  bottom three includes every tie at the third-lowest score. These descriptive rules are separate from the
  live tie setting.
- *Coverage:* each week ranks only the managers who have a score that week (late joiners and missing records
  are not ranked for weeks they lack); last place is the lowest of those, with all ties counted. A week needs at
  least two scored managers, and the number ranked is shown per week.
- *Last-place margin:* second-lowest minus lowest score; a bottom tie has margin zero.
- *Bench:* final bench-slot points with multiplier zero; autosubbed points do not count. Bench Boost
  slots 12-15 are recorded separately as its direct contribution.
- *Captain:* total multiplied contribution is separate from additional multiplier points (8 base doubled is
  16 total, +8 additional). Vice takeover receives its actual points. Every chip use is recorded.
- *Ownership:* owners / league managers; effective ownership sums current effective multipliers over
  manager count and may exceed 100%. Live substitutions and vice takeover remain provisional.
- *Head-to-head, consistency, trends:* use only weeks recorded for the managers concerned; unknown squads
  are never treated as non-owners.

**Retrieval and caching.** The summary fetches score histories only; one shared background job then verifies
final squads, with lazy section payloads and progress (`analytics/details`: loading / busy / ready).
Defaults: live snapshots 90 s, score histories 15 min, final picks and player totals 24 h, partial retry
5 min, two background league jobs, four concurrent upstream requests, 240 s fetch budget. Requests and jobs
are coalesced; caches are bounded, per-process and lost on restart (single-worker free Render).

**Limits.** Current league members and current season only; completed Gameweeks only; dashes mean unknown,
not zero; no transfer, captain or chip recommendations.

## Forecast model

The live analysis has three layers: a cheap summary (confirmed effective points, bounded safety, 96-sample
mean scores), a separate 1,600-sample reproducible shared-fixture risk run, and a forced-event What-if that
recalculates that model for every manager. The constraint-guided exact solver remains a separate,
candidate-specific late-Gameweek tool. Outputs are **uncalibrated internal estimates**, not verified
probabilities or player advice.

- **Code boundaries:** `api.py` parses official data; `forecasting/` holds the shared football model
  (statistics, joint fixture simulation, typed player projections); `points.py`, `scoring.py` and
  `substitutions.py` score fixtures, multipliers, chips, hits and legal autosubs; `forecast.py` adds
  official-score reconciliation and league risk; the private assistant reuses the same model. One fixture
  draw is shared by all managers.
- **Model:** independent home/away Poisson goals from season rates shrunk towards the league mean (home 1.1 /
  away 0.9); player goal/assist shares blend 65% official xG/xA with 35% observed returns, shrunk towards
  position priors; appearance and duration come from recent starts/minutes and FPL availability; shared goal
  times; clean sheets for players on the pitch; approximate 3/2/1 bonus (not Opta BPS); saves, defensive
  contributions and yellow cards from season rates. Future reds, penalties and own goals are not simulated.
  Missing live breakdowns are frozen and counted in `coverage`; unknown data is never read as a football blank.
- **Ties:** `ties=include` counts every tied-bottom manager as last (probabilities may sum above 100%);
  `ties=strict` counts only outright last. Wilson 95% intervals describe sampling error only. A zero sampled
  frequency never changes SAFE / AT RISK / NO MODELLED PATH / UNRESOLVED.
- **API:** `GET /api/league/{id}?detail=summary` (cheap), `GET /api/league/{id}/outlook?source={fetched_at}&ties=include`,
  `POST /api/league/{id}/what-if` with `{"source": fetched_at, "conditions": [{"fixture_id", "kind", "player_id", "value"}]}`.
  The source generation is mandatory; sources are kept at least ten minutes and expired ones return 409. Forecast
  caches hold at most 64 payloads and coalesce identical requests.
- **Boundary:** public routes never carry personal squads. Private planning lives only under `/api/assistant/*`
  behind server-side authentication (see ASSISTANT.md).
