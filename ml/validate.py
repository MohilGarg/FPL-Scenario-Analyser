"""Fast validation of prepared Parquet, including original-token preservation."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from .dataset import FEATURE_COLUMNS, read_csv, strict_sum
from .sources import ROOT, verify_cache


def validate(raw_dir: Path, output_dir: Path) -> dict[str, int]:
    import pyarrow.parquet as pq

    matches = pq.read_table(output_dir / "match_records.parquet").to_pylist()
    gameweeks = pq.read_table(output_dir / "player_gameweeks.parquet").to_pylist()
    features = pq.read_table(output_dir / "features.parquet").to_pylist()
    seasons = sorted({r["season"] for r in gameweeks})
    verify_cache(raw_dir, seasons)
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
    if len(features) != len(gameweeks):
        raise ValueError("Feature table coverage differs")
    for row, feature in zip(gameweeks, features):
        if feature != {k: row[k] for k in feature}:
            raise ValueError("Feature table values/identities differ from Gameweek rows")
        if set(feature) != {"season", "player_key", "gameweek", "feature_cutoff_utc",
                            "feature_status", *FEATURE_COLUMNS}:
            raise ValueError("Feature table contains unapproved source/outcome fields")
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
            "feature_rows": len(features), "preserved_source_rows": source_rows,
            "double_gameweeks": sum(r["fixture_records"] > 1 for r in gameweeks)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed")
    args = parser.parse_args()
    print(validate(args.raw_dir, args.output_dir))


if __name__ == "__main__":
    main()
