"""Offline Phase 3a preparation: python -m ml.prepare_fixtures [run|validate]."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import math
from itertools import islice
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    import pyarrow as pa

from ml.dataset import Row, read_csv, timestamp
from ml.deadlines import reconstruct_deadlines
from ml.fixture_features import (PLAYER_TEAM_FEATURE_COLUMNS, TEAM_FEATURE_COLUMNS, normalise_fixtures,
    club_proxy_audit, player_club_features, retrospective_context, schema_document, team_deadline_features, team_index,
    validate_config, validate_match_links, xg_coverage_audit)
from ml.prepare import json_default, write_parquet
from ml.sources import ROOT, digest, source_lock, verify_cache

CONFIG = ROOT / "fixture-config.json"
SCHEMA = ROOT / "schemas/fixture-context-v1.json"


def json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=json_default, allow_nan=False) + "\n", encoding="utf-8")


def load_inputs(raw_dir: Path, data_dir: Path, seasons: list[str]) -> tuple:
    import pyarrow.parquet as pq
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    validate_config(config)
    if json.loads(SCHEMA.read_text(encoding="utf-8")) != schema_document():
        raise ValueError("Versioned approved schema differs from code")
    if not seasons or len(set(seasons)) != len(seasons) or any(s not in source_lock()["seasons"] for s in seasons):
        raise ValueError("Invalid requested seasons")
    manifest = verify_cache(raw_dir, seasons)
    proof = reconstruct_deadlines(raw_dir, seasons)
    deadlines = {(r["season"], r["gameweek"]): timestamp(r["deadline_utc"]) for r in proof if r["deadline_utc"]}
    meta = json.loads((data_dir / "preparation_metadata.json").read_text(encoding="utf-8"))
    if not meta["verified_archive"] or digest(Path(meta["deadline_file"]).read_bytes()) != meta["deadline_sha256"]:
        raise ValueError("Prepared player data requires the checksum-verified calendar")
    rosters, fixtures = {}, []
    for season in seasons:
        rosters[season] = team_index(season, read_csv(raw_dir / season / "teams.csv").rows)
        fixtures.extend(normalise_fixtures(season, read_csv(raw_dir / season / "fixtures.csv").rows, rosters[season]))
    filters = [("season", "in", seasons)]
    players = pq.read_table(data_dir / "features.parquet", filters=filters,
                           columns=["season", "player_key", "gameweek", "feature_cutoff_utc"]).to_pylist()
    if any(r["feature_cutoff_utc"] != deadlines.get((r["season"], r["gameweek"])) for r in players):
        raise ValueError("Player cutoffs differ from verified deadline proof")
    keys = [(r["season"], r["player_key"], r["gameweek"]) for r in players]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate player sidecar keys")
    matches = pq.read_table(data_dir / "match_records.parquet", filters=filters,
        columns=["season", "player_key", "element", "fixture_id", "gameweek", "team_id", "kickoff_utc",
                 "finished", "starts", "minutes", "expected_goals"]).to_pylist()
    validate_match_links(fixtures, matches)
    return config, manifest, proof, deadlines, rosters, fixtures, players, matches


def feature_type(name: str) -> pa.DataType:
    import pyarrow as pa
    if name.endswith(("_short_history", "_no_history", "_missing")):
        return pa.bool_()
    if name.endswith(("_match_count", "_sum", "_history_matches", "_past7_days", "_past14_days")):
        return pa.int64()
    return pa.float64()


def write_player_sidecar(path: Path, rows: Iterable[Row]) -> None:
    """512-row batches avoid materialising 85,000 wide Python dictionaries."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([("season", pa.string()), ("player_key", pa.string()), ("gameweek", pa.int64()),
        ("feature_cutoff_utc", pa.timestamp("us", tz="+00:00")), ("last_observed_team_id", pa.int64()), ("club_status", pa.string()),
        *[(c, feature_type(c)) for c in PLAYER_TEAM_FEATURE_COLUMNS]])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.part")
    iterator = iter(rows)
    with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
        while batch := list(islice(iterator, 512)):
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    temporary.replace(path)


