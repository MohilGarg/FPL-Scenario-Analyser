# FPL Scenario Analyser

A correctness-first command-line tool for a 12-person Fantasy Premier League friends' league where
the manager finishing last each Gameweek does a forfeit.

It reports:

- exact current effective Gameweek scores from official FPL element points;
- the current last-place manager(s);
- conservative `SAFE`, `CAN FINISH LAST`, and `UNRESOLVED` statuses;
- remaining effective player exposures, including shared ownership and captain multipliers;
- a core points-swing condition and a few distinct, ranked football scenarios for each manager with
  a found route to last.

This is intentionally not a general analytics dashboard.

## Requirements and setup

- Python 3.12 or newer
- Internet access when fetching live FPL data (offline snapshots need no internet)

No runtime packages are required. From the project folder:

```powershell
python fpl.py LEAGUE_ID
```

The league ID is the number in a classic league URL such as
`https://fantasy.premierleague.com/leagues/123456/standings/c`.

The analyser expects 12 entries by default and stops if a different number is returned. To inspect
another league size deliberately:

```powershell
python fpl.py 123456 --expected-managers 0
```

Useful options:

```powershell
# Fetch once and keep an offline/reproducible input
python fpl.py 123456 --save-snapshot snapshots/gw05.json

# Re-run without contacting FPL
python fpl.py --snapshot snapshots/gw05.json

# Ask for more distinct examples or a wider/deeper search
python fpl.py 123456 --scenarios 8 --max-relevant 10 --max-nodes 200000

# Machine-readable output
python fpl.py 123456 --json

# A tie for last counts by default; change to strictly last
python fpl.py 123456 --strict-last
```

For an installed command, create a virtual environment and run `pip install -e .`; the command is
then `fpl-forfeit LEAGUE_ID`.

## What “current score” means

The program uses FPL's official `total_points` for every player. It does not try to recreate points
for completed or live fixtures. It resolves the squad's effective multipliers itself so it can:

- subtract transfer costs;
- apply normal and Triple Captain multipliers;
- hand captaincy to the vice-captain only after the captain's team has completed all Gameweek
  fixtures with zero minutes;
- include every Bench Boost player;
- make goalkeeper-for-goalkeeper and formation-legal outfield autosubs in bench order;
- distinguish zero minutes from even one minute.

Wildcard and Free Hit need no special scoring branch: the picks endpoint already returns the active
15-player squad. Their chip names are retained in the output.

## Safety and scenario assumptions

“Mathematically safe” needs an explicit finite model: real football has no hard upper bound on goals
or hard lower bound on own goals/cards. The default proof envelope allows every owned player in each
unfinished fixture to add between **-8 and +20 points**, with captain multipliers. It also lets bench
players count, making the interval deliberately wider and safe pruning cautious. Change the envelope
with `--min-points-per-fixture` and `--max-points-per-fixture`.

Status meanings:

- `SAFE`: another manager remains below this manager throughout the configured conservative bounds.
- `CAN FINISH LAST`: the bounded scenario solver found at least one internally consistent route.
- `NO MODELLED PATH`: the finite event vocabulary was exhausted without a route.
- `UNRESOLVED`: no route was found before a width/effort limit; this does **not** mean safe.

The scenario vocabulary models appearances, ordinary no-return performances (which normally score
appearance points), clean sheets, goals, assists, cards, goalkeeper penalty saves, and captain/autosub
effects. Goal constraints reject contradictions such as an opposing attacker scoring while a defender
keeps a clean sheet. Bonus/BPS and arbitrary multi-goal combinations are not yet enumerated.

If the search width omits lower-impact player-fixture variables, their snapshot points remain and
they are conservatively treated as adding no further points (a final zero-minute player can therefore
trigger an autosub). The report names the number omitted rather than hiding this assumption. Scenario
ranks are transparent rarity/complexity heuristics, not probabilities and never validity rules.

Live-fixture scenarios are additions to points already reported by FPL. For late Gameweeks this is
useful and tractable. Early in a Gameweek, focus on the exact score and exposure sections; widen the
search only if needed.

## FPL endpoints

The client reads the public FPL endpoints:

- `/api/bootstrap-static/`
- `/api/fixtures/?event=GW`
- `/api/event/GW/live/`
- `/api/leagues-classic/LEAGUE/standings/`
- `/api/entry/ENTRY/event/GW/picks/`

FPL does not publish a stability contract for these endpoints. A private league may require an
authenticated capture; in that case create a schema-compatible snapshot and use `--snapshot`.

## Tests

The suite is offline and uses only synthetic domain objects:

```powershell
python -m unittest discover -s tests -v
```

It covers captain/vice-captain handling, Triple Captain, Bench Boost, transfer hits, zero versus one
minute, goalkeeper and formation-restricted autosubs, shared exposures, safety bounds, official
snapshot parsing, and football consistency.

## Project layout

- `api.py` — FPL retrieval, snapshots and parsing
- `models.py` — domain types
- `state.py` — current effective standings
- `substitutions.py` — autosubs, captaincy and scoring
- `exposures.py` — shared/effective ownership
- `safety.py` — conservative safe-manager proofs
- `scenarios.py` — bounded event vocabulary and football constraints
- `solver.py` — last-place conditions and best-first search
- `ranking.py` — plausibility/complexity heuristic
- `formatting.py` / `cli.py` — human and JSON output
