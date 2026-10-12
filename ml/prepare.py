"""CLI: python -m ml.prepare [fetch|prepare|run]. All outputs stay under ml/."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import cross_season_identity, markdown_report, season_profile
from .dataset import (
    FEATURE_COLUMNS, INT_STATS, CsvData, gameweek_dataset, load_deadlines,
    normalise_matches, read_csv,
)
from .sources import ROOT, fetch, source_lock, verify_cache


def write_parquet(path: Path, rows: list[dict[str, Any]], *, columns: list[str] | None = None) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Install ml/requirements.txt in your isolated research environment") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is not None:
        # Preserve original field names and tokens without inference or null conversion.
        table = pa.table({c: pa.array([r[c] for r in rows], type=pa.string()) for c in columns})
    else:
        table = pa.Table.from_pylist(rows)
        integer_columns = {
            "element", "player_code", "gameweek", "fixture_id", "source_row", "team_id",
            "team_code", "opponent_id", "fixture_records", "target_points", "observed_points",
            "pre_gw_history_fixtures", "pre_gw_history_gameweeks", *INT_STATS,
            *(f"observed_{s}" for s in INT_STATS), *(f"pre_gw_{s}_sum" for s in INT_STATS),
        }
        # Stable numeric/timestamp types even when a column is entirely unknown.
        for index, field in enumerate(table.schema):
            if field.name in integer_columns:
                dtype = pa.int64()
            elif field.name.endswith("_utc"):
                # Fixed UTC offset round-trips on Windows without an extra tzdata package.
                dtype = pa.timestamp("us", tz="+00:00")
            elif pa.types.is_null(field.type):
                dtype = pa.float64()
            else:
                continue
            if field.type != dtype:
                table = table.set_column(index, field.name, table.column(index).cast(dtype))
    temporary = path.with_suffix(".parquet.part")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def prepare(raw_dir: Path, output_dir: Path, report_dir: Path, seasons: list[str],
            deadline_file: Path | None = None) -> dict:
    manifest = verify_cache(raw_dir, seasons)
    deadlines = load_deadlines(deadline_file)
    all_tables: dict[str, dict[str, CsvData]] = {}
    all_matches = []
    all_gameweeks = []
    profiles = []
    for season in seasons:
        directory = raw_dir / season
        tables = {"history": read_csv(directory / "gws/merged_gw.csv"),
                  "players": read_csv(directory / "players_raw.csv"),
                  "teams": read_csv(directory / "teams.csv"),
                  "fixtures": read_csv(directory / "fixtures.csv")}
        all_tables[season] = tables
        matches, diagnostics = normalise_matches(season, **tables)
        gameweeks = gameweek_dataset(matches, deadlines)
        profiles.append(season_profile(season, tables, matches, gameweeks, diagnostics))
        all_matches.extend(matches)
        all_gameweeks.extend(gameweeks)
        for name, data in tables.items():
            write_parquet(output_dir / "source" / season / f"{name}.parquet", data.rows,
                          columns=data.columns)
    if not all_gameweeks:
        raise ValueError("No player-Gameweek observations")
    all_matches.sort(key=lambda r: (r["season"], r["element"], r["gameweek"], r["fixture_id"]))
    all_gameweeks.sort(key=lambda r: (r["season"], r["element"], r["gameweek"]))
    write_parquet(output_dir / "match_records.parquet", all_matches)
    write_parquet(output_dir / "player_gameweeks.parquet", all_gameweeks)
    feature_rows = [{k: r[k] for k in ("season", "player_key", "gameweek", "feature_cutoff_utc",
                                     "feature_status", *FEATURE_COLUMNS)} for r in all_gameweeks]
    write_parquet(output_dir / "features.parquet", feature_rows)
    identity = cross_season_identity(profiles, all_tables)
    audit = {"manifest": manifest, "seasons": profiles, "identity": identity,
             "deadline_file": str(deadline_file) if deadline_file else None,
             "deadline_sha256": hashlib.sha256(deadline_file.read_bytes()).hexdigest()
             if deadline_file else None,
             "feature_columns": FEATURE_COLUMNS, "result_readiness_lag_hours": 24}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    (report_dir / "audit.md").write_text(markdown_report(profiles, manifest, identity), encoding="utf-8")
    (output_dir / "feature_columns.json").write_text(json.dumps(FEATURE_COLUMNS, indent=2) + "\n",
                                                     encoding="utf-8")
    sample_columns = ["season", "element", "name", "gameweek", "fixture_ids", "target_points",
                      "observed_minutes", "observed_expected_goals", "feature_status"]
    sample = []
    for season in seasons:
        rows = [r for r in all_gameweeks if r["season"] == season]
        sample.extend([r for r in rows if r["observed_minutes"] and r["gameweek"] == 1][:3])
        sample.extend([r for r in rows if r["fixture_records"] > 1 and r["observed_minutes"]][:2])
        sample.extend([r for r in rows if r["observed_minutes"] == 0][:1])
    with (output_dir / "sample.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sample_columns)
        writer.writeheader()
        for row in sample:
            writer.writerow({k: json.dumps(row[k]) if isinstance(row[k], list) else row[k]
                             for k in sample_columns})
    (output_dir / "sample.json").write_text(json.dumps(sample, indent=2, default=json_default) + "\n",
                                             encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "prepare", "run"), nargs="?", default="run")
    parser.add_argument("--seasons", nargs="+", choices=source_lock()["seasons"],
                        default=source_lock()["seasons"])
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports/generated")
    parser.add_argument("--deadlines", type=Path,
                        help="Sourced deadline CSV; absent deadlines leave ALL feature values null")
    args = parser.parse_args()
    if len(set(args.seasons)) != len(args.seasons):
        parser.error("Seasons must not be repeated")
    if args.command in {"fetch", "run"}:
        manifest = fetch(args.raw_dir, args.seasons)
        print(f"Verified {len(manifest['files'])} cached files at {manifest['revision']}")
    if args.command in {"prepare", "run"}:
        audit = prepare(args.raw_dir, args.output_dir, args.report_dir, args.seasons, args.deadlines)
        for p in audit["seasons"]:
            print(f"{p['season']}: {p['match_records']:,} player matches; "
                  f"{p['player_gameweeks']:,} player-GWs; {p['double_gameweeks']} doubles; "
                  f"{p['incomplete_targets']} incomplete targets")
        print(f"Parquet: {args.output_dir}\nAudit: {args.report_dir / 'audit.md'}")


if __name__ == "__main__":
    main()
