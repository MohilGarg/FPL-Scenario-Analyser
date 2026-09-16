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

Light mode is the default. The header provides a persistent dark-mode option. The layout is designed
for phones first and remains keyboard accessible on desktop.

League, view, selected scenario manager and strict tie-rule state are kept in the query string, so an
analysis can be bookmarked or shared. Recent league IDs are stored only in the browser.

## Demo mode

Use the **Demo** selector to test every important state without waiting for live football:

- **Early Gameweek** — broad scenario space and useful differentials;
- **Late Gameweek** — eight safe managers, four at risk and one fixture remaining;
- **Live match** — live minutes/points and focused scenarios;
- **Completed Gameweek** — a final loser with no remaining events.

These are deterministic Python domain states served by `GET /api/demo/{mode}`. They pass through the
same scoring, safety, exposure, solver, ranking and serialisation pipeline as a real league; the
frontend does not contain precomputed fake results.

## What counts as last?

The Settings control supports:

- **Tied for lowest counts** — the existing/default rule;
- **Strictly lowest only** — a tied bottom score is not a sole last-place result.

The rule is applied consistently to safety pruning, scenario validity and completed-state display.

## Safety and scenario model

The configurable practical safety envelope remains:

- minimum remaining contribution for one player: **-10**;
- maximum remaining contribution for one player: **+35**.

That range is applied once across the player's whole remaining Gameweek, including a possible Double
Gameweek. It is a conservative pruning envelope, not a theoretical football limit. Managers proven
safe within it are excluded from detailed scenario search. `UNRESOLVED` and `NO MODELLED PATH` are
never presented as `SAFE`.

The bounded scenario vocabulary includes appearances, clean sheets and their loss, goals, assists,
goal-plus-assist combinations, multiple assists, braces, hat-tricks, cards, penalty misses and
goalkeeper penalty saves. It enforces direct fixture consistency and ranks valid scenarios using a
transparent rarity/complexity heuristic. Ranking is not a probability model. Exact bonus/BPS and
every possible multi-return combination remain deliberately non-exhaustive.

Broad early-Gameweek states defer detailed scenario enumeration and point users to Differentials.
This keeps requests bounded and avoids pretending a handful of examples explains a huge state space.

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

## API

### `GET /api/league/{league_id}`

Query parameters:

- `scenarios=1..12` — examples returned per at-risk manager, default `3`;
- `ties=include|strict` — bottom-tie rule, default `include`.

The version 2 response contains Gameweek status/phase, fixture counts, summary status groups, manager
score and squad details, safe bounds, core conditions, ranked scenarios with share text, and the
focused effective-differential matrix.

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
safety bounds, tie rules, multi-return scenarios, diversity, all four demo phases, API structure,
caching and frontend theme/view structure.
