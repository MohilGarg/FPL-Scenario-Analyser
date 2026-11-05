# External forecast source register

Verified **5 November 2026**. These are forecast providers, not realised outcomes.
No source has been shown to improve this application's forecasts yet. External means
are experimental private comparisons and do not replace joint simulation draws.

| Source | Access and horizon | Definitions and cadence | Rights and implemented status |
|---|---|---|---|
| [Official FPL](https://fantasy.premierleague.com/api/bootstrap-static/) | Public JSON; `ep_next`, next GW, FPL element IDs | Preserve supplied expected points, never multiply another availability flag. No documented computation time or algorithm; first retrieval is availability evidence. | Collector implemented. Official fields are not treated as openly licensed redistribution data; local normalized batch excluded from public archive. Existing official-input recording remains unchanged. |
| [FPL Cat developer API](https://fplcat.com/developers) | Public versioned JSON/CSV, no key; next GW | One player/GW row; points already aggregate doubles. Daily, availability-change and pre-deadline refreshes; 5-minute response cache. **DGW `play_chance` is a sum of fixture probabilities and can exceed one**; never interpreted as GW appearance. | JSON adapter implemented. Personal/research/model comparison allowed with attribution; paid republication requires permission. Non-commercial research archive includes normalized forecasts with credit. [Terms](https://fplcat.com/terms). |
| [Sideline Quant data](https://sidelinequant.com/data) | Public JSON at `/data/latest.json`; next GW | Fixture rows, FPL IDs; doubles have multiple rows. xP averages over simulated minute states. `p_start60` means start **and** 60+, `p_cs` conditional on 60+, `p_ge_6/10` are GW values repeated across rows. ETag changes on model refresh. | Adapter implemented; source-model fields CC BY 4.0, credit/link required. FPL news/prices are outside that licence and not copied. Live [terms](https://sidelinequant.com/terms) explicitly exempt Open Data from general restrictions; an older search-cache copy omitted the exemption. |
| [FPL Form export](https://www.fplform.com/export-fpl-form-data) | User-operated CSV download; selectable fixture/GW range | `_pts`/`_with_prob` already include each fixture's appearance chance. `_pts_no_prob` is conditional on appearing. `_prob` describes the **first fixture**, not a double's GW chance. Unassigned fixtures may be labelled `tba`. | Strict long-form manual import implemented; no browser automation or automatic extraction. Personal use only; no sharing files/data. Derived analysis requires credit/link. Imports stay local and are rejected from public archive. |
| [FPL Review Free Model](https://docs.fplreview.com/the-model/projections/free-model/) | Free model documented, shorter three-GW horizon | Documentation gives EV but does not establish a stable bulk API, timestamps, row-level appearance semantics or archive. | Generic explicit-metadata personal CSV import supported. Automatic access and redistribution remain unimplemented pending a documented export contract and permission. No subscription/access-control bypass. |
| [Onside open dataset](https://onsidearena.com/data) | Additional discovered CSV archive; 4,954 graded rows on inspection | One player/GW; claims immutable pre-deadline captures, includes official/form/season baselines and outcome-selected `minutes_qualified`. Current download and self-reported capture times alone do not independently establish past publication. | Candidate only, not used in evaluation/training. CC BY 4.0 advertised. Independently verify versioned DOI/publication dates and eligible population before using historical outcomes. Do not filter on `minutes_qualified` for a pre-deadline selection population. |

## Reproduction

From an installed checkout (or set `PYTHONPATH=src`), using Python 3.12+:

```powershell
python -m ml.external_forecasts collect
python -m unittest tests.test_external_forecasts -v
```

The first validated collection at 10:39 UTC on 5 November captured all **670**
current-roster players from each of official FPL, FPL Cat and Sideline Quant for
GW10 (deadline 6 November, 18:30 UTC). These establish prospective availability
only. There are no settled matching external outcomes from those captures yet.

Collection uses timeouts, bounded payloads, transient-error retries, a persisted
five-minute minimum cache and conditional ETags. Windows uses native curl to
avoid the embedded Python OpenSSL/Uplink failure; Linux uses stdlib urllib.
Schema/mapping failures are visible per source without erasing earlier captures.
No collector runs inside an HTTP user request.

Captures and raw HTTP cache live under ignored `snapshots/ml/external/`. Normalized
files are content-addressed gzip JSON and are never overwritten. Mutable per-source/GW
pointer indexes make live reads cheap; they are locators, not publication evidence.
Background collection retains automatic local cache copies for up to seven days,
512 files or 64 MiB, keeping the newest two per automatic source and protecting
the current collection. **Personal imports are never pruned**, and immutable durable
recording releases are separate and never pruned by this cache policy. Historical
availability is **never**
backdated from a provider's `computed_at`: a usable capture requires
`computed_at <= retrieved_at <= cutoff < deadline`. Missing computation time
stays null. Default freshness is at most 36 hours from computation (retrieval
when computation is unknown). The durable recording workflow archives only
allowlisted normalized redistributable models with attribution; personal CSV
imports and raw HTTP caches must never be released.

### Personal CSV import

Convert a legally obtained source export to explicit long-form columns:

```csv
player_id,gameweek,fixture_id,expected_points,appearance_probability,expected_minutes,start_probability,sixty_probability
4,10,91,6.4,0.97,85,0.95,0.91
```

`fixture_id` is required only for fixture-grain rows. Optional values stay blank;
missing is never zero. Metadata (`personal-metadata.json`, kept outside Git):

```json
{
  "source": "fpl_form",
  "source_version": "personal-export",
  "season": "2026-27",
  "deadline": "2026-11-06T18:30:00Z",
  "computed_at": null,
  "granularity": "fixture",
  "points_semantics": "unconditional"
}
```

```powershell
python -m ml.external_forecasts import-csv personal.csv --metadata personal-metadata.json --official-input snapshots/bootstrap.local.json --fixtures snapshots/fixtures.local.json
```

Conditional fixture points require `points_semantics=conditional_appearance` and
an appearance probability; normalization multiplies exactly once. Conditional
GW aggregates are refused because one appearance chance cannot correctly adjust
separate DGW legs. The CLI sets retrieval to actual import time; it provides no
historical timestamp override. Unassigned `tba` fixtures must not be invented.
Use official season IDs where available. Without IDs, only unique full-name and
club matches or explicit `season:club:name` exceptions are accepted. Fuzzy or
ambiguous name matches fail visibly.

## Blending and limitations

Means/medians expose member forecasts and coverage, renormalizing over observed
members rather than zero-filling. A common-coverage mode supports fair paired
comparisons. Fixture means sum to GW means; joint DGW probabilities, variances
and fixture allocations are never invented from aggregates. Known incomplete
fixture coverage is excluded when the official roster/schedule is supplied.
Source probabilities and scoring components retain explicit definitions in
individual source rows. GW aggregate probabilities stay null without a joint
distribution.

The optional learned blend requires **eight distinct settled common deadlines** before
estimating weights; otherwise it returns equal weights. Hundreds
of players from one deadline count as one deadline. It uses past settled, common observations only,
nonnegative inverse-MSE weights shrunk towards equal weights. This is a simple
diagnostic comparator, not a tuned stacking model. Future/pending outcomes and
post-deadline captures are rejected. Correlated provider inputs, source ablations,
calibration and default promotion require future deadline-level paired evidence.
An average xP alone is **not** a valid simulation distribution, so external blends
cannot silently feed XI/captain/transfer/chip optimization.
