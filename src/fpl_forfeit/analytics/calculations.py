from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median, pstdev, quantiles
from typing import Any

POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def average(values: list[int | float]) -> float | None:
    return round(mean(values), 2) if values else None


def distribution(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"median": None, "q1": None, "q3": None, "bins": []}
    quartiles = quantiles(values, n=4, method="inclusive") if len(values) > 1 else values * 3
    counts = Counter((value // 10) * 10 for value in values)
    return {
        "median": median(values),
        "q1": quartiles[0],
        "q3": quartiles[2],
        "bins": [
            {"label": f"{low}–{low + 9}", "value": counts[low]}
            for low in range(min(counts), max(counts) + 1, 10)
        ],
    }


def season_summary(
    members: list[dict], histories: dict[int, dict], completed: list[int]
) -> dict[str, Any]:
    """Only fully covered weeks get league ranks. Ties share competition rank.

    Every tied lowest scorer counts as last. Bottom three includes ties at the
    third-lowest score (so it can contain more than three managers).
    """
    rows: dict[int, list[dict]] = defaultdict(list)
    managers = []
    chips = []
    for member in members:
        entry = member["entry_id"]
        history = histories.get(entry, {})
        manager_chips = [
            dict(c, entry_id=entry) for c in history.get("chips", []) if c.get("event") in completed
        ]
        chips.extend(manager_chips)
        for item in history.get("current", []):
            if item.get("event") not in completed:
                continue
            # Neither absent scores nor absent transfer costs mean zero.
            if item.get("points") is None or item.get("event_transfers_cost") is None:
                continue
            gw = item["event"]
            row = {
                "entry_id": entry,
                "name": member["name"],
                "gameweek": gw,
                "raw": item["points"],
                "hits": item["event_transfers_cost"],
                "score": item["points"] - item["event_transfers_cost"],
                "transfers": item.get("event_transfers"),
                "chips": [c["name"] for c in manager_chips if c["event"] == gw],
                "rank": None,
                "last": None,
                "bottom_three": None,
                "unused_bench": None,
                "detail": None,
            }
            rows[gw].append(row)
    weeks = []
    for gw in completed:
        ordered = sorted(rows[gw], key=lambda r: (-r["score"], r["entry_id"]))
        scores = sorted(r["score"] for r in ordered)
        full = len(ordered) == len(members)
        if full:
            for row in ordered:
                row["rank"] = 1 + sum(score > row["score"] for score in scores)
                row["last"] = row["score"] == scores[0]
                row["bottom_three"] = row["score"] <= scores[min(2, len(scores) - 1)]
                row["league_average"] = average(scores)
        weeks.append(
            {
                "gameweek": gw,
                "coverage": len(ordered),
                "complete": full,
                "rows": ordered,
                "unavailable_members": [
                    m for m in members if m["entry_id"] not in {r["entry_id"] for r in ordered}
                ],
                "average": average(scores),
                "bottom": scores[0] if scores and full else None,
                "second_last": scores[1] if len(scores) > 1 and full else None,
                "margin": scores[1] - scores[0] if len(scores) > 1 and full else None,
                "top": scores[-1] if scores and full else None,
                "last_names": [r["name"] for r in ordered if r["last"]],
                "top_names": [r["name"] for r in ordered if full and r["score"] == scores[-1]],
                "hits": sum(r["hits"] for r in ordered) if ordered else None,
                "chips": [c for c in chips if c["event"] == gw],
            }
        )
    for member in members:
        history_rows = [
            row for week in weeks for row in week["rows"] if row["entry_id"] == member["entry_id"]
        ]
        scores = [r["score"] for r in history_rows]
        ranked = [r for r in history_rows if r["rank"] is not None]
        gaps = [r["score"] - r["league_average"] for r in ranked]
        escapes = [
            r["score"] - w["bottom"]
            for w in weeks
            if w["complete"]
            for r in w["rows"]
            if r["entry_id"] == member["entry_id"] and not r["last"]
        ]
        losses = [
            w["margin"]
            for w in weeks
            if w["margin"] is not None
            for r in w["rows"]
            if r["entry_id"] == member["entry_id"] and r["last"]
        ]
        cumulative = 0
        cumulative_hits = 0
        for row in history_rows:
            cumulative += row["score"]
            cumulative_hits += row["hits"]
            row["cumulative"] = cumulative
            row["cumulative_hits"] = cumulative_hits
        costs = [r["hits"] for r in history_rows]
        transfers = [r["transfers"] for r in history_rows if r["transfers"] is not None]
        managers.append(
            {
                **member,
                "scores_analysed": len(scores),
                "ranked_weeks": len(ranked),
                "average": average(scores),
                "median": median(scores) if scores else None,
                "best": max(scores) if scores else None,
                "worst": min(scores) if scores else None,
                "standard_deviation": round(pstdev(scores), 2) if scores else None,
                "range": max(scores) - min(scores) if scores else None,
                "total_points": sum(scores) if scores else None,
                "times_last": sum(r["last"] for r in ranked) if ranked else None,
                "bottom_three": sum(r["bottom_three"] for r in ranked) if ranked else None,
                "wins": sum(r["rank"] == 1 for r in ranked) if ranked else None,
                "average_rank": average([r["rank"] for r in ranked]),
                "closest_escape": min(escapes) if escapes else None,
                "largest_last_margin": max(losses) if losses else None,
                "above_average_pct": round(100 * sum(g > 0 for g in gaps) / len(gaps), 1)
                if gaps
                else None,
                "below_average_pct": round(100 * sum(g < 0 for g in gaps) / len(gaps), 1)
                if gaps
                else None,
                "average_distance": average([abs(g) for g in gaps]),
                "hits": {
                    "total": sum(costs) if costs else None,
                    "weeks": sum(c > 0 for c in costs),
                    "four": costs.count(4),
                    "eight": costs.count(8),
                    "larger": sum(c > 8 for c in costs),
                    "no_hit": costs.count(0),
                    "average": average(costs),
                    "maximum": max(costs) if costs else None,
                    "transfers": sum(transfers) if transfers else None,
                    "transfer_coverage": len(transfers),
                },
                "chips": [c for c in chips if c["entry_id"] == member["entry_id"]],
                "history": history_rows,
            }
        )
    all_rows = [r for w in weeks for r in w["rows"]]
    all_scores = [r["score"] for r in all_rows]
    margins = [w for w in weeks if w["margin"] is not None]
    return {
        "managers": managers,
        "gameweeks": weeks,
        "chips": chips,
        "coverage": {
            "scores": len(all_scores),
            "expected": len(completed) * len(members),
            "ranked_gameweeks": sum(w["complete"] for w in weeks),
        },
        "totals": {
            "average": average(all_scores),
            "highest": max(all_rows, key=lambda r: r["score"]) if all_rows else None,
            "lowest": min(all_rows, key=lambda r: r["score"]) if all_rows else None,
            "closest_finish": min(margins, key=lambda w: w["margin"])["gameweek"]
            if margins
            else None,
            "largest_margin": max(margins, key=lambda w: w["margin"])["gameweek"]
            if margins
            else None,
            "hits": sum(r["hits"] for r in all_rows) if all_rows else None,
            "chips": len(chips),
            "unused_bench": None,
        },
        "distribution": distribution(all_scores),
    }


def scoring_detail(picks: dict, live: dict, players: dict[int, dict]) -> dict:
    """Use final published multipliers, never simulate historical substitutions.

    Event live points already include every DGW fixture. Reconciliation with the
    official raw event score prevents publishing fabricated contribution totals.
    """
    points = {
        item["id"]: item.get("stats", {}).get("total_points") for item in live.get("elements", [])
    }
    selection = picks.get("picks", [])
    if len(selection) != 15 or len({p["element"] for p in selection}) != 15:
        raise ValueError("Historical squad is incomplete")
    if any(points.get(p["element"]) is None or p.get("multiplier") is None for p in selection):
        raise ValueError("Historical player points or multipliers are missing")
    raw = sum(points[p["element"]] * p["multiplier"] for p in selection)
    if raw != picks.get("entry_history", {}).get("points"):
        raise ValueError("Historical player contributions do not reconcile to the official score")
    chip = picks.get("active_chip")
    positions = Counter()
    contributions = []
    chosen = next((p["element"] for p in selection if p.get("is_captain")), None)
    if chosen is None or "active_chip" not in picks:
        raise ValueError("Historical captain or chip state is missing")
    actual = next((p for p in selection if p["multiplier"] > 1), None)
    position_complete = True
    for p in selection:
        player = players.get(p["element"], {})
        position = POSITIONS.get(p.get("element_type", player.get("element_type")))
        base = points[p["element"]]
        counted = base * p["multiplier"]
        if position is None:
            position_complete = False
        else:
            positions[position] += counted
        contributions.append(
            {
                "id": p["element"],
                "name": player.get("web_name", f"Player #{p['element']}"),
                "position": position,
                "base": base,
                "multiplier": p["multiplier"],
                "points": counted,
                "scoring": p["multiplier"] > 0,
                "captain": bool(p.get("is_captain")),
                "additional": base * max(0, p["multiplier"] - 1),
            }
        )
    # Final multiplier-zero contributions did not enter the manager's score.
    # FPL reorders final bench positions after autosubs; never count an incoming
    # substitute as unused just because they began on the bench.
    unused = (
        0
        if chip == "bboost"
        else sum(points[p["element"]] for p in selection if p["multiplier"] == 0)
    )
    boost = (
        sum(points[p["element"]] * p["multiplier"] for p in selection if p["position"] > 11)
        if chip == "bboost"
        else None
    )
    captain_total = points[actual["element"]] * actual["multiplier"] if actual else 0
    additional = points[actual["element"]] * (actual["multiplier"] - 1) if actual else 0
    return {
        "raw": raw,
        "unused_bench": unused,
        "bench_boost": boost,
        "captain_chosen": chosen,
        "captain_name": players.get(chosen, {}).get("web_name", f"Player #{chosen}"),
        "effective_captain": actual["element"] if actual else None,
        "captain_points": captain_total,
        "captain_additional": additional,
        "triple_captain_additional": additional if chip == "3xc" else None,
        "positions": {p: positions[p] for p in POSITIONS.values()} if position_complete else None,
        "players": contributions,
    }


def enrich_season(summary: dict, details: dict[tuple[int, int], dict]) -> dict:
    contributions: dict[int, dict] = {}
    captain_weeks = []
    ownership_history = []
    season_captains = Counter()
    for week in summary["gameweeks"]:
        captains = Counter()
        owned = Counter()
        for row in week["rows"]:
            detail = details.get((row["entry_id"], row["gameweek"]))
            row["detail"] = detail
            row["unused_bench"] = detail["unused_bench"] if detail else None
            if detail:
                captains[detail["captain_chosen"]] += 1
                season_captains[detail["captain_chosen"]] += 1
                for p in detail["players"]:
                    owned[p["id"]] += 1
                    record = contributions.setdefault(
                        p["id"],
                        {
                            "id": p["id"],
                            "name": p["name"],
                            "points": 0,
                            "scoring_appearances": 0,
                            "captaincies": 0,
                            "additional": 0,
                        },
                    )
                    record["points"] += p["points"]
                    record["scoring_appearances"] += p["scoring"]
                    record["captaincies"] += p["captain"]
                    record["additional"] += p["additional"]
        captain_weeks.append(
            {
                "gameweek": week["gameweek"],
                "coverage": sum(captains.values()),
                "choices": [
                    {
                        "id": player,
                        "count": count,
                        "name": contributions.get(player, {}).get("name", f"Player #{player}"),
                    }
                    for player, count in captains.most_common()
                ],
                "concentration_pct": round(100 * max(captains.values()) / sum(captains.values()), 1)
                if captains
                else None,
                "unique_choices": sum(count == 1 for count in captains.values())
                if captains
                else None,
                "most_chosen": [
                    contributions.get(pid, {}).get("name", f"Player #{pid}")
                    for pid, count in captains.items()
                    if count == max(captains.values())
                ],
            }
        )
        ownership_history.append(
            {
                "gameweek": week["gameweek"],
                "coverage": sum(captains.values()),
                "most_owned": [
                    contributions[pid]["name"]
                    for pid, count in owned.items()
                    if count == max(owned.values())
                ],
                "most_owned_count": max(owned.values()) if owned else None,
                "players": [
                    {
                        "id": pid,
                        "name": contributions[pid]["name"],
                        "owned": count,
                        "captains": captains[pid],
                    }
                    for pid, count in owned.most_common()
                ],
            }
        )
    # Rows in manager history and gameweeks share identity in the summary builder.
    for manager in summary["managers"]:
        available = [r["detail"] for r in manager["history"] if r["detail"]]
        bench = [d["unused_bench"] for d in available]
        manager["detail_coverage"] = len(available)
        manager["bench"] = {
            "total": sum(bench) if bench else None,
            "average": average(bench),
            "highest": max(bench) if bench else None,
            "boost": sum(d["bench_boost"] or 0 for d in available) if available else None,
        }
        captain_points = [d["captain_points"] for d in available]
        best = max(
            (r for r in manager["history"] if r["detail"]),
            key=lambda r: r["detail"]["captain_points"],
            default=None,
        )
        manager["captaincy"] = {
            "total": sum(captain_points) if available else None,
            "additional": sum(d["captain_additional"] for d in available) if available else None,
            "average": average(captain_points),
            "different": len({d["captain_chosen"] for d in available}) if available else None,
            "best_gameweek": best["gameweek"] if best else None,
            "best_points": best["detail"]["captain_points"] if best else None,
        }
        positional = [d for d in available if d["positions"] is not None]
        position_total = sum(d["raw"] for d in positional)
        manager["positions"] = (
            [
                {
                    "position": p,
                    "points": sum(d["positions"][p] for d in positional),
                    "percentage": round(
                        100 * sum(d["positions"][p] for d in positional) / position_total, 1
                    )
                    if position_total
                    else None,
                }
                for p in POSITIONS.values()
            ]
            if positional
            else []
        )
        manager["position_coverage"] = len(positional)
        for chip in manager["chips"]:
            detail = details.get((manager["entry_id"], chip["event"]))
            chip["contribution"] = (
                (
                    detail["bench_boost"]
                    if chip["name"] == "bboost"
                    else detail["triple_captain_additional"]
                    if chip["name"] == "3xc"
                    else None
                )
                if detail
                else None
            )
    summary["detail_coverage"] = {
        "available": len(details),
        "expected": summary["coverage"]["scores"],
    }
    summary["totals"]["unused_bench"] = (
        sum(d["unused_bench"] for d in details.values()) if details else None
    )
    summary["captaincy"] = {
        "gameweeks": captain_weeks,
        "players": [
            {
                "id": pid,
                "name": contributions.get(pid, {}).get("name", f"Player #{pid}"),
                "chosen": count,
            }
            for pid, count in season_captains.most_common()
        ],
        "total": sum(d["captain_points"] for d in details.values()) if details else None,
        "additional": sum(d["captain_additional"] for d in details.values()) if details else None,
    }
    summary["contributions"] = sorted(contributions.values(), key=lambda p: -p["points"])
    summary["ownership_history"] = ownership_history
    return summary


def current_squads(
    members: list[dict],
    squads: dict[int, dict],
    players: dict[int, dict],
    multipliers: dict[int, dict[int, int]] | None = None,
) -> dict:
    records: dict[int, dict] = {}
    count = len(members)
    available = len(squads)
    for member in members:
        entry = member["entry_id"]
        for pick in squads.get(entry, {}).get("picks", []):
            pid = pick["element"]
            player = players.get(pid, {})
            record = records.setdefault(
                pid,
                {
                    "id": pid,
                    "name": player.get("web_name", f"Player #{pid}"),
                    "position": POSITIONS.get(player.get("element_type")),
                    "owners": [],
                    "starting": 0,
                    "bench": 0,
                    "captains": 0,
                    "vice_captains": 0,
                    "effective_weight": 0,
                },
            )
            record["owners"].append(member["name"])
            record["starting"] += pick["position"] <= 11
            record["bench"] += pick["position"] > 11
            record["captains"] += bool(pick.get("is_captain"))
            record["vice_captains"] += bool(pick.get("is_vice_captain"))
            weight = (
                multipliers[entry].get(pid, 0)
                if multipliers is not None and entry in multipliers
                else pick.get("multiplier")
            )
            if weight is None:
                raise ValueError("Current pick multiplier missing")
            record["effective_weight"] += weight
    for record in records.values():
        record["owned"] = len(record["owners"])
        record["ownership_pct"] = (
            round(100 * record["owned"] / count, 1) if available == count else None
        )
        record["effective_pct"] = (
            round(100 * record["effective_weight"] / count, 1) if available == count else None
        )
    pairs = []
    for index, a in enumerate(members):
        for b in members[index + 1 :]:
            if a["entry_id"] not in squads or b["entry_id"] not in squads:
                continue
            sa, sb = squads[a["entry_id"]]["picks"], squads[b["entry_id"]]["picks"]
            pa, pb = {p["element"] for p in sa}, {p["element"] for p in sb}

            def named(ids: set[int]) -> list[dict]:
                return [
                    {"id": pid, "name": players.get(pid, {}).get("web_name", f"Player #{pid}")}
                    for pid in sorted(ids)
                ]

            pairs.append(
                {
                    "a": a["entry_id"],
                    "b": b["entry_id"],
                    "a_name": a["name"],
                    "b_name": b["name"],
                    "shared_count": len(pa & pb),
                    "percentage": round(100 * len(pa & pb) / 15, 1),
                    "shared": named(pa & pb),
                    "only_a": named(pa - pb),
                    "only_b": named(pb - pa),
                    "captain_a": named({p["element"] for p in sa if p.get("is_captain")}),
                    "captain_b": named({p["element"] for p in sb if p.get("is_captain")}),
                }
            )
    return {
        "players": sorted(records.values(), key=lambda r: -r["owned"]),
        "pairs": pairs,
        "coverage": available,
        "manager_count": count,
        "definition": "Overlap = shared players / 15. Starting/bench counts use published squad positions; effective ownership uses current scoring multipliers, including confirmed autosubs and captaincy. It can exceed 100%.",
    }


def head_to_head(summary: dict, a: int, b: int) -> dict:
    by_id = {m["entry_id"]: m for m in summary["managers"]}
    ma, mb = by_id[a], by_id[b]
    rb = {r["gameweek"]: r for r in mb["history"]}
    rows = [
        {
            "gameweek": r["gameweek"],
            "a": r["score"],
            "b": rb[r["gameweek"]]["score"],
            "difference": r["score"] - rb[r["gameweek"]]["score"],
        }
        for r in ma["history"]
        if r["gameweek"] in rb
    ]
    diffs = [r["difference"] for r in rows]
    return {
        "a": ma,
        "b": mb,
        "rows": rows,
        "common_gameweeks": len(rows),
        "a_wins": sum(d > 0 for d in diffs),
        "b_wins": sum(d < 0 for d in diffs),
        "ties": diffs.count(0),
        "average_difference": average(diffs),
        "largest_a_win": max([d for d in diffs if d > 0], default=None),
        "largest_b_win": max([-d for d in diffs if d < 0], default=None),
        "a_common_points": sum(r["a"] for r in rows) if rows else None,
        "b_common_points": sum(r["b"] for r in rows) if rows else None,
    }