def approved_player_inputs(base: pa.Table, sidecar: pa.Table) -> pa.Table:
    """Opt-in v2 preparation join; metadata/outcome/research columns cannot become X."""
    from ml.dataset import FEATURE_COLUMNS
    keys = ["season", "player_key", "gameweek", "feature_cutoff_utc"]
    for key in keys:
        if not base[key].equals(sidecar[key]):
            raise ValueError(f"Player-context identity/order/cutoff mismatch: {key}")
    identities = list(zip(*(base[c].to_pylist() for c in keys[:3])))
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate player-context keys")
    base = base.select([*keys, *FEATURE_COLUMNS])
    for column in PLAYER_TEAM_FEATURE_COLUMNS:
        base = base.append_column(column, sidecar[column])
    return base


def coverage_tables(team_rows: list[Row], output: Path, research_gws: list[Row]) -> tuple[list[Row], list[Row]]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    teams = pa.Table.from_pylist(team_rows)
    players = pq.read_table(output / "player_team_features.parquet")
    summaries = {(r["season"], r["gameweek"]): {"season": r["season"], "gameweek": r["gameweek"],
                  "team_rows": 0, "team_rows_with_history": 0, "team_rows_full3": 0, "team_rows_full5": 0,
                  "team_rows_full10": 0, "player_rows": 0, "player_rows_with_club_proxy": 0,
                  "verified_schedule_rows": 0, "retro_blank_teams": 0, "retro_double_teams": 0,
                  "verified_deadline": r["feature_cutoff_utc"] is not None} for r in team_rows}
    for row in team_rows:
        s = summaries[row["season"], row["gameweek"]]
        s["team_rows"] += 1
        s["team_rows_with_history"] += (row["pre_team_overall_season_match_count"] or 0) > 0
        for n in (3, 5, 10):
            s[f"team_rows_full{n}"] += (row[f"pre_team_overall_last{n}_match_count"] or 0) >= n
    for row in research_gws:
        s = summaries[row["season"], row["gameweek"]]
        s["retro_blank_teams"] += row["retro_blank"] is True
        s["retro_double_teams"] += row["retro_double"] is True
    counts = players.group_by(["season", "gameweek"], use_threads=False).aggregate([
        ("player_key", "count"), ("last_observed_team_id", "count")]).to_pylist()
    for row in counts:
        s = summaries[row["season"], row["gameweek"]]
        s["player_rows"] = row["player_key_count"]
        s["player_rows_with_club_proxy"] = row["last_observed_team_id_count"]
    detailed = []
    for label, table, columns, history_column in (("team_deadline", teams, TEAM_FEATURE_COLUMNS, "pre_team_overall_season_match_count"),
            ("player_sidecar", players, PLAYER_TEAM_FEATURE_COLUMNS, "pre_gw_last_observed_club_overall_season_match_count")):
        with_history = table.filter(pc.fill_null(pc.greater(table[history_column], 0), False))
        observed = with_history.group_by(["season", "gameweek"], use_threads=False).aggregate([(c, "count") for c in columns])
        observed_index = {(r["season"], r["gameweek"]): r for r in observed.to_pylist()}
        all_counts = table.group_by(["season", "gameweek"], use_threads=False).aggregate([(c, "count") for c in columns])
        for row in all_counts.to_pylist():
            s = summaries[row["season"], row["gameweek"]]
            for column in columns:
                detailed.append({"dataset": label, "season": row["season"], "gameweek": row["gameweek"], "feature": column,
                    "rows": s["team_rows"] if label == "team_deadline" else s["player_rows"],
                    "non_null_rows": row[f"{column}_count"],
                    "non_null_with_observed_history_rows": observed_index.get((row["season"], row["gameweek"]), {}).get(f"{column}_count", 0)})
    return [summaries[k] for k in sorted(summaries)], detailed


