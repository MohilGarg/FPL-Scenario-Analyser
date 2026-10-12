"""Strict feature/target/quality joins and chronological experiment populations."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ml.dataset import FEATURE_COLUMNS, POSITIONS, timestamp
from ml.deadlines import VERIFIED, deadline_lock, reconstruct_deadlines
from ml.readiness import CORE_FEATURES
from ml.sources import digest


@dataclass
class Population:
    x: np.ndarray
    y: np.ndarray
    season: np.ndarray
    gameweek: np.ndarray
    player_key: np.ndarray
    minutes: np.ndarray
    position: np.ndarray
    exclusions: dict
    feature_names: tuple[str, ...] = FEATURE_COLUMNS


def table_keys(table: pa.Table) -> list[tuple[str, int]]:
    return list(zip(table["player_key"].to_pylist(), table["gameweek"].to_pylist()))


def candidate_population(features: pa.Table, outcomes: pa.Table, quality: pa.Table,
                         verified: dict, official_positions: dict) -> Population:
    keys = table_keys(features)
    if len(set(keys)) != len(keys) or table_keys(outcomes) != keys or table_keys(quality) != keys:
        raise ValueError("Feature/outcome/quality keys must be unique and identically ordered")
    if any(c not in features.column_names for c in FEATURE_COLUMNS):
        raise ValueError("Missing approved feature columns")
    x = np.column_stack([features[c].cast(pa.float64()).to_numpy(zero_copy_only=False)
                         for c in FEATURE_COLUMNS]).astype(np.float64)
    if np.isinf(x).any():
        raise ValueError("Infinite feature values")
    seasons = np.asarray(features["season"].to_pylist())
    gws = np.asarray(features["gameweek"].to_pylist(), dtype=int)
    cutoffs = features["feature_cutoff_utc"].cast(pa.timestamp("us", tz="+00:00")).to_pylist()
    proven = np.array([verified.get((s, int(g))) == d and d is not None
                       for s, g, d in zip(seasons, gws, cutoffs)], dtype=bool)
    history = x[:, FEATURE_COLUMNS.index("pre_gw_history_fixtures")]
    core = x[:, [FEATURE_COLUMNS.index(c) for c in CORE_FEATURES]]
    valid_history = (history > 0) & np.isfinite(core).all(axis=1)
    quality_ok = np.asarray(quality["evaluation_eligible"].to_pylist(), dtype=bool)
    target_ok = np.asarray(outcomes["target_complete"].to_pylist(), dtype=bool)
    mask = proven & valid_history & quality_ok & target_ok
    y = outcomes["target_points"].cast(pa.float64()).to_numpy(zero_copy_only=False)
    minutes = outcomes["observed_minutes"].cast(pa.float64()).to_numpy(zero_copy_only=False)
    if not np.isfinite(y[mask]).all() or not np.isfinite(minutes[mask]).all():
        raise ValueError("Candidate target/observed minutes must be known")
    positions = []
    for s, element, code, reported in zip(seasons, outcomes["element"].to_pylist(),
                                         outcomes["player_code"].to_pylist(), outcomes["position"].to_pylist()):
        recorded = official_positions.get((s, element, code))
        positions.append(recorded if recorded == reported and recorded in {"GK", "DEF", "MID", "FWD"}
                         else "UNKNOWN")
    exclusions = {}
    for s in sorted(set(seasons)):
        subset = seasons == s
        exclusions[s] = {
            "source_player_gws": int(subset.sum()), "candidates": int((mask & subset).sum()),
            "no_eligible_history_or_core_missing": int((subset & ~valid_history).sum()),
            "quality_quarantined": int((subset & ~quality_ok).sum()),
            "quarantine_history_overlap": int((subset & ~quality_ok & ~valid_history).sum()),
            "deadline_unverified": int((subset & ~proven).sum()),
            "target_incomplete": int((subset & ~target_ok).sum()),
            "unknown_position_candidates": int((mask & subset & (np.asarray(positions) == "UNKNOWN")).sum()),
        }
    return Population(x[mask], y[mask], seasons[mask], gws[mask],
                      np.asarray(features["player_key"].to_pylist())[mask], minutes[mask],
                      np.asarray(positions)[mask], exclusions)


def load_population(data_dir: Path, seasons: list[str], expected: dict[str, int]) -> Population:
    if not seasons or len(set(seasons)) != len(seasons):
        raise ValueError("Requested seasons must be nonempty and distinct")
    declared = json.loads((data_dir / "feature_columns.json").read_text(encoding="utf-8"))
    if declared != list(FEATURE_COLUMNS):
        raise ValueError("Prepared feature list differs from approved code list")
    metadata = json.loads((data_dir / "preparation_metadata.json").read_text(encoding="utf-8"))
    if not metadata["verified_archive"]:
        raise ValueError("Experiments require the verified archive calendar")
    deadline_file = Path(metadata["deadline_file"])
    if digest(deadline_file.read_bytes()) != metadata["deadline_sha256"]:
        raise ValueError("Deadline checksum mismatch")
    raw_dir = data_dir.parent / "raw"
    proof = reconstruct_deadlines(raw_dir, seasons)
    verified = {(d["season"], d["gameweek"]): timestamp(d["deadline_utc"]) for d in proof
                if d["verification_status"] == VERIFIED}
    positions = {}
    for capture in deadline_lock()["captures"]:
        if capture["season"] in seasons:
            # Already checksum-verified by reconstruction. Used ONLY for retrospective
            # evaluation labels, never X or training population selection.
            path = raw_dir / "deadlines" / capture["season"] / "bootstrap-static.json.gz"
            payload = json.loads(gzip.decompress(path.read_bytes()))
            for e in payload["elements"]:
                positions[capture["season"], int(e["id"]), int(e["code"])] = POSITIONS.get(str(e["element_type"]))
    filters = [("season", "in", seasons)]
    features = pq.read_table(data_dir / "features.parquet", filters=filters,
                             columns=["season", "player_key", "gameweek", "feature_cutoff_utc", *FEATURE_COLUMNS])
    outcomes = pq.read_table(data_dir / "player_gameweeks.parquet", filters=filters,
                             columns=["player_key", "gameweek", "target_points", "target_complete",
                                      "observed_minutes", "position", "element", "player_code"])
    quality = pq.read_table(data_dir / "quality.parquet", filters=filters,
                            columns=["player_key", "gameweek", "evaluation_eligible"])
    population = candidate_population(features, outcomes, quality, verified, positions)
    if set(population.season) != set(seasons):
        raise ValueError("Missing requested season population")
    for s, counts in population.exclusions.items():
        if counts["candidates"] != expected[s]:
            raise ValueError(f"Candidate count changed for {s}: {counts['candidates']} != {expected[s]}")
    return population


def subset(population: Population, season: str) -> Population:
    mask = population.season == season
    if not mask.any():
        raise ValueError(f"Insufficient/no observations: {season}")
    return Population(*(getattr(population, c)[mask] for c in
                        ("x", "y", "season", "gameweek", "player_key", "minutes", "position")),
                      {season: population.exclusions[season]}, population.feature_names)
