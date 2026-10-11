"""Private application service: cached shared forecasts and session-isolated planning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from threading import RLock
from time import perf_counter

from ..api import FPLClient, FPLGameweekUnavailable
from ..cache import TTLCache
from ..forecasting.horizon import HorizonForecast, forecast_horizon
from ..models import Position, PublicSquadSnapshot
from .optimisation import COUNTS, LIMITS, LineupOptimiser, validate_squad

ASSUMPTIONS = [
    "Experimental, uncalibrated expected points; future returns are not guaranteed.",
    "Uses the public analyser's football model, fixture scoring, captaincy and legal autosubs. All roster players share each fixture draw.",
    "Season xG/xA, minutes, starts and current injury flags are held fixed across the horizon. No injury recovery, future rotation, price changes or cross-Gameweek form evolution is predicted.",
    "Playing time, approximate bonus and defensive contributions are modelled; rare penalties, red cards and own goals are not comprehensively forecast.",
    "Only published fixture assignments are used. Unassigned or missing schedules are unknown, not confirmed zero-point blanks.",
    "One 192-sample joint forecast is cached for up to 20 minutes. Different public live/owned-player contexts can produce different numerical estimates; the statistical and FPL scoring implementations are shared.",
]


class AssistantService:
    def __init__(
        self,
        client: FPLClient | None = None,
        *,
        samples: int = 192,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client or FPLClient()
        self.samples = samples
        self.now = now
        self.lock = RLock()
        self.forecasts: TTLCache[int, HorizonForecast] = TTLCache(1200, max_items=1)
        self.squads: TTLCache[str, PublicSquadSnapshot] = TTLCache(3600, max_items=16)
        self.plans: TTLCache[str, dict[tuple, dict]] = TTLCache(1200, max_items=16)
        self.last_forecast_seconds = 0.0

    def forecast(self) -> HorizonForecast:
        with self.lock:
            cached = self.forecasts.get(0)
            if cached:
                first = next(
                    (g for g in cached.catalogue.gameweeks if g.id == cached.gameweeks[0]), None
                )
                if first and first.deadline and first.deadline > self.now():
                    return cached
            started = perf_counter()
            catalogue = self.client.forecast_catalogue()
            future = sorted(
                g.id for g in catalogue.gameweeks if g.deadline and g.deadline > self.now()
            )
            if not future:
                raise FPLGameweekUnavailable(
                    "No future Gameweek deadline is published for this season."
                )
            gameweeks = tuple(
                range(future[0], min(future[0] + 6, max(g.id for g in catalogue.gameweeks) + 1))
            )
            forecast = forecast_horizon(catalogue, gameweeks, samples=self.samples)
            self.forecasts.set(0, forecast)
            self.plans.clear()
            self.last_forecast_seconds = perf_counter() - started
            return forecast

    def refresh(self) -> dict:
        with self.lock:
            self.forecasts.clear()
            self.plans.clear()
        return {"refreshed": True}

    def clear_session(self, subject: str) -> None:
        self.squads.delete(subject)
        self.plans.delete(subject)

    @staticmethod
    def identity(forecast: HorizonForecast, pid: int) -> dict:
        player = forecast.catalogue.players[pid]
        return {
            "player_id": pid,
            "name": player.name,
            "team_id": player.team_id,
            "team_name": player.team_name,
            "position": player.position.value,
            "price_tenths": player.price_tenths,
            "news": player.availability_news,
        }

    def metadata(self, forecast: HorizonForecast) -> dict:
        return {
            "model_version": forecast.inputs.model_version,
            "data_timestamp": forecast.catalogue.fetched_at.isoformat(),
            "samples": forecast.samples,
            "seed": forecast.seed,
            "forecast_seconds": self.last_forecast_seconds,
            "gameweek_deadlines": {
                g.id: g.deadline.isoformat()
                for g in forecast.catalogue.gameweeks
                if g.id in forecast.gameweeks and g.deadline
            },
            "provenance": [asdict(p) for p in forecast.inputs.provenance],
            "assumptions": [
                note.replace("192-sample", f"{forecast.samples}-sample") for note in ASSUMPTIONS
            ],
        }

    def predictions(self, horizon: int) -> dict:
        if horizon not in {1, 3, 5, 6}:
            raise ValueError("Choose a 1, 3, 5 or 6 Gameweek horizon.")
        forecast = self.forecast()
        gameweeks = forecast.gameweeks[:horizon]
        rows = []
        for pid in forecast.catalogue.players:
            projected = [forecast.players[pid][gw] for gw in gameweeks]
            total = (
                sum(p["expected_points"] for p in projected)
                if all(p["expected_points"] is not None for p in projected)
                else None
            )
            rows.append(
                {
                    **self.identity(forecast, pid),
                    "gameweeks": projected,
                    "total_expected_points": total,
                }
            )
        rows.sort(
            key=lambda row: (
                -(
                    row["total_expected_points"]
                    if row["total_expected_points"] is not None
                    else -1e10
                )
            )
        )
        return {
            **self.metadata(forecast),
            "requested_horizon": horizon,
            "gameweeks": list(gameweeks),
            "players": rows,
            "horizon_note": f"Only {len(gameweeks)} Gameweeks remain in the published season."
            if len(gameweeks) < horizon
            else "",
        }

    def player(self, pid: int) -> dict:
        forecast = self.forecast()
        if pid not in forecast.catalogue.players:
            raise ValueError("Unknown player.")
        return {
            **self.metadata(forecast),
            **self.identity(forecast, pid),
            "gameweeks": list(forecast.players[pid].values()),
            "fixtures": [asdict(p) for p in forecast.fixtures if p.player_id == pid],
        }

    def load_squad(self, subject: str, entry_id: int) -> dict:
        forecast = self.forecast()
        snapshot = self.client.public_squad(entry_id, forecast.catalogue)
        validate_squad(
            tuple(p.player_id for p in snapshot.manager.picks), forecast.catalogue.players
        )
        self.squads.set(subject, snapshot)
        self.plans.delete(subject)
        return self.squad(subject)

    def snapshot(self, subject: str) -> PublicSquadSnapshot:
        snapshot = self.squads.get(subject)
        if not snapshot:
            raise ValueError(
                "Load your public squad again; private squad snapshots expire after one hour."
            )
        return snapshot

    def squad(self, subject: str) -> dict:
        snapshot = self.snapshot(subject)
        forecast = self.forecast()
        picks = sorted(snapshot.manager.picks, key=lambda p: p.squad_position)
        return {
            "entry_id": snapshot.entry_id,
            "name": snapshot.name,
            "snapshot_gameweek": snapshot.gameweek,
            "updated_at": snapshot.fetched_at.isoformat(),
            "planning_gameweek": forecast.gameweeks[0],
            "deadline": next(
                g.deadline.isoformat()
                for g in forecast.catalogue.gameweeks
                if g.id == forecast.gameweeks[0]
            ),
            "snapshot_bank_tenths": snapshot.bank_tenths,
            "snapshot_value_tenths": snapshot.value_tenths,
            "free_transfers": None,
            "chips_used": snapshot.chips_used,
            "players": [
                {
                    **self.identity(forecast, p.player_id),
                    "squad_position": p.squad_position,
                    "is_captain": p.is_captain,
                    "is_vice_captain": p.is_vice_captain,
                    "purchase_price_tenths": snapshot.purchase_prices.get(p.player_id),
                    "selling_price_tenths": snapshot.selling_prices.get(p.player_id),
                    "projection": forecast.players[p.player_id][forecast.gameweeks[0]],
                }
                for p in picks
            ],
            "notes": [
                "Last publicly released deadline squad; unpublished transfers and next-deadline picks may differ.",
                "Bank and team value are last-deadline snapshots, not verified live balances. Free transfers are unavailable through public endpoints; enter them manually.",
                "Buying/selling values remain unknown unless supplied by FPL or entered manually. No team changes are submitted to FPL.",
            ],
        }

    @staticmethod
    def check_rules(forecast: HorizonForecast) -> None:
        catalogue = forecast.catalogue
        if (
            catalogue.squad_counts != COUNTS
            or catalogue.formation_limits != LIMITS
            or catalogue.club_limit != 3
        ):
            raise ValueError(
                "Published FPL formation/squad rules are unavailable or differ from the verified rules. Optimisation is disabled until reviewed."
            )

    def optimal(
        self, subject: str, ids: tuple[int, ...], gameweek: int, chip: str | None = None
    ) -> dict:
        forecast = self.forecast()
        self.check_rules(forecast)
        if gameweek not in forecast.gameweeks:
            raise ValueError("Gameweek is outside the cached future horizon.")
        if any(forecast.players[pid][gameweek]["expected_points"] is None for pid in ids):
            raise ValueError(
                f"GW{gameweek} includes unknown or unassigned squad fixtures; a complete lineup score cannot be inferred."
            )
        key = forecast.catalogue.fetched_at, tuple(sorted(ids)), gameweek, chip
        with self.lock:
            plans = self.plans.get(subject)
            if plans is None:
                plans = {}
                self.plans.set(subject, plans)
            if key not in plans:
                started = perf_counter()
                result = LineupOptimiser(
                    forecast.catalogue.players, forecast.outcomes[gameweek], ids
                ).optimise(chip)
                result["optimisation_seconds"] = perf_counter() - started
                if len(plans) >= 64:
                    del plans[next(iter(plans))]
                plans[key] = result
            return plans[key]

    def lineup(
        self, subject: str, gameweek: int | None = None, *, draft: dict | None = None
    ) -> dict:
        snapshot, forecast = self.snapshot(subject), self.forecast()
        gw = gameweek or forecast.gameweeks[0]
        picks = sorted(snapshot.manager.picks, key=lambda p: p.squad_position)
        ids = tuple(p.player_id for p in picks)
        optimal = self.optimal(subject, ids, gw)
        optimiser = LineupOptimiser(forecast.catalogue.players, forecast.outcomes[gw], ids)
        if draft:
            current = optimiser.evaluate(
                tuple(draft["starting_ids"]),
                tuple(draft["bench_ids"]),
                draft["captain"],
                draft["vice_captain"],
            )
        else:
            current = optimiser.evaluate(
                tuple(p.player_id for p in picks[:11]),
                tuple(p.player_id for p in picks[11:]),
                next(p.player_id for p in picks if p.is_captain),
                next(p.player_id for p in picks if p.is_vice_captain),
            )
        return {
            **self.metadata(forecast),
            "gameweek": gw,
            "player_projections": {pid: forecast.players[pid][gw] for pid in ids},
            "recommended": optimal,
            "current": current,
            "expected_gain": optimal["expected_points"] - current["expected_points"],
            "comparison_note": "Same shared outcomes and FPL scoring for both lineups; scores are gross of any already-incurred account hits, which public data cannot verify for the next deadline.",
        }

    def transfers(
        self,
        subject: str,
        moves: list[tuple[int, int]],
        bank: int,
        free_transfers: int,
        selling: dict[int, int],
    ) -> dict:
        snapshot, forecast = self.snapshot(subject), self.forecast()
        self.check_rules(forecast)
        maximum = forecast.catalogue.maximum_free_transfers
        if maximum is None or not 0 <= free_transfers <= maximum or not 0 <= bank <= 2000:
            raise ValueError(
                "Enter a valid confirmed bank balance and free transfers under the published season rules."
            )
        if (
            len(moves) > 5
            or len({a for a, _ in moves}) != len(moves)
            or len({b for _, b in moves}) != len(moves)
        ):
            raise ValueError("Use up to five distinct outgoing and incoming players.")
        original = tuple(p.player_id for p in snapshot.manager.picks)
        ids = set(original)
        if any(a not in ids or b in ids or b not in forecast.catalogue.players for a, b in moves):
            raise ValueError(
                "Outgoing players must belong to the loaded squad; incoming players must be new known players."
            )
        sold = 0
        bought = 0
        for outgoing, incoming in moves:
            value = selling.get(outgoing, snapshot.selling_prices.get(outgoing))
            purchase = forecast.catalogue.players[incoming].price_tenths
            market = forecast.catalogue.players[outgoing].price_tenths
            if value is None or purchase is None or market is None:
                raise ValueError(
                    "Supply each outgoing player's actual selling price; unknown selling values are never replaced by market prices."
                )
            if not 0 <= value <= market:
                raise ValueError(
                    "Selling prices must be non-negative and no higher than the current official market price."
                )
            sold += value
            bought += purchase
        final = tuple(sorted((ids - {a for a, _ in moves}) | {b for _, b in moves}))
        validate_squad(final, forecast.catalogue.players, club_limit=forecast.catalogue.club_limit)
        remaining = bank + sold - bought
        if remaining < 0:
            raise ValueError("The plan exceeds your confirmed bank and selling proceeds.")
        hit = 4 * max(0, len(moves) - free_transfers)
        comparisons = []
        for index, gw in enumerate(forecast.gameweeks):
            try:
                baseline = self.optimal(subject, original, gw)
                changed = self.optimal(subject, final, gw)
                gain = (
                    changed["expected_points"]
                    - baseline["expected_points"]
                    - (hit if index == 0 else 0)
                )
                comparisons.append(
                    {
                        "gameweek": gw,
                        "baseline": baseline["expected_points"],
                        "proposed": changed["expected_points"],
                        "hit": hit if index == 0 else 0,
                        "net_gain": gain,
                    }
                )
            except ValueError as error:
                comparisons.append({"gameweek": gw, "net_gain": None, "note": str(error)})

        def gain_over(length: int):
            rows = comparisons[:length]
            return (
                sum(r["net_gain"] for r in rows)
                if len(rows) == length and all(r["net_gain"] is not None for r in rows)
                else None
            )

        return {
            "moves": [
                {"out": self.identity(forecast, a), "in": self.identity(forecast, b)}
                for a, b in moves
            ],
            "bank_before_tenths": bank,
            "bank_after_tenths": remaining,
            "purchase_cost_tenths": bought,
            "selling_proceeds_tenths": sold,
            "hit": hit,
            "free_transfers_remaining": max(0, free_transfers - len(moves)),
            "gameweeks": comparisons,
            "gain_next": gain_over(1),
            "gain_3": gain_over(3),
            "gain_5": gain_over(5),
            "assumptions": [
                "Manual plan comparison, not an automatic or globally optimal transfer recommendation.",
                "Each fixed squad is optimised independently in every projected Gameweek with captaincy and legal autosubs. No further transfers, future price changes, chip use or value of saved free transfers are priced.",
                "Entered bank/free transfers/selling prices must reflect your actual account. The loaded public squad must still match your current squad.",
                "Prospective hits are deducted once in the first Gameweek. Pre-existing private hits are not known.",
            ],
        }

    @staticmethod
    def screening_score(
        forecast: HorizonForecast, ids: tuple[int, ...], gameweeks: tuple[int, ...]
    ) -> float | None:
        """Cheap legal mean-XI/captain screening; final rankings use joint autosub scoring."""
        total = 0.0
        for gw in gameweeks:
            means = {pid: forecast.players[pid][gw]["expected_points"] for pid in ids}
            if any(value is None for value in means.values()):
                return None
            by_position = {
                position: sorted(
                    (
                        means[pid]
                        for pid in ids
                        if forecast.catalogue.players[pid].position == position
                    ),
                    reverse=True,
                )
                for position in COUNTS
            }
            scores = []
            for defenders in range(3, 6):
                for midfielders in range(2, 6):
                    forwards = 10 - defenders - midfielders
                    if not 1 <= forwards <= 3:
                        continue
                    selected = (
                        by_position[Position.GOALKEEPER][:1]
                        + by_position[Position.DEFENDER][:defenders]
                        + by_position[Position.MIDFIELDER][:midfielders]
                        + by_position[Position.FORWARD][:forwards]
                    )
                    scores.append(sum(selected) + max(selected))
            total += max(scores)
        return total

    def transfer_search(
        self,
        subject: str,
        outgoing: int,
        bank: int,
        free_transfers: int,
        selling: dict[int, int],
        horizon: int,
    ) -> dict:
        """Bounded two-stage single-transfer search, explicitly not a global optimum."""
        if horizon not in {1, 3, 5, 6}:
            raise ValueError("Choose a 1, 3, 5 or 6 Gameweek horizon.")
        # Reuse all account/rule validation and the exact no-transfer baseline.
        baseline = self.transfers(subject, [], bank, free_transfers, {})
        snapshot, forecast = self.snapshot(subject), self.forecast()
        original = tuple(p.player_id for p in snapshot.manager.picks)
        if outgoing not in original:
            raise ValueError("Choose an outgoing player from the loaded squad.")
        sell = selling.get(outgoing, snapshot.selling_prices.get(outgoing))
        market = forecast.catalogue.players[outgoing].price_tenths
        if sell is None or market is None or not 0 <= sell <= market:
            raise ValueError(
                "Confirm the outgoing player's actual selling price; market value is not a substitute."
            )
        gameweeks = forecast.gameweeks[:horizon]
        if self.screening_score(forecast, original, gameweeks) is None:
            raise ValueError("The selected horizon has unknown squad fixture coverage.")
        eligible = []
        for pid, player in forecast.catalogue.players.items():
            if (
                pid in original
                or player.position != forecast.catalogue.players[outgoing].position
                or player.price_tenths is None
                or player.price_tenths > bank + sell
            ):
                continue
            ids = tuple(pid if p == outgoing else p for p in original)
            try:
                validate_squad(
                    ids, forecast.catalogue.players, club_limit=forecast.catalogue.club_limit
                )
            except ValueError:
                continue
            score = self.screening_score(forecast, ids, gameweeks)
            if score is not None:
                eligible.append((score, pid))
        shortlisted = sorted(eligible, key=lambda item: (-item[0], item[1]))[:8]
        results = []
        for _, pid in shortlisted:
            plan = self.transfers(
                subject, [(outgoing, pid)], bank, free_transfers, {outgoing: sell}
            )
            rows = plan["gameweeks"][: len(gameweeks)]
            gain = (
                sum(row["net_gain"] for row in rows)
                if all(row["net_gain"] is not None for row in rows)
                else None
            )
            results.append({**plan, "gain_horizon": gain})
        results.sort(
            key=lambda plan: -(plan["gain_horizon"] if plan["gain_horizon"] is not None else -1e10)
        )
        return {
            "gameweeks": list(gameweeks),
            "eligible_candidates": len(eligible),
            "shortlist_limit": 8,
            "evaluated_candidates": len(results),
            "no_transfer_gain": 0,
            "baseline": baseline,
            "recommendations": results,
            "notes": [
                "Single outgoing player only. Affordable, legal replacements are screened using legal mean-XI scores and captain points across the selected horizon, then the best eight are evaluated with joint draws, vice takeover and legal autosubs.",
                "Screening excludes autosub and vice effects and can miss the best transfer. This is a bounded heuristic, not a globally optimal search. Final gains re-optimise both whole squads each Gameweek and include a prospective hit once.",
                "Keeping your squad has zero incremental gain. Negative gains favour no transfer. Saved free-transfer value, future transfers, chip use and prices are not predicted.",
                f"Rankings cover the {len(gameweeks)} published future Gameweeks shown; unavailable later Gameweeks are not invented.",
            ],
        }

    def chips(self, subject: str) -> dict:
        snapshot, forecast = self.snapshot(subject), self.forecast()
        ids = tuple(p.player_id for p in snapshot.manager.picks)
        rows = []
        for gw in forecast.gameweeks:
            for chip in ("bboost", "3xc"):
                window = next(
                    (
                        c
                        for c in forecast.catalogue.chips
                        if c.name == chip and c.start <= gw <= c.stop
                    ),
                    None,
                )
                used = (
                    None
                    if snapshot.chips_used is None
                    else sum(
                        name == chip and window is not None and window.start <= event <= window.stop
                        for name, event in snapshot.chips_used
                    )
                )
                available = window is not None and used is not None and used < window.quantity
                row = {
                    "gameweek": gw,
                    "chip": chip,
                    "availability": "unused_at_public_snapshot"
                    if available
                    else "unknown"
                    if used is None
                    else "unavailable_or_used",
                    "expected_gain": None,
                }
                if available:
                    try:
                        baseline = self.optimal(subject, ids, gw)
                        with_chip = self.optimal(subject, ids, gw, chip)
                        row.update(
                            expected_gain=with_chip["expected_points"]
                            - baseline["expected_points"],
                            expected_points=with_chip["expected_points"],
                            captain=with_chip["captain"],
                            formation=with_chip["formation"],
                        )
                    except ValueError as error:
                        row["note"] = str(error)
                rows.append(row)
        return {
            "comparisons": rows,
            "notes": [
                "Conditional Bench Boost / Triple Captain values for the same unchanged squad, with a legal optimal XI in each Gameweek.",
                "Compare gain now with other published Gameweeks in this window. Opportunity cost outside the horizon, future transfers and injury changes are unknown; these are not definitive chip-timing recommendations.",
                "Availability is based on public chip history and the current official chip windows. Confirm your account before acting; only one chip can be played in a Gameweek.",
                "Free Hit and Wildcard require a budget-constrained whole-squad and future-transfer optimiser and are not implemented.",
            ],
        }