def input_fingerprints(raw_dir: Path, data_dir: Path, seasons: list[str]) -> dict:
    return {"configuration": digest(CONFIG.read_bytes()), "schema": digest(SCHEMA.read_bytes()),
            "code": {name: digest((ROOT / name).read_bytes()) for name in ("fixture_features.py", "prepare_fixtures.py", "dataset.py", "deadlines.py", "sources.py", "prepare.py")},
            "source_locks": {name: digest((ROOT / name).read_bytes()) for name in ("source-lock.json", "deadline-source-lock.json", "fixture-source-review.json")},
            "season_outcomes": {f"{s}/{name}": digest((raw_dir / s / name).read_bytes()) for s in seasons for name in ("fixtures.csv", "teams.csv")},
            "prepared_inputs": {name: digest((data_dir / name).read_bytes()) for name in ("features.parquet", "match_records.parquet", "preparation_metadata.json")},
            "pyarrow_version": importlib.metadata.version("pyarrow")}


def prepare(raw_dir: Path, data_dir: Path, output: Path, report_dir: Path, seasons: list[str]) -> dict:
    if (not output.resolve().is_relative_to((ROOT / "data/processed").resolve())
            or not report_dir.resolve().is_relative_to((ROOT / "reports/generated").resolve())
            or output.resolve() == data_dir.resolve()):
        raise ValueError("Write only to a separate ignored ML data directory and generated-report directory")
    start = perf_counter()
    config, manifest, proof, deadlines, rosters, fixtures, players, matches = load_inputs(raw_dir, data_dir, seasons)
    team_rows = team_deadline_features(fixtures, rosters, deadlines, config)
    fixture_rows, research_gws = retrospective_context(fixtures, team_rows, deadlines, rosters)
    xg_audit = xg_coverage_audit(fixtures, matches)
    write_parquet(output / "team_deadline_features.parquet", team_rows)
    write_player_sidecar(output / "player_team_features.parquet", player_club_features(players, matches, team_rows, deadlines))
    write_parquet(output / "retrospective_fixture_context.parquet", fixture_rows)
    write_parquet(output / "retrospective_team_gameweeks.parquet", research_gws)
    write_parquet(output / "team_xg_quality.parquet", xg_audit)
    import pyarrow.parquet as pq
    club_ids = pq.read_table(output / "player_team_features.parquet", columns=["season", "player_key", "gameweek", "last_observed_team_id"]).to_pylist()
    club_audit = club_proxy_audit(club_ids, matches)
    write_parquet(output / "player_club_quality.parquet", club_audit)
    summary, detailed = coverage_tables(team_rows, output, research_gws)
    write_parquet(output / "feature_coverage.parquet", detailed)
    with (output / "coverage_by_gameweek.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    json_write(output / "approved_schema.json", schema_document())
    season_summary = []
    for season in seasons:
        rows = [r for r in summary if r["season"] == season]
        fs = [f for f in fixtures if f["season"] == season]
        xa = [r for r in xg_audit if r["season"] == season]
        season_summary.append({"season": season, **{c: sum(r[c] for r in rows) for c in rows[0] if c not in ("season", "gameweek")},
            "fixture_results": len(fs), "complete_results": sum(f["result_complete"] for f in fs),
            "missing_kickoffs": sum(f["kickoff_utc"] is None for f in fs), "missing_final_events": sum(f["gameweek"] is None for f in fs),
            "team_matches_with_11_recorded_starts": sum(r["quality_starts_sum"] == 11 for r in xa),
            "team_matches_with_nonnull_player_xg_sum": sum(r["research_player_xg_sum"] is not None for r in xa),
            "club_proxy_target_mismatches": sum(r["season"] == season and r["quality_proxy_matches_target_club"] is False for r in club_audit)})
    # Neither season summaries nor any source/evaluation coverage metadata are approved predictors.
    result = {"schema_version": config["schema_version"], "configuration": config, "seasons": season_summary,
              "provenance": input_fingerprints(raw_dir, data_dir, seasons), "source_revision": manifest["revision"],
              "deadline_proof": proof, "training_performed": False, "verified_schedule_coverage": 0,
              "source_results_revised_vintages": True, "elapsed_seconds": perf_counter() - start,
              "outputs_sha256": {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir()) if p.suffix in (".parquet", ".csv")}}
    json_write(output / "metadata.json", result)
    json_write(report_dir / "coverage.json", {"seasons": season_summary, "gameweeks": summary})
    json_write(report_dir / "sample.json", [r for r in team_rows if r["gameweek"] in (1, 5) and r["team_id"] == 1])
    return result


