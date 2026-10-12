"""Fast validation of prepared Parquet, including original-token preservation."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .dataset import FEATURE_COLUMNS, feature_values, gameweek_dataset, load_deadlines, read_csv, strict_sum
from .deadlines import reconstruct_deadlines
from .readiness import quality_metadata
from .sources import ROOT, digest, verify_cache


def validate(raw_dir: Path, output_dir: Path) -> dict[str, int]:
    import pyarrow.parquet as pq

    matches = pq.read_table(output_dir / "match_records.parquet").to_pylist()
    gameweeks = pq.read_table(output_dir / "player_gameweeks.parquet").to_pylist()
    feature_file = pq.ParquetFile(output_dir / "features.parquet")
    seasons = sorted({r["season"] for r in gameweeks})
    verify_cache(raw_dir, seasons)
    metadata = json.loads((output_dir / "preparation_metadata.json").read_text(encoding="utf-8"))
    deadline_file = Path(metadata["deadline_file"])
    if digest(deadline_file.read_bytes()) != metadata["deadline_sha256"]:
        raise ValueError("Prepared deadline calendar checksum mismatch")
    if metadata["verified_archive"]:
        proven = reconstruct_deadlines(raw_dir, seasons)
        if proven != metadata["deadline_records"] or read_csv(deadline_file).rows != [
            {k: str(v) for k, v in row.items()} for row in proven
        ]:
            raise ValueError("Deadline calendar/provenance differs from archived API proof")
    deadlines = load_deadlines(deadline_file)
    keys = [(m["season"], m["element"], m["fixture_id"]) for m in matches]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate normalised player-fixture keys")
    gw_keys = [(r["season"], r["element"], r["gameweek"]) for r in gameweeks]
    if gw_keys != sorted(set(gw_keys)):
        raise ValueError("Player-Gameweek keys must be unique and ordered numerically")
    grouped = defaultdict(list)
    for m in matches:
        grouped[m["season"], m["element"], m["gameweek"]].append(m)
    if set(gw_keys) != set(grouped):
        raise ValueError("Player-Gameweek coverage differs from retained match records")
    for row in gameweeks:
        records = grouped[row["season"], row["element"], row["gameweek"]]
        if set(row["fixture_ids"]) != {m["fixture_id"] for m in records}:
            raise ValueError("Lost fixture identity in Gameweek aggregation")
        if row["observed_points"] != strict_sum([m["total_points"] for m in records]):
            raise ValueError("Observed points do not reconcile to fixture records")
        if row["target_complete"]:
            if row["target_points"] != row["observed_points"]:
                raise ValueError("Target does not reconcile to fixture points")
        elif row["target_points"] is not None:
            raise ValueError("Incomplete target must be null")
        if row["feature_cutoff_utc"] is None and any(row[c] is not None for c in FEATURE_COLUMNS):
            raise ValueError("Ungated historical features")
    if feature_file.metadata.num_rows != len(gameweeks):
        raise ValueError("Feature table coverage differs")
    features = (r for batch in feature_file.iter_batches(batch_size=512) for r in batch.to_pylist())
    for row, feature in zip(gameweeks, features):
        if feature != {k: row[k] for k in feature}:
            raise ValueError("Feature table values/identities differ from Gameweek rows")
        if set(feature) != {"season", "player_key", "gameweek", "feature_cutoff_utc",
                            "feature_status", *FEATURE_COLUMNS}:
            raise ValueError("Feature table contains unapproved source/outcome fields")
    # Recompute per player to keep validation memory bounded. This checks the real
    # archive against the cutoff filter/window code, beyond schema/count assertions.
    by_player = defaultdict(list)
    for m in matches:
        by_player[m["player_key"]].append(m)
    prepared = {(r["player_key"], r["gameweek"]): r for r in gameweeks}
    for player_matches in by_player.values():
        for expected in gameweek_dataset(player_matches, deadlines):
            actual = prepared[expected["player_key"], expected["gameweek"]]
            if (actual["feature_cutoff_utc"] != expected["feature_cutoff_utc"]
                    or feature_values(actual) != feature_values(expected)):
                raise ValueError(f"Historical features fail cutoff reconstruction: {expected['player_key']}")
    profiles = []
    for season in seasons:
        totals = defaultdict(list)
        for m in matches:
            if m["season"] == season:
                totals[m["element"]].append(m["total_points"])
        players = read_csv(raw_dir / season / "players_raw.csv")
        mismatches = []
        for p in players.rows:
            element = int(p["id"])
            if element not in totals:
                continue
            recorded = strict_sum(totals[element])
            snapshot = int(p["total_points"])
            if recorded is not None and recorded != snapshot:
                mismatches.append({"element": element, "history_points": recorded,
                                   "snapshot_points": snapshot})
        profiles.append({"season": season, "season_total_mismatches": mismatches})
    quality = pq.read_table(output_dir / "quality.parquet").to_pylist()
    if quality != quality_metadata(gameweeks, profiles):
        raise ValueError("Evaluation quality metadata does not reconcile to source totals")
    source_rows = 0
    paths = {"history": "gws/merged_gw.csv", "players": "players_raw.csv",
             "fixtures": "fixtures.csv", "teams": "teams.csv"}
    for season in seasons:
        for name, csv_path in paths.items():
            original = read_csv(raw_dir / season / csv_path)
            restored = pq.read_table(output_dir / "source" / season / f"{name}.parquet")
            if restored.column_names != original.columns or restored.to_pylist() != original.rows:
                raise ValueError(f"Source token/column loss: {season}/{name}")
            source_rows += len(original.rows)
    return {"match_records": len(matches), "player_gameweeks": len(gameweeks),
            "feature_rows": len(gameweeks), "preserved_source_rows": source_rows,
            "verified_deadlines": sum(bool(r.get("deadline_utc")) for r in metadata["deadline_records"])
            if metadata["verified_archive"] else 0,
            "evaluation_excluded_rows": sum(not q["evaluation_eligible"] for q in quality),
            "double_gameweeks": sum(r["fixture_records"] > 1 for r in gameweeks)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed")
    args = parser.parse_args()
    print(validate(args.raw_dir, args.output_dir))


if __name__ == "__main__":
    main()
