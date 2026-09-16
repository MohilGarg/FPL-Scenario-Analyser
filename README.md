# FPL Scenario Analyser

A focused website for a 12-person Fantasy Premier League friends' league where the manager who
finishes last each Gameweek does a forfeit.

Enter a public classic league ID to see:

- exact current effective Gameweek scores from official FPL points;
- who is currently last;
- who is conservatively `SAFE` and excluded from detailed scenario solving;
- which managers can still finish last;
- core points/differential conditions and ranked, football-consistent scenarios;
- useful remaining-player effective exposures earlier in the Gameweek.

This deliberately is not a generic FPL dashboard.

## Architecture

```text
GitHub Pages (static HTML/CSS/JS)
              │
              │ GET /api/league/{league_id}
              ▼
FastAPI service (Render or another Python host)
              │
              ├── cached public FPL API retrieval
              └── existing tested Python scoring and scenario engine
```

The browser never calls FPL directly and contains no duplicate scoring logic. `analysis.py` turns
the existing domain result into the structured payload used by both the web API and CLI JSON mode.
The API caches FPL snapshots and processed analyses for 90 seconds by default.

## Run the website locally

Requirements:

- Python 3.12+
- Internet access for live FPL data

Create an environment and install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

Terminal 1 — run the API:

```powershell
uvicorn fpl_forfeit.web:app --reload --port 8000
```

Terminal 2 — build and serve the static frontend:

```powershell
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
python -m http.server 8080 --directory frontend/dist
```

Open [http://localhost:8080](http://localhost:8080). API documentation is available at
[http://localhost:8000/docs](http://localhost:8000/docs).

## Command-line interface

The original CLI remains available. `.fpl-forfeit.json` stores league `188263`, so from the project
folder the normal command is:

```powershell
python fpl.py
```

Override the saved ID or request more scenarios when needed:

```powershell
python fpl.py 123456 --scenarios 8
python fpl.py --json
python fpl.py --save-snapshot snapshots/gw05.json
python fpl.py --snapshot snapshots/gw05.json
```

## API

### `GET /api/league/{league_id}`

Query parameters:

- `scenarios`: number of examples per at-risk manager, from 1 to 12 (default 3)

The response contains league metadata, current-last/at-risk/safe ID lists, processed manager cards,
ranked scenarios, remaining differentials, model settings and cache metadata.

### `GET /api/health`

Returns `{"status": "ok"}` for hosting health checks.

Only public FPL data is used. No FPL login, cookie or secret is required.

## Safety model

The default practical safety envelope is configurable in `settings.py`:

- minimum total remaining contribution for one player: **-10**;
- maximum total remaining contribution for one player: **+35**.

Each player gets that range once across the rest of the current Gameweek, including a possible
Double Gameweek—it is not multiplied per unfinished fixture. Captain/vice-captain potential and
bench/autosub uncertainty deliberately widen manager bounds.

These are conservative practical pruning settings, not literal theoretical or historical limits.
`UNRESOLVED` is never presented as `SAFE`, and safe managers are not sent through detailed scenario
search.

CLI overrides remain available as `--min-remaining-contribution` and
`--max-remaining-contribution`. The older `--*-points-per-fixture` spellings are retained as
compatibility aliases but now use the correct whole-Gameweek meaning.

## Correct scoring behavior

The engine uses FPL's official `total_points` for completed and live fixtures. It resolves effective
multipliers itself to handle:

- transfer costs;
- captain, vice-captain and Triple Captain;
- Bench Boost;
- Wildcard and Free Hit squads returned by the picks endpoint;
- goalkeeper and formation-legal outfield autosubs in bench order;
- zero minutes versus even one minute;
- shared player exposure and Double Gameweeks.

The scenario vocabulary covers appearances, ordinary no-return performances, clean sheets, goals,
assists, cards and goalkeeper penalty saves. It rejects direct football contradictions such as an
opposing attacker scoring while a defender keeps a clean sheet. Bonus/BPS and arbitrary multi-goal
combinations are not exhaustively enumerated. Scenario ordering is a plausibility heuristic, never a
validity rule or probability.

## Configuration

Backend environment variables:

- `FPL_CACHE_TTL_SECONDS` — positive cache lifetime, default `90`;
- `FPL_ALLOWED_ORIGINS` — comma-separated browser origins allowed by CORS.

Frontend build environment variables:

- `API_BASE_URL` — optional deployed backend origin override; the Pages workflow defaults to
  `https://fpl-scenario-analyser-api.onrender.com`;
- `DEFAULT_LEAGUE_ID` — optional pre-filled league ID, default `188263`.

No secrets belong in either value.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for exact Render and GitHub Pages steps. The expected project
site is:

`https://mohilgarg.github.io/FPL-Scenario-Analyser/`

The Pages workflow is `.github/workflows/deploy-pages.yml`. `render.yaml` describes the separate
FastAPI service.

## Tests and builds

```powershell
python -m unittest discover -s tests -v
$env:API_BASE_URL="http://localhost:8000"
python frontend/build.py
```

The offline test suite covers scoring edge cases, the -10/+35 whole-Gameweek bounds, Double
Gameweeks, API serialization, invalid league IDs, caching-facing response structure and frontend
configuration.

## Project layout

- `src/fpl_forfeit/api.py` — public FPL retrieval, snapshots and parsing
- `src/fpl_forfeit/models.py` — domain types
- `src/fpl_forfeit/substitutions.py` — autosubs, captaincy and scoring
- `src/fpl_forfeit/exposures.py` — shared/effective ownership
- `src/fpl_forfeit/safety.py` — conservative safe-manager proofs
- `src/fpl_forfeit/scenarios.py` / `solver.py` — event generation and scenario search
- `src/fpl_forfeit/analysis.py` — shared orchestration and structured serialization
- `src/fpl_forfeit/service.py` / `web.py` — cache and FastAPI layer
- `frontend/` — responsive static website and dependency-free build script
- `.github/workflows/deploy-pages.yml` — Pages deployment
- `render.yaml` — backend deployment blueprint