def validate(raw_dir: Path, data_dir: Path, output: Path, seasons: list[str]) -> dict:
    """Check hashes and independently regenerate the small team/context tables; scan the wide sidecar in batches."""
    import pyarrow.parquet as pq
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    if metadata["provenance"] != input_fingerprints(raw_dir, data_dir, seasons):
        raise ValueError("Feature preparation fingerprints changed")
    for name, checksum in metadata["outputs_sha256"].items():
        if digest((output / name).read_bytes()) != checksum:
            raise ValueError(f"Generated file checksum mismatch: {name}")
    if json.loads((output / "approved_schema.json").read_text(encoding="utf-8")) != schema_document():
        raise ValueError("Output schema changed")
    config, _, _, deadlines, rosters, fixtures, players, matches = load_inputs(raw_dir, data_dir, seasons)
    teams = team_deadline_features(fixtures, rosters, deadlines, config)
    research, gws = retrospective_context(fixtures, teams, deadlines, rosters)
    for filename, expected in (("team_deadline_features.parquet", teams), ("retrospective_fixture_context.parquet", research),
                               ("retrospective_team_gameweeks.parquet", gws), ("team_xg_quality.parquet", xg_coverage_audit(fixtures, matches))):
        if pq.read_table(output / filename).to_pylist() != expected:
            raise ValueError(f"Regenerated values differ: {filename}")
    expected = iter(player_club_features(players, matches, teams, deadlines))
    sidecar = pq.ParquetFile(output / "player_team_features.parquet")
    allowed = {"season", "player_key", "gameweek", "feature_cutoff_utc", "last_observed_team_id", "club_status", *PLAYER_TEAM_FEATURE_COLUMNS}
    if set(sidecar.schema_arrow.names) != allowed:
        raise ValueError("Sidecar contains outcome/research/unapproved columns")
    count = 0
    for batch in sidecar.iter_batches(batch_size=512):
        values = batch.to_pylist()
        if values != list(islice(expected, len(values))):
            raise ValueError("Player history/club join failed regeneration")
        for row in values:
            if any(isinstance(row[c], float) and not math.isfinite(row[c]) for c in PLAYER_TEAM_FEATURE_COLUMNS):
                raise ValueError("Nonfinite sidecar feature")
        count += len(values)
    if list(islice(expected, 1)) or count != len(players):
        raise ValueError("Player sidecar coverage differs")
    ids = pq.read_table(output / "player_team_features.parquet", columns=["season", "player_key", "gameweek", "last_observed_team_id"]).to_pylist()
    if pq.read_table(output / "player_club_quality.parquet").to_pylist() != club_proxy_audit(ids, matches):
        raise ValueError("Separate club quality metadata differs")
    if set(TEAM_FEATURE_COLUMNS) & {c for c in pq.read_schema(output / "retrospective_fixture_context.parquet").names if c.startswith("retro_")}:
        raise ValueError("Research columns entered the approved schema")
    return {"team_deadline_rows": len(teams), "player_rows": count, "retrospective_fixture_sides": len(research),
            "verified_schedule_rows": 0, "schema_columns": len(PLAYER_TEAM_FEATURE_COLUMNS), "training_performed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate"), nargs="?", default="run")
    parser.add_argument("--seasons", nargs="+", choices=source_lock()["seasons"], default=source_lock()["seasons"])
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/fixture_context")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports/generated/fixture_context")
    args = parser.parse_args()
    if args.command == "validate":
        print(validate(args.raw_dir, args.data_dir, args.output_dir, args.seasons))
    else:
        result = prepare(args.raw_dir, args.data_dir, args.output_dir, args.report_dir, args.seasons)
        print(json.dumps({"seasons": result["seasons"], "seconds": result["elapsed_seconds"], "training_performed": False}, indent=2))


if __name__ == "__main__":
    main()
