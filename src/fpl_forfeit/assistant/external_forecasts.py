"""Private, cutoff-aware public-forecast adapters and conservative point-only blends.

No network or filesystem work happens on import or in the parsers. Raw source data is
never returned by a public route. External means do not replace joint simulation draws.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .prospective import canonical, immutable

SOURCE_URLS = {
    "fpl_cat": "https://fplcat.com/api/v1/projections",
    "sideline_quant": "https://sidelinequant.com/data/latest.json",
    "official_fpl": "https://fantasy.premierleague.com/api/bootstrap-static/",
}
ATTRIBUTIONS = {
    "fpl_cat": "Projections: FPL Cat (https://fplcat.com)",
    "sideline_quant": "Sideline Quant (https://sidelinequant.com), CC BY 4.0",
    "official_fpl": "Official Fantasy Premier League (https://fantasy.premierleague.com)",
    "fpl_form": "FPL Form (https://fplform.com); personal use, no redistribution",
    "fpl_review": "FPL Review (https://fplreview.com); local personal import only",
}


def may_redistribute(batch: ForecastBatch) -> bool:
    """Allowlist model-only source contracts, never trust an imported boolean."""
    return batch.redistribution_allowed and (
        (batch.source == "fpl_cat" and batch.source_version == "api/v1")
        or (batch.source == "sideline_quant" and batch.source_version == "1.0")
    ) and batch.attribution == ATTRIBUTIONS.get(batch.source)


def timestamp(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Forecast timestamps must include a timezone")
    return result.astimezone(UTC)


def number(value: Any, *, optional: bool = False) -> float | None:
    if optional and (value is None or value == ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a forecast number")  # noqa: TRY004 - uniform schema-validation contract
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Forecast numbers must be finite")
    return result


def probability(value: Any) -> float | None:
    result = number(value, optional=True)
    if result is not None and not 0 <= result <= 1:
        raise ValueError("Probability outside [0, 1]")
    return result


def positive_id(value: Any) -> int:
    if isinstance(value, bool) or float(value) != int(value) or int(value) <= 0:
        raise ValueError("Invalid FPL identifier")
    return int(value)


def season_of(bootstrap: dict) -> str:
    year = timestamp(bootstrap["events"][0]["deadline_time"]).year
    return f"{year}-{str(year + 1)[-2:]}"


@dataclass(frozen=True)
class ForecastRow:
    player_id: int
    gameweek: int
    expected_points: float
    granularity: str = "gameweek"
    fixture_id: int | None = None
    expected_minutes: float | None = None
    appearance_probability: float | None = None
    start_probability: float | None = None
    sixty_probability: float | None = None
    no_show_probability: float | None = None
    cameo_probability: float | None = None
    haul_probability: float | None = None
    blank_probability: float | None = None
    points_semantics: str = "unconditional"
    mapping_confidence: str = "exact_season_id"
    validation_status: str = "validated"
    fixture_count: int | None = None
    notes: tuple[str, ...] = ()
    source_probabilities: dict[str, float] = field(default_factory=dict)
    probability_definitions: dict[str, str] = field(default_factory=dict)
    components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastBatch:
    source: str
    source_version: str
    season: str
    retrieved_at: str
    computed_at: str | None
    deadline: str
    rows: tuple[ForecastRow, ...]
    attribution: str
    redistribution_allowed: bool = False
    source_url: str | None = None
    raw_sha256: str | None = None
    schema_version: int = 1
    notes: tuple[str, ...] = ()

    def available_at(self, cutoff: datetime, *, max_age_hours: float = 36) -> bool:
        """Retrieval establishes availability; source publication cannot backdate it."""
        cutoff = timestamp(cutoff)
        retrieved = timestamp(self.retrieved_at)
        computed = timestamp(self.computed_at) if self.computed_at else retrieved
        return (
            computed <= retrieved <= cutoff < timestamp(self.deadline)
            and cutoff - computed <= timedelta(hours=max_age_hours)
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _context(payload: dict, bootstrap: dict, retrieved_at: datetime, computed_key: str) -> dict:
    retrieved = timestamp(retrieved_at)
    deadline = timestamp(payload["deadline"])
    computed = timestamp(payload[computed_key])
    if not computed <= retrieved < deadline:
        raise ValueError("Source computation/retrieval must precede its deadline")
    gw = positive_id(payload["gameweek"])
    events = {int(e["id"]): e for e in bootstrap["events"]}
    if gw not in events or deadline != timestamp(events[gw]["deadline_time"]):
        raise ValueError("Source deadline/gameweek disagrees with official schedule")
    return {
        "season": season_of(bootstrap), "retrieved_at": retrieved.isoformat(),
        "computed_at": computed.isoformat(), "deadline": deadline.isoformat(),
        "raw_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
    }


def _players(bootstrap: dict) -> dict[int, dict]:
    result = {positive_id(p["id"]): p for p in bootstrap["elements"]}
    if len(result) != len(bootstrap["elements"]):
        raise ValueError("Duplicate official player IDs")
    return result


def _validate(batch: ForecastBatch, bootstrap: dict, fixtures: list[dict] | None = None) -> ForecastBatch:
    players = _players(bootstrap)
    if batch.season != season_of(bootstrap) or not batch.rows:
        raise ValueError("Season mismatch or empty forecast")
    seen = set()
    fixture_index = {int(f["id"]): f for f in fixtures or []}
    for row in batch.rows:
        key = (row.player_id, row.gameweek, row.fixture_id)
        if key in seen or row.player_id not in players:
            raise ValueError("Duplicate forecast or unknown season/player ID")
        seen.add(key)
        positive_id(row.player_id)
        if not 1 <= row.gameweek <= 38 or row.points_semantics != "unconditional":
            raise ValueError("Unnormalised points or invalid gameweek")
        number(row.expected_points)
        if row.granularity not in {"gameweek", "fixture"} or (row.fixture_id is None) != (row.granularity == "gameweek"):
            raise ValueError("Granularity and fixture identifier disagree")
        count = row.fixture_count if row.fixture_count is not None else (1 if row.granularity == "fixture" else None)
        if row.fixture_count is not None and (row.fixture_count < 0 or int(row.fixture_count) != row.fixture_count):
            raise ValueError("Invalid fixture count")
        if row.expected_minutes is not None:
            minutes = number(row.expected_minutes)
            if minutes < 0 or count is not None and minutes > 90 * count + 0.1:
                raise ValueError("Expected minutes exceed declared fixtures")
        for name in ("appearance_probability", "start_probability", "sixty_probability", "no_show_probability", "cameo_probability", "haul_probability", "blank_probability"):
            probability(getattr(row, name))
        if set(row.source_probabilities) != set(row.probability_definitions):
            raise ValueError("Source probabilities require explicit definitions")
        for value in row.source_probabilities.values():
            probability(value)
        for value in row.components.values():
            number(value)
        if row.appearance_probability is not None and row.no_show_probability is not None and abs(row.appearance_probability + row.no_show_probability - 1) > 0.005:
            raise ValueError("Appearance/no-show probabilities contradict")
        if row.start_probability is not None and row.appearance_probability is not None and row.start_probability > row.appearance_probability + 0.005:
            raise ValueError("Start probability exceeds appearance")
        if row.sixty_probability is not None and row.appearance_probability is not None and row.sixty_probability > row.appearance_probability + 0.005:
            raise ValueError("60-minute probability exceeds appearance")
        if row.start_probability is not None and row.cameo_probability is not None and row.appearance_probability is not None and abs(row.start_probability + row.cameo_probability - row.appearance_probability) > 0.005:
            raise ValueError("Minutes-state probabilities contradict")
        if fixtures is not None and row.fixture_id is not None:
            fixture = fixture_index.get(row.fixture_id)
            if not fixture or fixture.get("event") != row.gameweek or players[row.player_id]["team"] not in {fixture["team_h"], fixture["team_a"]}:
                raise ValueError("Fixture/player/gameweek mapping disagrees with official schedule")
    return batch


def validate_batch(batch: ForecastBatch, bootstrap: dict, fixtures: list[dict] | None = None) -> ForecastBatch:
    """Validate a deserialised capture against its original official roster/schedule."""
    if batch.schema_version != 1:
        raise ValueError("Unknown external forecast schema")
    retrieved = timestamp(batch.retrieved_at)
    if retrieved >= timestamp(batch.deadline) or batch.computed_at and timestamp(batch.computed_at) > retrieved:
        raise ValueError("External source violates pre-deadline availability")
    events = {int(e["id"]): e for e in bootstrap["events"]}
    if any(r.gameweek not in events or timestamp(events[r.gameweek]["deadline_time"]) < timestamp(batch.deadline) for r in batch.rows):
        raise ValueError("External row precedes declared deadline")
    return _validate(batch, bootstrap, fixtures)


def batch_from_dict(data: dict) -> ForecastBatch:
    data = dict(data)
    data["rows"] = tuple(ForecastRow(**{**r, "notes": tuple(r.get("notes", ()))}) for r in data["rows"])
    data["notes"] = tuple(data.get("notes", ()))
    if data.get("schema_version") != 1:
        raise ValueError("Unknown external forecast schema")
    return ForecastBatch(**data)


def parse_fpl_cat(payload: dict, bootstrap: dict, retrieved_at: datetime, fixtures: list[dict] | None = None) -> ForecastBatch:
    context = _context(payload, bootstrap, retrieved_at, "computed_at")
    rows = []
    players = _players(bootstrap)
    teams = {int(t["id"]): t["short_name"] for t in bootstrap["teams"]}
    for item in payload["players"]:
        pid = positive_id(item["id"])
        if pid not in players or item["team"] != teams[int(players[pid]["team"])]:
            raise ValueError("FPL Cat season/player/team mapping mismatch")
        if item.get("name") and players[pid].get("web_name") and _normal_name(item["name"]) != _normal_name(players[pid]["web_name"]):
            raise ValueError("FPL Cat player ID/name mapping mismatch")
        count = int(item["fixtures"])
        if number(item["fixtures"]) != count or count < 0:
            raise ValueError("Invalid FPL Cat fixture count")
        # Documented play_chance is a SUM across DGW legs. Other exported event
        # probabilities lack DGW aggregation definitions, so retain no DGW probability.
        play = probability(item.get("play_chance")) if count <= 1 else None
        rows.append(ForecastRow(
            pid, int(payload["gameweek"]), number(item["xp"]),
            expected_minutes=number(item.get("expected_minutes"), optional=True),
            appearance_probability=play,
            no_show_probability=1 - play if play is not None else None,
            haul_probability=probability(item.get("p_haul")) if count <= 1 else None,
            blank_probability=probability(item.get("p_blank")) if count <= 1 else None,
            fixture_count=count,
            notes=(f"DGW summed play_chance={number(item.get('play_chance'), optional=True)} is not a gameweek probability; probabilities omitted",) if count > 1 else ("Blank means <=3 points including no appearance; haul means >=10 points",),
            source_probabilities={"p_return": probability(item["p_return"])} if count <= 1 and item.get("p_return") is not None else {},
            probability_definitions={"p_return": "Gameweek probability of a goal or assist; single fixture only"} if count <= 1 and item.get("p_return") is not None else {},
        ))
    return _validate(ForecastBatch(
        "fpl_cat", "api/v1", rows=tuple(rows), attribution=ATTRIBUTIONS["fpl_cat"],
        redistribution_allowed=True, source_url=SOURCE_URLS["fpl_cat"], **context,
        notes=("Personal/research use with attribution; paid redistribution requires written permission",),
    ), bootstrap, fixtures)


def parse_sideline_quant(payload: dict, bootstrap: dict, retrieved_at: datetime, fixtures: list[dict] | None = None) -> ForecastBatch:
    context = _context(payload, bootstrap, retrieved_at, "generated_at")
    if str(payload["schema_version"]) != "1.0":
        raise ValueError("Unsupported Sideline Quant schema version")
    rows = []
    players = _players(bootstrap)
    teams = {int(t["id"]): t for t in bootstrap["teams"]}
    definitions = {
        "p_start60": "Fixture P(start and minutes>=60), not P(minutes>=60)",
        "p_start_sub": "Fixture P(start and minutes<60)",
        "p_cs": "Fixture P(clean sheet | minutes>=60)",
        "p_goal": "Fixture P(at least one goal)",
        "p_goals_2plus": "Fixture P(at least two goals)",
        "p_assist": "Fixture P(at least one FPL assist)",
        "p_return": "Fixture P(goal or assist)",
        "p_defcon": "Fixture P(defensive contribution threshold)",
        "p_card": "Fixture P(at least one yellow/red card)",
        "p_ge_6": "Gameweek P(points>=6), repeated across fixture rows; do not sum",
        "p_ge_10": "Gameweek P(points>=10), repeated across fixture rows; do not sum",
    }
    for item in payload["rows"]:
        if positive_id(item["gameweek"]) != positive_id(payload["gameweek"]):
            raise ValueError("Sideline row gameweek differs from payload gameweek")
        pid = positive_id(item["player_id"])
        if pid not in players:
            raise ValueError("Unknown Sideline season/player ID")
        official_team = teams[int(players[pid]["team"])]
        if item.get("club") and _normal_name(item["club"]) not in {_normal_name(official_team.get("name", "")), _normal_name(official_team["short_name"])}:
            raise ValueError("Sideline player ID/club mapping mismatch")
        no_show = probability(item.get("p_unused"))
        start = probability(item.get("p_start"))
        start60 = probability(item.get("p_start60"))
        # p_start60 is a subset, not full P(minutes>=60): a long substitute
        # appearance can also qualify. Preserve this precise distinction in notes.
        rows.append(ForecastRow(
            positive_id(item["player_id"]), positive_id(item["gameweek"]), number(item["xP"]),
            granularity="fixture", fixture_id=positive_id(item["fixture_id"]),
            expected_minutes=number(item.get("e_mins"), optional=True),
            appearance_probability=1 - no_show if no_show is not None else None,
            start_probability=start, no_show_probability=no_show,
            cameo_probability=probability(item.get("p_cameo")), fixture_count=1,
            notes=(f"P(start and 60+ minutes)={start60}" if start60 is not None else "Start60 unavailable",),
            source_probabilities={k: probability(item[k]) for k in definitions if item.get(k) is not None},
            probability_definitions={k: definition for k, definition in definitions.items() if item.get(k) is not None},
            components={k: number(item[k]) for k in ("xp_appearance", "xp_goals", "xp_assists", "xp_clean_sheet", "xp_conceded", "xp_saves", "xp_penalty_save", "xp_penalty_miss", "xp_defcon", "xp_cards", "xp_own_goal", "bonus_mean") if item.get(k) is not None},
        ))
        if start60 is not None and start is not None and start60 > start + 0.005:
            raise ValueError("Start60 probability exceeds start")
    return _validate(ForecastBatch(
        "sideline_quant", str(payload["schema_version"]), rows=tuple(rows),
        attribution=ATTRIBUTIONS["sideline_quant"], redistribution_allowed=True,
        source_url=SOURCE_URLS["sideline_quant"], **context,
        notes=("CC BY 4.0 covers source-model forecasts, not underlying FPL fields",),
    ), bootstrap, fixtures)


def parse_official_fpl(bootstrap: dict, retrieved_at: datetime) -> ForecastBatch:
    upcoming = [e for e in bootstrap["events"] if e.get("is_next")]
    if len(upcoming) != 1:
        raise ValueError("Official next gameweek unavailable")
    event = upcoming[0]
    retrieved = timestamp(retrieved_at)
    deadline = timestamp(event["deadline_time"])
    if retrieved >= deadline:
        raise ValueError("Official forecast retrieved after deadline")
    rows = tuple(ForecastRow(
        positive_id(p["id"]), int(event["id"]), number(p["ep_next"]),
        notes=("Official ep_next retained as supplied; no additional availability multiplier",),
    ) for p in bootstrap["elements"] if p.get("ep_next") not in (None, ""))
    return _validate(ForecastBatch(
        "official_fpl", "bootstrap-static/ep_next", season_of(bootstrap),
        retrieved.isoformat(), None, deadline.isoformat(), rows,
        ATTRIBUTIONS["official_fpl"], False, SOURCE_URLS["official_fpl"],
        hashlib.sha256(canonical(bootstrap)).hexdigest(),
        notes=("Computation timestamp and algorithm undocumented; retrieval is first availability evidence",),
    ), bootstrap)


def _normal_name(value: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value).casefold() if not unicodedata.combining(c)).split())


def map_player(record: dict, bootstrap: dict, season: str, exceptions: dict[str, int] | None = None) -> tuple[int, str]:
    """ID first, otherwise exact season+club+full-name; ambiguous names fail closed."""
    if season != season_of(bootstrap):
        raise ValueError("Player mapping season mismatch")
    players = _players(bootstrap)
    teams = {str(t["id"]): int(t["id"]) for t in bootstrap["teams"]}
    teams.update({str(t["short_name"]).casefold(): int(t["id"]) for t in bootstrap["teams"]})
    teams.update({str(t.get("name", "")).casefold(): int(t["id"]) for t in bootstrap["teams"]})
    team = teams.get(str(record.get("team", "")).casefold())
    if record.get("player_id") not in (None, ""):
        pid = positive_id(record["player_id"])
        if pid not in players or record.get("team") not in (None, "") and team != int(players[pid]["team"]):
            raise ValueError("Unknown season/player ID")
        return pid, "exact_season_id"
    key = f"{season}:{record.get('team', '')}:{record.get('name', '')}"
    if key in (exceptions or {}):
        pid = positive_id(exceptions[key])
        if pid not in players:
            raise ValueError("Mapping exception points to unknown player")
        return pid, "explicit_season_exception"
    name = _normal_name(record.get("name", ""))
    candidates = [pid for pid, p in players.items() if team == int(p["team"]) and name == _normal_name(f"{p.get('first_name', '')} {p.get('second_name', '')}")]
    if not name or len(candidates) != 1:
        raise ValueError("Ambiguous/unmapped player; supply a season-specific ID or explicit exception")
    return candidates[0], "exact_full_name_and_club"


def parse_manual_csv(body: str, metadata: dict, bootstrap: dict, retrieved_at: datetime, fixtures: list[dict] | None = None, exceptions: dict[str, int] | None = None) -> ForecastBatch:
    """Explicit long-form import, never guesses wide-export or probability semantics.

    Source exports (including FPL Form X_with_prob / X_pts_no_prob) must be
    converted to this auditable contract. Conditional rows require fixture-level
    appearance_probability and are normalised once. GW conditional rows are refused.
    """
    required = {"source", "season", "deadline", "computed_at", "points_semantics", "granularity"}
    if not required <= metadata.keys() or metadata["source"] not in {"fpl_form", "fpl_review", "manual"}:
        raise ValueError("Explicit source, season, timestamps, granularity and point semantics required")
    if metadata["season"] != season_of(bootstrap):
        raise ValueError("Manual forecast season mismatch")
    retrieved = timestamp(retrieved_at)
    computed = timestamp(metadata["computed_at"]) if metadata["computed_at"] else None
    deadline = timestamp(metadata["deadline"])
    if retrieved >= deadline or computed is not None and computed > retrieved:
        raise ValueError("Manual import cannot establish pre-deadline availability")
    granularity = metadata["granularity"]
    semantics = metadata["points_semantics"]
    if semantics not in {"unconditional", "conditional_appearance"} or semantics == "conditional_appearance" and granularity != "fixture":
        raise ValueError("Conditional GW export is ambiguous, especially for double gameweeks")
    if metadata.get("minutes_semantics", "unconditional") != "unconditional":
        raise ValueError("Manual expected_minutes must already be unconditional")
    rows = []
    for item in csv.DictReader(io.StringIO(body)):
        pid, confidence = map_player(item, bootstrap, metadata["season"], exceptions)
        xp = number(item["expected_points"])
        appear = probability(item.get("appearance_probability"))
        if semantics == "conditional_appearance":
            if appear is None:
                raise ValueError("Conditional fixture points require appearance probability")
            xp *= appear
        rows.append(ForecastRow(
            pid, positive_id(item["gameweek"]), xp, granularity=granularity,
            fixture_id=positive_id(item["fixture_id"]) if granularity == "fixture" else None,
            expected_minutes=number(item.get("expected_minutes"), optional=True),
            appearance_probability=appear,
            start_probability=probability(item.get("start_probability")),
            sixty_probability=probability(item.get("sixty_probability")),
            no_show_probability=1 - appear if appear is not None else None,
            mapping_confidence=confidence,
            notes=(f"Imported points semantics: {semantics}; normalised exactly once",),
        ))
    events = {int(e["id"]): e for e in bootstrap["events"]}
    if any(r.gameweek not in events or timestamp(events[r.gameweek]["deadline_time"]) < deadline for r in rows):
        raise ValueError("Imported row deadline is earlier than declared source deadline")
    return _validate(ForecastBatch(
        metadata["source"], metadata.get("source_version", "manual-v1"), metadata["season"],
        retrieved.isoformat(), computed.isoformat() if computed else None, deadline.isoformat(),
        tuple(rows), ATTRIBUTIONS.get(metadata["source"], "Personal manual import"), False,
        raw_sha256=hashlib.sha256(body.encode()).hexdigest(),
        notes=("Local personal-use data; excluded from all public archive bundles",),
    ), bootstrap, fixtures)


def gameweek_rows(batch: ForecastBatch, gameweek: int, fixtures: list[dict] | None = None, bootstrap: dict | None = None) -> dict[int, ForecastRow]:
    """Sum fixture means only; never sum probabilities or invent fixture draws.

    If a fixture schedule and roster are supplied, incomplete DGW coverage is
    excluded. Without that evidence multi-fixture rows are still summed, but no
    whole-GW probability/variance is fabricated.
    """
    grouped: dict[int, list[ForecastRow]] = {}
    for row in batch.rows:
        if row.gameweek == gameweek:
            grouped.setdefault(row.player_id, []).append(row)
    result = {}
    players = _players(bootstrap) if bootstrap is not None else {}
    for pid, rows in grouped.items():
        if any(r.granularity != rows[0].granularity for r in rows):
            raise ValueError("Mixed fixture/GW granularity for the same player")
        if rows[0].granularity == "gameweek":
            if len(rows) != 1:
                raise ValueError("Duplicate gameweek forecast")
            if fixtures is not None and bootstrap is not None:
                scheduled = [f for f in fixtures if f.get("event") == gameweek and players[pid]["team"] in {f["team_h"], f["team_a"]}]
                if rows[0].fixture_count is not None and len(scheduled) != rows[0].fixture_count:
                    continue
            result[pid] = rows[0]
            continue
        if fixtures is not None and bootstrap is not None:
            expected = {int(f["id"]) for f in fixtures if f.get("event") == gameweek and players[pid]["team"] in {f["team_h"], f["team_a"]}}
            if {r.fixture_id for r in rows} != expected:
                continue
        if len(rows) == 1:
            result[pid] = ForecastRow(**{**asdict(rows[0]), "granularity": "gameweek", "fixture_id": None})
        else:
            result[pid] = ForecastRow(
                pid, gameweek, sum(r.expected_points for r in rows),
                expected_minutes=sum(r.expected_minutes for r in rows) if all(r.expected_minutes is not None for r in rows) else None,
                fixture_count=len(rows), notes=("Summed fixture expectations; gameweek probabilities unknown without joint outcomes",),
            )
    return result


def combine_rows(sources: dict[str, dict[int, ForecastRow]], *, method: str = "mean", weights: dict[str, float] | None = None, require_all: bool = False) -> dict[int, dict]:
    """Point-only experimental ensemble. Missing means are absent, never zero."""
    if method not in {"mean", "median", "weighted"} or not sources:
        if not sources:
            return {}
        raise ValueError("Unknown ensemble method")
    if method == "weighted" and (not weights or any(not math.isfinite(v) or v < 0 for v in weights.values()) or set(weights) != set(sources) or sum(weights.values()) <= 0):
        raise ValueError("Nonnegative weights required for exactly these sources")
    keys = set.intersection(*(set(rows) for rows in sources.values())) if require_all else set.union(*(set(rows) for rows in sources.values()))
    result = {}
    for pid in keys:
        members = {s: rows[pid].expected_points for s, rows in sources.items() if pid in rows}
        if method == "weighted":
            mass = sum(weights[s] for s in members)
            if mass <= 0:
                continue
            value = sum(weights[s] * value for s, value in members.items()) / mass
        else:
            value = statistics.mean(members.values()) if method == "mean" else statistics.median(members.values())
        result[pid] = {"expected_points": value, "members": members, "coverage": len(members) / len(sources), "method": method, "experimental": True, "distribution_available": False}
    return result


def fit_past_weights(observations: Iterable[dict], sources: tuple[str, ...], cutoff: datetime, *, shrinkage: float = 25) -> dict[str, float]:
    """Training-only inverse-MSE weights shrunk to equal, not a claim of superiority.

    Uses identical complete rows and settled outcomes available strictly before
    the forecast cutoff. Correlated sources and sparse data favour equal weights.
    A more complex stack must beat this and equal-weight blending prospectively.
    """
    if not sources or len(set(sources)) != len(sources) or shrinkage < 0:
        raise ValueError("Invalid ensemble configuration")
    cutoff = timestamp(cutoff)
    errors = {s: [] for s in sources}
    deadlines = set()
    for row in observations:
        deadline, captured, outcome = (timestamp(row[k]) for k in ("deadline", "captured_at", "outcome_available_at"))
        if not captured < deadline < outcome < cutoff:
            raise ValueError("Ensemble training row violates chronological availability")
        if not all(s in row["predictions"] and row["predictions"][s] is not None for s in sources):
            continue
        deadlines.add(deadline)
        actual = number(row["actual"])
        for s in sources:
            errors[s].append((number(row["predictions"][s]) - actual) ** 2)
    n = len(errors[sources[0]])
    if not n:
        return {s: 1 / len(sources) for s in sources}
    inverse = {s: 1 / max(statistics.mean(errors[s]), 0.01) for s in sources}
    total = sum(inverse.values())
    # Many players at one deadline do not create independent evidence about
    # model performance; shrinkage counts deadlines, not player observations.
    effective = len(deadlines) / (len(deadlines) + shrinkage)
    return {s: effective * inverse[s] / total + (1 - effective) / len(sources) for s in sources}


def save_batch(batch: ForecastBatch, directory: Path) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", batch.source) or not re.fullmatch(r"20\d{2}-\d{2}", batch.season):
        raise ValueError("Unsafe external source or season identifier")
    body = canonical(batch.to_dict())
    suffix = hashlib.sha256(body).hexdigest()[:12]
    stamp = timestamp(batch.retrieved_at).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{stamp}-{batch.source}-{suffix}.json.gz"
    immutable(path, gzip.compress(body, mtime=0))
    # Mutable pointers locate immutable evidence; they are not publication proof.
    # One tiny pointer per source/GW avoids inflating hundreds of captures in an
    # authenticated request, especially when later GWs have no source coverage.
    pointer = {"filename": path.name, "sha256": hashlib.sha256(body).hexdigest(), "retrieved_at": batch.retrieved_at}
    for gw in {row.gameweek for row in batch.rows}:
        index = directory / f"index-{batch.season}-GW{gw:02}-{batch.source}.json"
        if index.exists():
            try:
                if timestamp(json.loads(index.read_bytes())["retrieved_at"]) > timestamp(batch.retrieved_at):
                    continue
            except (ValueError, KeyError):
                pass
        fd, temporary = tempfile.mkstemp(prefix=".external-index-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical(pointer))
            os.replace(temporary, index)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return path


def read_batch(path: Path) -> ForecastBatch:
    with gzip.open(path, "rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("External capture exceeds 16 MiB")
    return batch_from_dict(json.loads(raw))


def load_available_forecasts(directory: Path, season: str, gameweek: int, cutoff: datetime, *, max_age_hours: float = 36, bootstrap: dict | None = None, fixtures: list[dict] | None = None) -> dict:
    """Read existing local captures only, bounded in memory; no request-time fetch."""
    latest: dict[str, ForecastBatch] = {}
    observed: dict[str, ForecastBatch] = {}
    errors = []
    indexes = sorted(directory.glob("index-*.json"), reverse=True)[:512]
    paths, future_sources, checksums = [], [], {}
    for index in indexes:
        if not index.name.startswith(f"index-{season}-GW{gameweek:02}-"):
            continue
        try:
            pointer = json.loads(index.read_bytes())
            filename = pointer["filename"]
            if Path(filename).name != filename or not filename.endswith(".json.gz"):
                raise ValueError("Unsafe external index path")
            path = directory / filename
            if path.resolve().parent != directory.resolve():
                raise ValueError("External index escapes cache directory")
            if timestamp(pointer["retrieved_at"]) > timestamp(cutoff):
                future_sources.append(index.name.removeprefix(f"index-{season}-GW{gameweek:02}-").removesuffix(".json"))
            else:
                paths.append(path)
                checksums[path] = pointer["sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            errors.append(f"Invalid external index: {index.name}")
    # Legacy archives without indexes retain a bounded compatibility scan.
    # For a historical cutoff, indexed captures newer than cutoff trigger a
    # source-filtered fallback; filenames are routing hints, timestamps rechecked.
    if not indexes or future_sources:
        for path in sorted(directory.glob("*.json.gz"), reverse=True)[:512]:
            if future_sources and not any(f"-{source}-" in path.name for source in future_sources):
                continue
            match = re.match(r"^(\d{8}T\d{12}Z)-", path.name)
            if match:
                try:
                    if datetime.strptime(match[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC) > timestamp(cutoff):
                        continue
                except ValueError:
                    errors.append(f"Invalid external capture filename: {path.name}")
                    continue
            if path not in paths:
                paths.append(path)
    for path in paths:
        try:
            batch = read_batch(path)
            if path in checksums and hashlib.sha256(canonical(batch.to_dict())).hexdigest() != checksums[path]:
                raise ValueError("External indexed capture checksum mismatch")
            if bootstrap is not None:
                validate_batch(batch, bootstrap, fixtures)
            if batch.season != season or not any(r.gameweek == gameweek for r in batch.rows):
                continue
            if timestamp(batch.retrieved_at) <= timestamp(cutoff) < timestamp(batch.deadline) and (batch.source not in observed or timestamp(batch.retrieved_at) > timestamp(observed[batch.source].retrieved_at)):
                observed[batch.source] = batch
            if not batch.available_at(cutoff, max_age_hours=max_age_hours):
                continue
            if batch.source not in latest or timestamp(batch.retrieved_at) > timestamp(latest[batch.source].retrieved_at):
                latest[batch.source] = batch
        except (OSError, ValueError, TypeError, KeyError, EOFError):
            errors.append(f"Invalid external capture: {path.name}")
    rows = {s: gameweek_rows(b, gameweek, fixtures, bootstrap) for s, b in latest.items()}
    eligible = len(bootstrap["elements"]) if bootstrap is not None else None
    status = {}
    for source in set(SOURCE_URLS) | set(observed):
        batch = latest.get(source) or observed.get(source)
        count = len(rows.get(source, {}))
        status[source] = {
            "source": source, "state": "ready" if source in latest else "stale" if batch else "missing",
            "retrieved_at": batch.retrieved_at if batch else None,
            "computed_at": batch.computed_at if batch else None,
            "deadline": batch.deadline if batch else None,
            "coverage_count": count, "eligible_count": eligible,
            "coverage_fraction": count / eligible if eligible else None,
            "attribution": batch.attribution if batch else ATTRIBUTIONS.get(source),
            "redistribution_allowed": may_redistribute(batch) if batch else False,
            "freshness": ("fresh" if batch.computed_at else "retrieval_only") if source in latest else "stale" if batch else "unknown",
            "notes": batch.notes if batch else ("No locally captured forecast available at cutoff",),
        }
    return {
        "sources": rows,
        "status": status,
        "ensemble_mean": combine_rows(rows),
        "ensemble_median": combine_rows(rows, method="median"),
        "errors": errors,
        "fallback": "No fresh pre-deadline external capture; internal model retained" if not latest else None,
    }


def prune_external_cache(directory: Path, *, now: datetime | None = None, keep_days: int = 7, max_files: int = 512, max_bytes: int = 64 * 1024 * 1024, protect: Iterable[Path] = ()) -> dict:
    """Bound local automatic cache copies; never delete personal imports/releases.

    Durable evidence belongs in the separate immutable recording releases. Local
    HTTP/normalized files support the live comparison and are a rolling cache.
    Keep the two newest files of each automatic source even when a source fails.
    """
    if keep_days < 1 or max_files < 6 or max_bytes < 1024:
        raise ValueError("Unsafe external retention configuration")
    root = directory.resolve()
    protected = {path.resolve() for path in protect}
    automatic = {"official_fpl": "bootstrap-static/ep_next", "fpl_cat": "api/v1", "sideline_quant": "1.0"}
    candidates = []
    for path in directory.glob("*.json.gz"):
        # Do not read/delete a manual import merely because its extension matches.
        if not any(f"-{source}-" in path.name for source in automatic) or path.resolve().parent != root:
            continue
        try:
            batch = read_batch(path)
            if automatic.get(batch.source) != batch.source_version:
                continue
            candidates.append((timestamp(batch.retrieved_at), path, batch.source, path.stat().st_size))
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            continue
    candidates.sort(reverse=True)
    for source in automatic:
        protected.update(path.resolve() for _, path, name, _ in [c for c in candidates if c[2] == source][:2])
    cutoff = timestamp(now or datetime.now(UTC)) - timedelta(days=keep_days)
    count, size = len(candidates), sum(item[3] for item in candidates)
    removed = set()
    for retrieved, path, _, length in reversed(candidates):
        if path.resolve() in protected or path.resolve().parent != root:
            continue
        if retrieved >= cutoff and count <= max_files and size <= max_bytes:
            continue
        path.unlink()
        removed.add(path.name)
        count -= 1
        size -= length
    for index in directory.glob("index-*.json"):
        if index.resolve().parent != root:
            continue
        try:
            if json.loads(index.read_bytes())["filename"] in removed:
                index.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {"removed_files": len(removed), "remaining_automatic_files": count, "remaining_automatic_bytes": size, "personal_imports_pruned": False}


class ForecastHTTPClient:
    """Small bounded conditional-GET cache with retries and source-friendly pacing."""

    def __init__(self, directory: Path, *, opener: Callable | None = None, sleeper: Callable = time.sleep, now: Callable = lambda: datetime.now(UTC)) -> None:
        self.directory, self.opener, self.sleeper, self.now = directory, opener or urlopen, sleeper, now
        # Match the existing FPL client: native Windows curl avoids a process-level
        # OpenSSL/Uplink crash in the embedded Windows Python runtime.
        self.curl = shutil.which("curl.exe") if os.name == "nt" and opener is None else None

    def _request(self, url: str, headers: dict) -> tuple[bytes, str | None]:
        if self.curl:
            with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
                header_path, body_path = Path(temporary) / "headers", Path(temporary) / "body"
                args = [self.curl, "--silent", "--show-error", "--location", "--max-time", "25", "--max-filesize", str(8 * 1024 * 1024), "--dump-header", str(header_path), "--output", str(body_path), "--write-out", "%{http_code}"]
                for name, value in headers.items():
                    args.extend(["--header", f"{name}: {value}"])
                args.append(url)
                result = subprocess.run(args, capture_output=True, timeout=30, check=False)
                if result.returncode:
                    raise URLError("Public forecast curl transport failed")
                status = int(result.stdout)
                if status != 200:
                    raise HTTPError(url, status, "Forecast HTTP response", {}, None)
                header_lines = header_path.read_text().splitlines()
                etags = [line.split(":", 1)[1].strip() for line in header_lines if line.lower().startswith("etag:")]
                return body_path.read_bytes(), etags[-1] if etags else None
        with self.opener(Request(url, headers=headers), timeout=25) as response:
            return response.read(8 * 1024 * 1024 + 1), response.headers.get("ETag")

    def get_json(self, url: str, *, min_age_seconds: int = 300) -> tuple[dict, datetime]:
        if url not in SOURCE_URLS.values() and url != "https://fantasy.premierleague.com/api/fixtures/":
            raise ValueError("Forecast fetch URL is not an approved public endpoint")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        cached = json.loads(path.read_text()) if path.exists() else None
        now = timestamp(self.now())
        if cached and (now - timestamp(cached["retrieved_at"])).total_seconds() < min_age_seconds:
            return cached["payload"], timestamp(cached["retrieved_at"])
        headers = {"User-Agent": "FPL-Scenario-Analyser/0.3 (+https://github.com/MohilGarg/FPL-Scenario-Analyser)", "Accept": "application/json"}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        for attempt in range(3):
            try:
                raw, etag = self._request(url, headers)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("External payload exceeds 8 MiB")
                payload = json.loads(raw)
                retrieved = timestamp(self.now())
                path.write_bytes(canonical({"payload": payload, "etag": etag, "retrieved_at": retrieved.isoformat()}))
                return payload, retrieved
            except HTTPError as exc:
                if exc.code == 304 and cached:
                    # Keep earliest retrieval evidence for unchanged cached content.
                    return cached["payload"], timestamp(cached["retrieved_at"])
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
            except (URLError, TimeoutError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
            self.sleeper((1, 3)[attempt])
        raise AssertionError("Unreachable")


def collect_sources(bootstrap: dict | None, directory: Path, *, now: datetime | None = None, client: ForecastHTTPClient | None = None, fixtures: list[dict] | None = None) -> dict:
    """Best-effort scheduled collection. Failures do not erase prior captures."""
    client = client or ForecastHTTPClient(directory / "http-cache")
    status = {}
    if bootstrap is None:
        bootstrap, official_retrieved = client.get_json(SOURCE_URLS["official_fpl"])
    else:
        official_retrieved = timestamp(now or datetime.now(UTC))
    if fixtures is None:
        fixtures, _ = client.get_json("https://fantasy.premierleague.com/api/fixtures/")
    for source, parser in (("official_fpl", parse_official_fpl), ("fpl_cat", parse_fpl_cat), ("sideline_quant", parse_sideline_quant)):
        try:
            if source == "official_fpl":
                batch = parser(bootstrap, official_retrieved)
            else:
                payload, retrieved = client.get_json(SOURCE_URLS[source])
                batch = parser(payload, bootstrap, retrieved, fixtures)
            path = save_batch(batch, directory)
            status[source] = {"state": "ready", "path": str(path), "rows": len(batch.rows), "retrieved_at": batch.retrieved_at}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Deliberately omit payload/body and news from logs.
            status[source] = {"state": "unavailable", "error": f"{type(exc).__name__}: schema, availability or transport failure"}
    try:
        prune_external_cache(directory, protect=(Path(s["path"]) for s in status.values() if s["state"] == "ready"))
    except OSError:
        for details in status.values():
            details["retention_warning"] = "Local automatic cache pruning unavailable"
    return status
