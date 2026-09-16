# Analytics: sources, definitions and limitations

Analytics is descriptive and separate from live scenario solving. Calculations are in
`src/fpl_forfeit/analytics/calculations.py`; caching/retrieval is in `analytics/service.py`.
The existing domain engine, CLI, safety envelope and scenario vocabulary are unchanged.

## Public data sources

All calls are server-side, without FPL login credentials. Current-season fields were checked
against the official endpoints, including a real final automatic substitution and chip uses.

| Official FPL API resource | Fields used |
| --- | --- |
| `/api/bootstrap-static/` | Events/finished state, season deadline namespace, player names/positions |
| `/api/leagues-classic/{id}/standings/` | Current membership and names; pagination bounded to 50 managers |
| `/api/entry/{id}/history/` | Event points, transfer costs/counts, every chip event |
| `/api/entry/{id}/event/{gw}/picks/` | Final multiplier, published captain/vice, squad slots, chip, raw event score |
| `/api/event/{gw}/live/` | Official event player totals, already aggregated across DGW fixtures |

Score history is useful even when individual old squads cannot be retrieved. Detailed contributions
are published only when all 15 player totals/multipliers are present and their counted sum matches
the official raw event score. Missing or mismatched records remain unavailable with a manager/GW
warning. Final pick position fields are preferred; current-season catalogue positions are a fallback.

Official rule references: [managing your team](https://www.premierleague.com/en/news/2174899),
[chips](https://www.premierleague.com/en/news/2174900), and
[2026/27 chip availability](https://www.premierleague.com/en/news/4679879).
We record every chip event rather than assuming a chip can only be used once in a season.

## Accounting

- **Weekly score:** official raw GW points minus that week's transfer cost, once. All-season totals
  sum available completed weeks, not global overall rank or unrecorded weeks.
- **Rank:** competition ranking; scores 60, 50, 40, 40 have ranks 1, 2, 3, 3. All tied lowest scores
  count as last. Bottom three includes every tie at the third-lowest score. These descriptive rules
  are fixed and explicitly separate from the live Settings tie rule.
- **Coverage:** all current league members must have scores before a week gets last-place/rank
  statistics. Partial weeks still show known scores, hits and an available-score mean, but no invented
  loser. Managers who started later can therefore leave early weeks unranked.
- **Last-place margin:** second-lowest manager score minus lowest; a bottom tie has margin zero.
  Closest escape is the smallest positive gap above the lowest score in a fully covered week.
- **Bench:** sum of unused final multiplier-zero player points; autosubbed points do not count.
  Bench Boost unused points are zero; counted final slots 12–15 are recorded separately as its
  direct contribution. FPL may reorder final squad positions after autosubs.
- **Captain:** total multiplied contribution is separate from additional multiplier points.
  Eight base points doubled means 16 total and +8 additional; tripled means 24 and +16.
  Published captain selection and the effective captain are separate; vice takeover receives its
  actual points. A Triple Captain chip's displayed contribution is the full +2-times-base amount
  above ordinary ownership, not an inferred advantage over choosing a normal captain.
- **Positional/player points:** base event points × final multiplier, including autosubs, captaincy
  and Bench Boost; hits stay separate. Positional percentages use counted player points as their
  denominator. League-player totals count each manager/GW contribution, not global player totals.
- **Current ownership:** owners/league managers. Starting/bench columns use published squad
  positions. Effective ownership is the sum of current effective multipliers divided by manager
  count, including pending captain exposure if they appear; it may exceed 100%. Live substitutions
  and vice-captain takeover remain provisional. This does not predict the eventual lineup.
- **Similarity:** shared current squad players / 15. Captain differences are displayed separately.
- **Head-to-head:** weekly wins/ties, average gaps and common-point totals use only weeks recorded
  for both managers. Whole-season hits/bench/forfeits have separate coverage labels.
- **Consistency:** population standard deviation of recorded net weekly scores; lower means more
  consistent, not more skilled. Above/below-average percentages and mean absolute distance use
  fully covered league weeks. Quartiles use inclusive interpolation.
- **Ownership/captain trends:** counts among verified squads only, with coverage alongside each
  week. Unknown squads are not treated as non-owners; do not compare partial counts as full-league
  percentages.

## Retrieval and resource bounds

The initial summary fetches score histories, not every historical squad. A single shared background
job then verifies required final squads. Section payloads are lazy; selected manager, Gameweek and
player routes avoid sending the entire enriched dataset. GET `analytics/details` starts/reuses work
and reports `loading`/`busy`/`ready`, attempted/total squads, and verified coverage.

Named settings in `analytics/service.py`:

| Setting | Default |
| --- | --- |
| Live metadata/current snapshots | 90 seconds |
| Entry score histories | 15 minutes |
| Final historical picks/player totals | 24 hours |
| Partial enrichment retry cache | 5 minutes |
| Concurrent background league jobs | 2 |
| Concurrent upstream analytics requests | 4 |
| Historical fetch time budget | 240 seconds plus in-flight request completion |

Resource requests and jobs are coalesced. Historical player totals are reduced before caching.
Resource, summary and result caches have bounded item counts; season/source keys prevent reuse
across seasons and score generations. Caches are per-process and ephemeral, suitable for the
existing single-worker free Render service. A cold restart loses them. Official corrections can
take up to the historical TTL to appear. Failed or budget-limited detail is explicit and retryable;
the browser stops automatic progress polling after five minutes and offers a recheck.

## Deliberate limits

- Current league membership only; no reconstruction of when managers joined/left the league.
- Current season only, completed GWs only for seasonal statistics. No invented mid-GW history.
- No blanket claim that all old squads are available. Dashes mean unknown/not applicable, not zero.
- No speculative bench-versus-starting-XI counterfactual counts, Wildcard/Free Hit uplift, transfer
  success, recommendations or predictions.
- No database, scheduled warehouse imports or synthetic historical demo league. The existing
  single-Gameweek demos remain for the live workflow; Analytics requires a real public league.

## Checks

Run `python -m unittest discover -s tests -v`, `ruff check .`, `ruff format --check .`, then build
with `python frontend/build.py`. With both local servers running, `python frontend/check.py --league
188263` additionally exercises real Analytics, neutral/recent home routing, shared links, all five
sections, interactions and both themes at 320/390/1440px. Screenshots stay in a temporary directory.
