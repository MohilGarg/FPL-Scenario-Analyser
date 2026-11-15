# FPL Scenario Analyser

Who can still finish last in your FPL mini-league this Gameweek, what separates them, and how the
season has gone so far. A separate password-protected FPL Assistant handles private squad planning.

**Live site:** <https://mohilgarg.github.io/FPL-Scenario-Analyser/>

Paste a public classic-league ID or standings URL. No FPL login is needed.

## Website

Navigation is **Live**, **Season** and **Assistant**.

The root URL is a neutral home page: no league loads automatically, even after a previous visit.
Recent leagues appear as explicit shortcuts. Light mode is the default; dark mode and the tie rule
are in **Settings**. The layout is phone-first and keyboard accessible.

### Live

One page for the current Gameweek:

- **Bottom standings** with live effective points (transfer deductions included), lowest first.
  Every manager is marked **SAFE** or **AT RISK**; the manager(s) currently bottom are flagged.
  Safe managers are grouped and collapsed.
- **Click-through squads:** open any manager to see their 15 players, points, minutes, remaining
  fixtures, captaincy, chips, pending autosubs and vice-captain takeover, plus the score range they can
  still reach under the safety envelope and why they are or are not safe.
- **What separates the bottom:** the remaining players whose effective multiplier differs between the
  managers who can still finish last, largest differences first. Shared exposure cancels and is left out.
- **How SAFE and AT RISK are decided:** the stated assumptions (see below).

When the Gameweek is complete the page shows only the final result: who finished last and the final
standings, each still clickable for the squad. During live fixtures the page refreshes every 90 seconds
while the tab is visible, matching the backend cache.

Use the **Gameweek** selector to view a completed previous Gameweek of the current season. Final standings
use FPL's official Gameweek points minus that week's transfer cost and official pick multipliers. History
covers current league members only; player team/position labels use the current season catalogue. A member
with no published historical squad produces an explicit error rather than being silently dropped.

League, view, Gameweek and the strict tie rule are kept in the query string, so a page can be shared, for
example <https://mohilgarg.github.io/FPL-Scenario-Analyser/?league=188263&view=live&gw=9>. Older links
using `view=overview` (or the other retired tabs) open Live, and `view=analytics` opens Season.

### Season

