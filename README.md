# FPL Scenario Analyser

A focused website for Fantasy Premier League mini-leagues where the manager finishing last in a
Gameweek does a forfeit.

**Live site:** <https://mohilgarg.github.io/FPL-Scenario-Analyser/>

Paste a public classic-league ID or standings URL to see who is currently bottom, who can still
finish there, who is practically safe, and realistic football routes to the final outcome. No FPL
login is required.

## Website

The interface is organised around four views:

- **Overview** — the current loser, at-risk managers, core conditions and a collapsed safe group;
- **Scenarios** — focused ranked examples, final bottom scores, copy-to-chat text and an optional
  two-manager comparison;
- **Differentials** — meaningful remaining effective multipliers after captaincy, chips and current
  substitution state;
- **Managers** — all effective scores and expandable 15-player Gameweek squads for transparency.

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
bookings and minutes changes when identifying distinct routes. Future bonus/BPS, defensive-contribution
awards, every goal-concession deduction and every possible multi-return combination remain outside
the bounded vocabulary. Official points already awarded are always retained.

Core conditions and comparisons describe the current effective exposures. Pending starters, autosubs
and vice-captain takeover are explicitly conditional. A comparison against one manager alone does
not prove last place against the whole league. **Why safe?** explains the score bound and the opponent
who guarantees safety; already-scored bench/captaincy points are included when they may move.

Broad early-Gameweek states defer detailed scenario enumeration and point users to Differentials.
This keeps requests bounded and avoids pretending a handful of examples explains a huge state space.
The league summary loads before scenarios. At-risk managers are searched individually afterwards;
**Show more scenarios** only extends that manager's search, up to twelve examples. `NO MODELLED PATH`
means the bounded vocabulary was exhausted; `UNRESOLVED` means it was deferred or search-limited.

## Architecture

```text
GitHub Pages (static HTML/CSS/JavaScript)
                |
                | processed JSON
                v
FastAPI service on Render
                |
                +-- 90-second cache
                +-- public FPL API retrieval
                +-- existing Python domain and scenario engine
```

FPL requests happen server-side to avoid browser CORS problems. Scoring and scenario logic is never
duplicated in JavaScript.

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

Accepts `scenarios=1..12`, `ties`, and optional `gameweek`. Returns only the selected manager's result
plus league/cache metadata. Safe managers and completed/broad Gameweeks are not searched. The
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

## Existing CLI and snapshots

The website is the main product, but the tested CLI remains available. League `188263` is stored in
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

See [DEPLOYMENT.md](DEPLOYMENT.md). The static frontend deploys through GitHub Actions and the Render
Blueprint deploys the separate FastAPI service. Neither deployment contains a secret.

## Tests and build

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

Optional browser checks (with the local API and frontend running):

```powershell
python -m pip install playwright
python frontend/check.py
```

On Windows this uses installed Microsoft Edge; otherwise install a Playwright Chromium browser
(`python -m playwright install chromium`) or supply `--browser-executable`. Checks include real JS
league-ID/URL parsing, all four views at 320/390/1440px, theme persistence, comparison, manager-only
Show more and completed-state UX. Screenshots are written to a temporary directory, never the Pages
build. Playwright is not a production dependency.
