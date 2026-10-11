# Private FPL Assistant

The public Pages application remains available without login. Its lock-marked FPL Assistant
link navigates in the same browser tab to `/assistant/` on the Python backend. That backend
serves the dashboard and every private API from one origin. This avoids relying on third-party
cookies between GitHub Pages and Render. The address bar changes to the backend host.

Private access is **disabled by default**. Missing or invalid credentials/origin produce a locked
page and HTTP 503 on private operations; public league analysis continues to work.

## Enable your own access on Render

1. Generate a random **8-digit passcode**, or a unique passphrase of at least 14 characters locally. From an installed
   checkout, run `python -m fpl_forfeit.assistant.auth hash-password`. It prompts twice without
   echoing the password and prints a salted hash. Never put the password or hash in Git, a
   frontend configuration file, a URL or a command-line argument.
2. In the existing Render service's **Environment** dashboard, set
   `FPL_ASSISTANT_PASSWORD_HASH` to that complete hash (including its dollar signs).
   The HTTPS origin is automatically taken from Render's `RENDER_EXTERNAL_URL` unless
   `FPL_ASSISTANT_ORIGIN` is explicitly configured (for example, for a custom backend domain).
   The blueprint also supplies this non-secret origin; the password hash must be added manually as an environment
   secret. Do not enable `FPL_ASSISTANT_DEV_HTTP` in production.
3. Save/redeploy. Keep the existing single-process `uvicorn fpl_forfeit.web:app` command.
   No paid database, identity provider, signing key or new runtime dependency is needed.
4. Open **FPL Assistant** from the public site, sign in, then enter your public FPL entry ID.
   You never supply your FPL account password. Enter current bank/free transfers/actual selling
   prices before comparing transfers, and verify that the public squad still matches your account.

Change the password by replacing the hash and restarting the service; the restart revokes all
existing sessions. Removing the hash disables private access. A cold Render instance may take
time to wake up; the public analyser's hosting arrangements are unchanged.

For the prepared one-time setup, copy the entire contents of the ignored local
`assistant-password-hash.local.txt` file into `FPL_ASSISTANT_PASSWORD_HASH` in Render's
Environment dashboard, then choose **Save, rebuild, and deploy**. The local file contains only
the generated salted hash, never the plaintext passcode. It is excluded from Git and the frontend
build. Once deployed, unlocking only requires the 8-digit passcode provided privately.
Short numeric passcodes have less entropy than long passphrases; keep the code private and use
the existing rate limiting. A long passphrase remains supported.
Automatic origin configuration uses Render's [documented service environment](https://render.com/docs/environment-variables).

Render's source checkout includes `frontend/src`, which the installed backend serves directly.
An installation outside that checkout must set `FPL_ASSISTANT_ASSETS` to a directory containing
`assistant.html`, `assistant.js`, `assistant.css`, `styles.css` and `favicon.svg`. This directory
contains public presentation assets only. The hash, personal inputs and results stay on the server.
The public static build can include these presentation files; they grant no private access.

## Authentication and privacy