Descriptive analytics from completed Gameweeks, in **Summary**, **Gameweeks**, **Managers**, **Players**
and **Head-to-head** sections: the weekly forfeit leaderboard (each week ranks the managers who have a
score that week), manager averages and consistency, hits, bench points, captaincy and chips, positional
and player contributions, current ownership and squad overlap, and head-to-head records. Charts support
hover/tap/focus; tables are sortable. See [Analytics reference](#analytics-reference).

<https://mohilgarg.github.io/FPL-Scenario-Analyser/?league=188263&view=season&section=gameweeks>

### Assistant

The lock-marked **Assistant** link opens the Python-hosted private dashboard in the same tab: all-player
predictions, public deadline squad loading, legal XI/bench/captain selection, manual transfer plans, a
bounded single-transfer shortlist and conditional Bench Boost/Triple Captain values. It is disabled until a
password hash is configured. See [ASSISTANT.md](ASSISTANT.md).

## What counts as last?

Settings offers **Tied for lowest counts** (default) or **Strictly lowest only**. The rule is applied
consistently to safety, the final result and the CLI. A strict bottom tie has no sole loser. Season always
records every tied last place, independently of this setting.

## SAFE and AT RISK

Football scores have no literal bound, so safety is stated against a named practical envelope:

- minimum remaining contribution for one player: **-10**;
- maximum remaining contribution for one player: **+35**.

Each owned player with a fixture left receives one range across the rest of the Gameweek (including a Double
Gameweek), multiplied by the largest multiplier they could still carry (captain, vice-captain takeover, Triple
Captain). Every bench player may count, and already-scored bench or vice-captain points are allowed to become
effective after a pending absence. This deliberately over-states each manager's range.

A manager is **SAFE** when, even at their lowest possible score, some opponent cannot reach it at their highest
(with the selected tie rule). Otherwise they are **AT RISK**: last place cannot be ruled out under the envelope.
AT RISK is not a probability, and SAFE is a statement about this envelope, not a theoretical guarantee.

Scoring uses official live/final element points, transfer costs, captain/vice/Triple Captain/Bench Boost
multipliers, zero-minute handling and legal automatic substitutions. A human-facing "blank" is never read as
zero points.

## Architecture

```text
GitHub Pages (static HTML/CSS/JavaScript)
                |  processed JSON
                v
FastAPI service on Render
                +-- live league cache (90 seconds)
                +-- score history (15 minutes), final squads (24 hours)
                +-- public FPL API retrieval and parsing (api.py)
                +-- scoring, autosubs, safety and exposures (Python domain)
                +-- descriptive season analytics (analytics/)
                +-- private assistant under /assistant and /api/assistant/* (assistant/)
```

FPL requests happen server-side. Scoring and safety are never duplicated in JavaScript. The assistant's shared
football model lives in `fpl_forfeit.forecasting`; see [ASSISTANT.md](ASSISTANT.md).

## Run locally

Requirements: Python 3.12+ and internet access for real FPL data.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
uvicorn fpl_forfeit.web:app --reload --port 8000
```

In a second window:

```powershell
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
python -m http.server 8080 --directory frontend/dist
```

Open <http://localhost:8080>. API documentation is at <http://localhost:8000/docs>.

## API

### `GET /api/league/{league_id}`

- `ties=include|strict` — tie rule, default `include`;
- `gameweek=1..38` — optional completed Gameweek of the current season; omit for the current one.

The version 3 response contains Gameweek status and fixture counts; summary groups (`at_risk_entry_ids`,
`safe_entry_ids`, `current_last_entry_ids`, `last_place_decided`); each manager's score, status
(`safe` / `at_risk` live, `last` / `clear` once complete), score bounds, safety explanation and 15-player squad;
the separating players among at-risk managers (`differentials`, `differential_manager_ids`); available
Gameweeks; and the stated envelope (`model.safety_note`). Unpublished or unavailable Gameweeks return 409.

### `GET /api/health`

Returns `{"status":"ok", ...}` for deployment health checks.

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

Section responses never wait for all historical picks; the browser polls `details` and then refreshes the
selected section. Missing or unreconciled detail is null with a warning naming the manager/GW.

## CLI and snapshots

The CLI prints the same standings and SAFE / AT RISK statuses. The CLI-only default league is read from
`.fpl-forfeit.json`:

```powershell
python fpl.py
python fpl.py 123456 --strict-last
python fpl.py --json
python fpl.py --save-snapshot snapshots/gw07.json
python fpl.py --snapshot snapshots/gw07.json
```

Raw snapshots stay out of Git (`snapshots/*.json` is ignored).

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
show the neutral input, never a remembered league. Deploy both sides together: the Live page needs API version 3.

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

```powershell
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
```

The suite covers scoring and autosub edge cases, hits and chips, Double Gameweeks, safety bounds and
SAFE / AT RISK, separating players, tie rules, historical results, API structure, caching, frontend structure,
the season analytics and the private assistant (authentication, forecasts, optimisation and the shared
football simulator's invariants).

Optional real-browser check (local API and site running; Playwright is a developer-only dependency):

```powershell
python -m pip install playwright
python frontend/check.py --league 188263 --final-gameweek 9
python frontend/check.py --live-payload payload.json
python -m frontend.check_assistant --browser-executable 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
```

It checks the neutral home page, league-ID parsing, the Live page (standings, statuses, squad click-through,
separating players or final result), Season sections and old shared links at 320/390/1440px in both themes.
`--live-payload` replays a saved in-progress API response (produced by the Python analysis from a saved
snapshot) so the live layout can be checked when no Gameweek is in progress. Screenshots go to a temporary
directory. On Windows it uses installed Microsoft Edge; otherwise install Chromium with
`python -m playwright install chromium` or pass `--browser-executable`.

## Analytics reference

Analytics (the Season view) is descriptive and separate from the Live page. Calculations live in
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
