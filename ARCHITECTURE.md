# Shared forecasting and the future private assistant

The public Scenario Analyser and a future private FPL Assistant will consume one
football forecasting implementation. This cycle implements reusable player
projections and improves season-based starting-duration estimates. Personal squads,
optimisation, recommendations and authentication are future work. No private routes
or login placeholders are registered.

## Python boundaries

| Responsibility | Current boundary |
| --- | --- |
| Official FPL retrieval, raw parsing and normalisation | `api.py`; domain inputs in `models.py` |
| Optional licensed data | Future server-side adapters supplying normalised `ForecastInputs` with named `DataSource` provenance; none installed |
| Player and team statistical estimates | `forecasting/statistics.py` |
| Shared fixture distributions, playing windows and football events | `forecasting/simulation.py` |
| Structured player projections and uncertainty | `forecasting/models.py`, `forecasting/projections.py` |
| FPL fixture points | `points.py`, also re-exported by `scoring.py` for existing consumers |
| Public official-score reconciliation, league risk and sensitivity | `forecast.py` |
| Hits, captaincy, chips and legal autosubs | `scoring.py`, `substitutions.py` |
| Public safety and exact scenarios | Existing `safety.py`, `solver.py`, `scenarios.py` |
| Descriptive league analytics | Existing `analytics/service.py`, `analytics/calculations.py` |
| Private optimisation and authentication | Future dedicated services and protected router; no implementation yet |

The shared forecasting package uses standard-library Python and normalised `Player`
and `Fixture` values. It has no league IDs, managers, picks, transfer costs, web
dependencies, network requests or raw API parsing. Public `football_inputs(state)`
strips those application details. `FixtureModel` remains a compatibility adapter over
the shared `FixtureSimulator`.

Each public simulation draws a fixture once for all managers. Player projections
summarise those same draws; they do not run a separate model. The public adapter adds
only unfinished-fixture scoring deltas to official aggregate event points, preserving
completed Double Gameweek contributions, then resolves multipliers and hits. Private
optimisation must consume the same joint outcomes when evaluating correlated squad
choices; independently sampling per-player marginal projections would lose shared
goals, clean sheets, appearances and autosub dependencies. No predictive advice is
added to the public application.

## Projection interface

`ForecastInputs` carries a roster, a tuple of fixtures, an observation timestamp,
normalised per-fixture live categories/minutes, team scoring/conceding rates and
provenance. The fixture horizon can span multiple Gameweeks. Inputs must remain
unchanged during a run. The timestamp identifies the observed data generation, not
a promise that every cached upstream resource was fetched at that instant.

```python
from fpl_forfeit.forecasting import ForecastInputs, project_players

# All values below are already normalised by the server-side data layer.
inputs = ForecastInputs(
    players=roster,
    fixtures=fixture_horizon,
    fetched_at=observed_at,
    team_goal_rates=team_rates,
)
projections = project_players(
    inputs, gameweeks=(current_gameweek, current_gameweek + 1),
    samples=1600, seed=20261010,
)
```

`project_players` returns immutable `PlayerFixtureProjection` records keyed by
`player_id`, `fixture_id` and `gameweek`. Double Gameweeks retain separate fixture
records. Finished fixtures are excluded. A Gameweek with no supplied fixtures returns
no records; callers must distinguish a confirmed blank schedule from incomplete
schedule coverage. The function retrieves nothing and fabricates no future fixtures.

| Field | Meaning |
| --- | --- |
| `expected_points` | Mean final fixture FPL category points, before squad multipliers or hits |
| `expected_remaining_points` | Mean change from this fixture's observed category points; may be negative |
| `expected_minutes` | Mean final regulation minutes for this fixture |
| `appearance_probability` | Positive minutes or a disciplinary appearance, including a zero-minute card |
| `starting_probability` | Modelled start probability before kickoff; null for live matches because reliable lineup timestamps are unavailable |
| `expected_goals`, `expected_assists` | Mean final fixture event counts, including already observed returns |
| `clean_sheet_probability` | Probability of a qualifying player clean sheet: 60+ minutes and no counted concession, including retention after an ordinary substitution |
| `points_variance`, `points_interval_80` | Population variance and empirical 10th–90th percentiles of sampled final fixture points |
| `samples`, `coverage` | Number of usable draws and explicit live-breakdown coverage |
| `uses_player_fallbacks`, `uses_team_fallbacks` | Flags for missing/sparse evidence requiring fallback inputs; shrinkage priors also apply with complete evidence |
| `model_version`, `data_timestamp`, `provenance` | Versioned model and named input generation/source |

Missing live breakdowns produce null metrics and zero usable samples. The public
adapter freezes the already-awarded aggregate points in that case. Neither API
interprets unknown data as a football blank. Final fixture category expectations
are not a replacement for official awarded event totals or historical results.
The public outlook includes these records as `player_projections` alongside its
existing risk outputs. All of these records concern public football outcomes.

For joint draws, use `FixtureSimulator(inputs).sample(random.Random(seed))` and
inspect each `FixtureSample`'s scoreline, categories, events and playing windows.
The seed, input generation, supplied fixture horizon, explicitly modelled player
set, model version and any forced conditions together identify a run. An optional
`player_ids` selection bounds computation; omitted roster members contribute
anonymous allocation weights. Changing that selection can change allocation/bonus
approximations. Use a stable full roster or stable comparison set across future
optimisation choices, rather than rerunning with only each candidate XI. Public
analysis retains its existing league-owned comparison set to keep summaries cheap.

Future Gameweeks currently reuse the observed season rates and availability. The
interface supports the horizon, but this cycle does not model future injuries,
rotation, transfers, evolving form or inter-Gameweek uncertainty. There is no new
forecasting accuracy claim: estimates remain uncalibrated and need saved real live
forecasts for evaluation. Licensed adapters can later improve the same inputs/rates,
with server-held credentials and explicit source/licence restrictions, without
introducing a second prediction model.

## Future server-side authentication boundary

Public `/api/league/*`, `/api/demo/*` and descriptive analytics routes remain public.
Personal squads, preferences, budgets and optimisation results must live in a
separate private service exposed only through a dedicated router such as
`/api/private/*`. When that router is built, attach a server-side authentication
dependency to the entire router and enforce resource ownership/authorisation on
every endpoint, including progress, cached results and background-job access.
Unauthenticated requests must fail before fetching personal data or computing a
private result. A hidden tab, JavaScript password prompt, route naming or CORS
policy is not authentication.

The shared football model accepts no user identity and can be reused by both
services. Private services will pass authenticated identity explicitly and store
personal inputs/results in caches partitioned by that identity. Public analysis
caches must never receive private squads or optimisation output. Share only
licence-permitted public football inputs/distributions through public caches.
Background private work must carry its owner's identity and check authorisation
when results are read.

Credentials and external provider keys belong in server environment/secret storage,
never frontend configuration or Git. The eventual authentication choice must fit
the deployment topology: either secure server sessions with appropriate cookie/CSRF
handling or validated identity-provider tokens. Credentialed CORS and origins must
be deliberately configured then. The current unauthenticated public CORS settings
provide no private access control. No database, identity provider, secret, login
system or private API endpoint is introduced in this cycle.