- Passwords use stdlib PBKDF2-HMAC-SHA256, 600,000 iterations, a random 16-byte salt and
  constant-time digest comparison. There is no default password/hash and no committed credential.
  The work factor follows [OWASP's password-storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
- A random 256-bit opaque session token is sent only in a `__Host-fpl-assistant` cookie:
  HttpOnly, Secure, SameSite=Strict, Path=/, no Domain. The server retains only its SHA-256 lookup
  key and a random session subject. Cookies/tokens never appear in API JSON, URLs or localStorage.
- Every `/api/assistant/*` operation/data request is authorised by middleware **before** request
  validation or computation, including unknown future routes. Only exact GET session status and
  POST login are public. All private responses and dashboard assets use `Cache-Control: no-store`.
- Origin, Host and browser fetch-site checks require the configured backend origin. Writes require
  a same-origin JSON request and a session CSRF nonce in `X-CSRF-Token`. Login requires the exact
  Origin; private APIs do not use credentialed public CORS. The origin comes from configuration,
  never from untrusted forwarded headers. TLS may terminate at Render's proxy.
- Login allows five attempts per client address and twenty globally per ten minutes, including
  successful attempts, with at most two simultaneous password checks. Errors do not distinguish
  account identities. The service trusts the platform's client-address configuration; the global
  limit remains effective if addresses are shared or vary.
- Sessions expire after one idle hour or eight absolute hours; up to sixteen are retained.
  Passive browser status checks do not extend idle expiry. Logout revokes access and removes that
  session's caches, aborts browser requests and clears personal DOM/state. Other open assistant
  tabs receive a logout notification. Reload/back-forward restoration rechecks access.
- Sessions and private caches are in-process memory only. Restarts revoke sessions. Use **one
  worker/instance**; multiple workers require a shared session store before scaling. Expired/evicted
  sessions cannot read old caches; those bounded caches expire within their TTLs. No personal
  results are saved to disk, public history SQLite, public endpoints or the public analyser cache.
- A strict same-origin Content Security Policy blocks inline scripts/eval, framing and external
  connections. No-referrer and nosniff headers apply. Browser storage contains only the theme.
  The dashboard shell is public; protected data and computations are authorised on the server.

For local development only, set `FPL_ASSISTANT_DEV_HTTP=1` and
`FPL_ASSISTANT_ORIGIN=http://127.0.0.1:8000`, plus your locally generated hash. HTTP is accepted
only for loopback hosts under this explicit flag and uses a separately named development cookie.
Open `http://127.0.0.1:8000/assistant/`; a different localhost spelling is a different origin.

## Implemented planning

| Section | Behaviour |
| --- | --- |
| Predictions | All official roster players; next 1/3/5/6 GW views, per-GW and total expected points; name/club search, position/club/price filters, sorting and pagination. Fixture details show minutes, start/appearance/qualifying clean-sheet probabilities, goals/assists, defensive contribution/points, empirical uncertainty, version/timestamp/provenance/fallback flags. |
| My Team | Loads the latest released public deadline picks, captain/vice and bench. Shows last-deadline bank/value and snapshot time. Buy/sell prices, free transfers and missing chip history remain unknown. Select any of the next six published planning GWs; compare public lineup, manual legal draft and recommended lineup. |
| XI/captaincy | Enumerates all legal starting XIs, six outfield bench orders and every starting captain/vice pairing. Includes reserve-GK and legal outfield autosubs, disciplinary appearances and Gameweek-wide vice takeover, including doubles. Shows formation, expected base/total/captain points, ranges and alternatives. |
| Transfers | No transfer or up to five distinct immediate transfers, confirmed account inputs, official buying prices, actual selling prices, legal position/club limits, bank and prospective hits. Re-optimises each unchanged resulting squad in every GW against the no-transfer baseline; next/3/5-GW and per-GW net gains. |
| Single-transfer search | Choose one outgoing player and confirmed selling price. Screen all affordable legal same-position replacements using the best legal mean XI and captain over the selected horizon; refine the best eight with the full joint-outcome optimiser. Rank whole-squad gains including hits, compare with zero-gain no-transfer baseline and allow selection into the transfer draft. Explicitly a bounded heuristic. |
| Chips | Conditional Bench Boost and Triple Captain gains vs no chip across six GWs, using optimal legal XIs/captaincy and the unchanged squad. Checks official chip windows/quantities and public history, including repeat uses. Compares future-window values without claiming a definitive opportunity-cost optimum. |

Formation and transfer rules were checked against the current official
[team-selection guidance](https://www.premierleague.com/en/news/2174899/fpl-basics-managing-your-team),
[transfer guidance](https://www.premierleague.com/es/news/2174907) and
[2026/27 chip guidance](https://www.premierleague.com/en/news/4679879/whats-happening-with-fpl-chips-in-202627).
The backend additionally checks official bootstrap squad/formation/club metadata and free-transfer
cap. Unsupported/missing squad rules disable optimisation instead of guessing. A paid transfer
costs four points and is deducted once from the next GW; already incurred private hits are unknown.

## Shared forecasts, correctness and limitations

`api.py` normalises official bootstrap/fixture feeds and public squad endpoints. `forecasting/horizon.py`
uses the existing `ForecastInputs`, `FixtureSimulator`, `ProjectionAccumulator` and `event_points`;
it adds future-GW aggregation and compact joint sample matrices, not a second statistical model.
The public optimiser-independent scoring remains in `points.py` and `substitutions.py`.
The assistant's domain optimiser uses those same scoring/autosub functions and reconciles the
winning sampled score against them. Appearance-pattern grouping is an exact computational shortcut.

All players share a fixture draw. Reusing a stable full roster across lineups/transfers preserves
correlated goals, concessions, appearances and autosubs. DGW points/minutes are summed per draw;
variance and percentiles are calculated from those GW totals, preserving within-GW dependence.
Seed = shared default + GW; selecting a shorter horizon cannot change that GW's draws. Public live
analysis retains its existing owned-player roster, official scored totals, sample counts and caches.
Different roster/live contexts can yield different numbers; both use the same model and FPL scorer.

One 192-draw, six-GW all-roster forecast is cached for twenty minutes or until the next deadline,
whichever comes first. Switching horizon/sections does not simulate again. Refresh invalidates
the shared future generation and derived private plans. The forecast feed requires two official
requests, without fetching individual histories. Public-squad loading uses a profile, latest picks
and history; recent picks are retried only for unavailable releases. Squads expire after an hour;
session plans after twenty minutes, with at most 64 cached results per each of sixteen sessions.
The public and private services have separate retrieval/application caches.

Only actual published fixtures are used. Missing schedule pairs and event-unassigned fixtures flag
affected clubs as partial/unknown; totals are null, not zero. A confirmed blank requires a complete
season schedule and no assigned/unassigned match for that club/GW. Fixture detail still shows
available published matches. Incomplete squad coverage disables complete lineup totals/gains.
Postponements are handled through refreshed official assignments, not guessed replacement dates.
No future deadlines or missing historical/private observations are invented.

Forecasts are experimental and uncalibrated. Season xG/xA, appearances, starts, rates and current
injury flags are held fixed across the window; injury recovery, evolving form/rotation, future
transfers, prices and inter-GW uncertainty are not predicted. Bonus is approximate and rare penalty,
red-card/own-goal events are incomplete. An exhaustive XI is optimal **on its finite cached draws**,
not guaranteed optimal under the unknown true distribution. Small gains can be sampling/model noise.
Ranges are simulated score uncertainty, not confidence guarantees for model accuracy.

Transfer screening ignores autosub/vice effects and can omit the best replacement; exact refinement
then includes them. Manual multi-transfer plans are evaluated, but automatic multi-transfer search,
sequential future transfers, saved free-transfer value and risk preferences are not implemented.
An existing squad with more than three players from a club after a real-world move can still be
analysed; transfer plans must restore the official club limit. The current version compares fixed
squads and no chips in transfer plans; a concurrent chip is not silently assumed.

Wildcard/Free Hit whole-squad budget optimisation is not implemented: doing it properly requires
selling-value coverage for the whole squad and valid longer-term constraints. Chip values are
conditional on unchanged squads and public usage history. Unpublished chip usage and opportunity
cost beyond six GWs remain unknown. Nothing is submitted to FPL; this is account-read-only planning.

## Verification and representative performance

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')
python -m unittest discover -s tests -v
python frontend/build.py --api-base-url https://fpl-scenario-analyser-api.onrender.com
python -m frontend.check_assistant --browser-executable 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
```

The optional assistant browser check uses an in-process API, synthetic correctness fixtures and
a runtime-generated password held only in memory. It exposes no production test route/configuration.
It verifies protected access, login/CSRF/logout, horizons/search/details, legal/rejected swaps,
draft scoring, actual-price transfer budget/hits, shortlist/chip results and one cached forecast
across all sections. It checks all four private views at 320/390/1440px in light/dark modes.
Screenshots go to ignored `.browser-artifacts/assistant`, never the frontend build.
Public browser checks remain `frontend/check.py` with its existing deterministic demos.

Domain tests cover analytically known XI optima, brute-force legal-XI agreement, bench order,
captain takeover/Triple Captain/Bench Boost/zero-minute cards, unknown fields, doubles/blanks and
missing schedules, exact shared-projection consistency, selling vs buying, budgets/hits/club limits,
chip windows and private cache isolation. Auth tests cover all private routes, malformed requests,
disabled settings, cookies/CSP, CORS/CSRF/origin, rate limiting, passive/active expiry and revoked sessions.

Implementation validation on 2026-10-11: **258 Python tests passed**, the production frontend
build passed, repository lint passed and all changed Python files passed formatting checks.
Private browser checks and the full existing public browser check passed; the public check
covered the neutral homepage, all six views, desktop/mobile themes, scenarios/combined what-ifs,
comparison, manager-specific loading and completed results. Screenshots remain outside Git/build.

A saved official 2026-10-11 data generation (kept outside Git) covered 667 players and sixty
fixtures across GW7–12. At 192 joint draws, offline forecasting took **6.525 seconds** and one
15-player exhaustive XI/bench/captain optimisation took **0.611 seconds** (3,300 legal XI/bench
configurations). These are local measurements, exclude upstream network/wake-up latency and are
not Render performance guarantees. Cached repeats reuse those forecasts and optimised plans.
