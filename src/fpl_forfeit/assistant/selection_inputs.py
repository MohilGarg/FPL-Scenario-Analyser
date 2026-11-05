"""Timestamped selection evidence, separate from fitted probabilities and point draws.

No prose is interpreted into injury dates. Availability at a cutoff requires an
original retrieval; publication/last-update fields cannot backdate a download.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .external_forecasts import ForecastBatch, season_of, timestamp
from .prospective import canonical, immutable

ATTRIBUTION = "Data provided by football-data.org (https://www.football-data.org)"


def official_availability(bootstrap: dict, retrieved_at: datetime) -> dict:
    retrieved = timestamp(retrieved_at)
    future = [e for e in bootstrap["events"] if timestamp(e["deadline_time"]) > retrieved]
    event = min(future, key=lambda e: e["id"]) if future else None
    rows = []
    for player in bootstrap["elements"]:
        chance = player.get("chance_of_playing_next_round")
        if chance is not None and (isinstance(chance, bool) or not 0 <= chance <= 100):
            raise ValueError("Invalid official availability chance")
        published = timestamp(player["news_added"]) if player.get("news_added") else None
        if published and published > retrieved:
            raise ValueError("Official news publication is later than retrieval")
        rows.append(
            {
                "player_id": int(player["id"]),
                "club_id": int(player["team"]) if player.get("team") is not None else None,
                "status": player.get("status"),
                "chance_next_gw": chance,
                "gameweek": event["id"] if event else None,
                "published_at": published.isoformat() if published else None,
                "retrieved_at": retrieved.isoformat(),
                "has_news": bool(player.get("news")),
                "return_date": None,
                "confidence": "official_flag_not_confirmed_lineup",
                "horizon": "next_gameweek_only",
            }
        )
    return {
        "schema_version": 1,
        "source": "official_fpl_availability",
        "season": season_of(bootstrap),
        "retrieved_at": retrieved.isoformat(),
        "attribution": "Official Fantasy Premier League",
        "redistribution_allowed": False,
        "rows": rows,
        "policy": "Publication unknown stays null; official status/chance/news presence only, no inferred report meaning or return date.",
    }


def provider_selection(batch: ForecastBatch) -> dict:
    if batch.source != "sideline_quant":
        raise ValueError("Explicit fixture selection definitions required")
    return {
        "schema_version": 1,
        "source": "sideline_quant_selection",
        "season": batch.season,
        "retrieved_at": batch.retrieved_at,
        "published_at": batch.computed_at,
        "attribution": batch.attribution,
        "redistribution_allowed": batch.redistribution_allowed,
        "rows": [
            {
                "player_id": r.player_id,
                "gameweek": r.gameweek,
                "fixture_id": r.fixture_id,
                "expected_minutes": r.expected_minutes,
                "start_probability": r.start_probability,
                "no_show_probability": r.no_show_probability,
                "substitute_probability": r.cameo_probability,
                "probabilities": r.source_probabilities,
                "definitions": r.probability_definitions,
                "confidence": "provider_forecast_not_confirmed_lineup",
            }
            for r in batch.rows
        ],
    }


def club_report(
    report: dict, bootstrap: dict, retrieved_at: datetime, *, official_domains: set[str]
) -> dict:
    """Strict human-supplied structured fact; no scraping or LLM medical inference."""
    retrieved = timestamp(retrieved_at)
    published = timestamp(report["published_at"])
    url = urlsplit(report["source_url"])
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.hostname not in official_domains
    ):
        raise ValueError("An explicitly verified official club domain is required")
    if published > retrieved or report["season"] != season_of(bootstrap):
        raise ValueError("Club report unavailable at retrieval or wrong season")
    if report["player_id"] not in {e["id"] for e in bootstrap["elements"]}:
        raise ValueError("Unknown season-specific FPL player")
    if report["fact"] not in {
        "ruled_out",
        "available_for_selection",
        "selection_uncertain",
        "suspended",
        "confirmed_lineup",
    }:
        raise ValueError("Unsupported structured fact; do not infer recovery dates")
    if report.get("confidence") not in {"confirmed_statement", "uncertain_report"}:
        raise ValueError("Confirmed facts and uncertain reports must be distinguished")
    return {
        k: report[k]
        for k in ("player_id", "season", "published_at", "source_url", "fact", "confidence")
    } | {
        "retrieved_at": retrieved.isoformat(),
        "source": "manual_official_club_report",
        "redistribution_allowed": False,
        "appearance_probability": None,
        "return_date": None,
        "attribution": url.hostname,
    }


def available_at(evidence: dict, cutoff: datetime, deadline: datetime) -> bool:
    cutoff, deadline = timestamp(cutoff), timestamp(deadline)
    retrieved = timestamp(evidence["retrieved_at"])
    published = timestamp(evidence["published_at"]) if evidence.get("published_at") else retrieved
    return published <= retrieved <= cutoff < deadline


def competition_matches(
    payload: dict, bootstrap: dict, mapping: dict, retrieved_at: datetime
) -> dict:
    retrieved = timestamp(retrieved_at)
    if mapping.get("season") != season_of(bootstrap):
        raise ValueError("Cross-competition club mapping season mismatch")
    club_map = mapping["teams"]
    clubs = {int(t["id"]) for t in bootstrap["teams"]}
    if (
        not club_map
        or len(set(club_map.values())) != len(club_map)
        or any(isinstance(pid, bool) or pid not in clubs for pid in club_map.values())
    ):
        raise ValueError("Explicit season-verified football-data club mapping required")
    rows = []
    for match in payload["matches"]:
        if not str(match.get("season", {}).get("startDate", "")).startswith(
            season_of(bootstrap)[:4]
        ):
            raise ValueError("Competition fixture season is unverified")
        competition = match["competition"]["code"]
        if competition not in {"CL", "PL"}:
            raise ValueError("Only documented free-tier competitions are automated")
        updated = timestamp(match["lastUpdated"]) if match.get("lastUpdated") else None
        if updated and updated > retrieved:
            raise ValueError("Future source update timestamp")
        home = club_map.get(str(match["homeTeam"]["id"]))
        away = club_map.get(str(match["awayTeam"]["id"]))
        if home is None and away is None:
            continue
        rows.append(
            {
                "provider_fixture_id": int(match["id"]),
                "competition": competition,
                "home_club_id": home,
                "away_club_id": away,
                "kickoff": timestamp(match["utcDate"]).isoformat(),
                "status": match["status"],
                "published_at": None,
                "source_updated_at": updated.isoformat() if updated else None,
                "retrieved_at": retrieved.isoformat(),
                "confidence": "published_club_calendar_not_player_participation",
            }
        )
    return {
        "schema_version": 1,
        "source": "football_data_calendar",
        "season": season_of(bootstrap),
        "retrieved_at": retrieved.isoformat(),
        "attribution": ATTRIBUTION,
        "redistribution_allowed": False,
        "coverage_complete": False,
        "coverage_note": "Configured competitions only; other domestic/European cups remain unknown.",
        "rows": rows,
    }


def congestion(evidence: dict, club_id: int, kickoff: datetime, cutoff: datetime) -> dict:
    kickoff, cutoff = timestamp(kickoff), timestamp(cutoff)
    known = [
        r
        for r in evidence["rows"]
        if timestamp(r["retrieved_at"]) <= cutoff
        and club_id in (r["home_club_id"], r["away_club_id"])
        and r["status"] not in {"CANCELLED", "POSTPONED"}
        and timestamp(r["kickoff"]) < kickoff
    ]
    times = [timestamp(r["kickoff"]) for r in known]
    return {
        "known_competition_matches_previous_7d": sum(
            kickoff - timedelta(days=7) <= t for t in times
        ),
        "days_since_known_match": (kickoff - max(times)).total_seconds() / 86400 if times else None,
        "coverage_complete": False,
        "player_minutes_unknown": True,
    }


def save_evidence(value: dict, directory: Path) -> Path:
    if value["source"] not in {
        "official_fpl_availability",
        "sideline_quant_selection",
        "football_data_calendar",
        "manual_official_club_report",
    }:
        raise ValueError("Unknown selection evidence source")
    body = canonical(value)
    digest = hashlib.sha256(body).hexdigest()
    path = (
        directory
        / f"{timestamp(value['retrieved_at']):%Y%m%dT%H%M%S%fZ}-{value['source']}-{digest[:12]}.json"
    )
    immutable(path, body)
    return path


def collect_calendar(
    competition: str, token: str, directory: Path, *, opener=None, now=None, sleeper=None
) -> tuple[dict, datetime]:
    """Private one-hour cache and bounded retries; auth tokens are never serialized/logged."""
    import time
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    if competition not in {"CL", "PL"} or not token:
        raise ValueError("Configured free-tier competition and token required")
    observed = timestamp(now or datetime.now(UTC))
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    path = directory / f"football-data-{competition}.json"
    if path.exists():
        cached = json.loads(path.read_bytes())
        retrieved = timestamp(cached["retrieved_at"])
        if timedelta(0) <= observed - retrieved < timedelta(hours=1):
            return cached["payload"], retrieved
    for attempt in range(3):
        try:
            request = Request(
                f"https://api.football-data.org/v4/competitions/{competition}/matches",
                headers={"X-Auth-Token": token, "User-Agent": "FPL-Scenario-Analyser/research"},
            )
            with opener(request, timeout=20) as response:
                body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Calendar payload too large")
            payload = json.loads(body)
            if not isinstance(payload.get("matches"), list):
                raise TypeError("Calendar match schema missing")
            directory.mkdir(parents=True, exist_ok=True)
            completed = timestamp(now or datetime.now(UTC))
            path.write_bytes(canonical({"payload": payload, "retrieved_at": completed.isoformat()}))
            return payload, completed
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(
                    "Calendar access unavailable; no access-control bypass"
                ) from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("Calendar transport unavailable") from None
        sleeper((1, 3)[attempt])
    raise RuntimeError("Calendar unavailable")
