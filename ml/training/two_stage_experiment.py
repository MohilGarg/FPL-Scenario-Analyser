"""Fit once on 2023/24; evaluate 2024/25. There is deliberately no test stage."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ml.dataset import FEATURE_COLUMNS
from ml.evaluation.two_stage_metrics import evaluate_two_stage
from ml.sources import ROOT, digest
from ml.training.data import Population, load_population, subset
from ml.training.experiment import versions, write_json
from ml.training.two_stage_models import fit_two_stage, predict_two_stage, regular_starters

DEFAULT_CONFIG = ROOT / "training/configs/two-stage-v1.json"
CODE_FILES = (
    "dataset.py", "readiness.py", "deadlines.py", "sources.py", "training/data.py",
    "training/models.py", "training/experiment.py", "training/two_stage_models.py",
    "training/two_stage_experiment.py", "evaluation/metrics.py", "evaluation/two_stage_metrics.py",
)


def validate_config(config: dict) -> None:
    if (config["protocol_version"] != 1 or config["splits"] != {"train": "2023-24", "validation": "2024-25"}
            or config["historical_reference_only"] != "2025-26"
            or config["expected_candidates"] != {"2023-24": 27877, "2024-25": 26098}):
        raise ValueError("Only 2023/24 fitting and 2024/25 validation are permitted; 2025/26 is consumed")
    reference = json.loads((ROOT / "training/configs/baselines-v1.json").read_text(encoding="utf-8"))
    if config["reference_ridge"] != reference["ridge"]:
        raise ValueError("Reference Ridge must match the existing benchmark")
    if (not 1 <= config["threads"] <= 4 or not 1 <= config["appearance"]["max_iter"] <= 2000
            or config["appearance"]["C"] <= 0 or config["conditional_ridge"]["alpha"] <= 0
            or config["appearance"]["class_weight"] is not None):
        raise ValueError("Invalid compute budget, regularisation or probability weighting")
    if (config["features"] != "existing_approved_list_only"
            or config["nonappearance"] != "training_nonappearance_points_mean"
            or config["probability_reference_feature"] not in FEATURE_COLUMNS
            or any(config[name] for name in ("parameter_search", "prediction_clipping", "validation_refit"))):
        raise ValueError("Unsupported feature, selection or mixture protocol")
    rule = config["regular_starters"]
    if any(f"{rule['window']}_{suffix}" not in FEATURE_COLUMNS for suffix in ("count", "start_rate", "minutes_mean")):
        raise ValueError("Regular-starter rule must use approved history only")
    edges = config["calibration"]["points_band_edges"]
    if sorted(set(edges)) != edges or not np.isfinite(edges).all() or not 2 <= config["calibration"]["probability_bins"] <= 20:
        raise ValueError("Invalid fixed calibration bands")
    if not 1 <= config["uncertainty"]["bootstrap_draws"] <= 5000 or not 1 <= config["uncertainty"]["gameweek_block_length"] <= 10:
        raise ValueError("Invalid bounded uncertainty configuration")


def population_hashes(population: Population) -> dict:
    """Hash the actual season-filtered rows, not unexamined later-season outcomes."""
    result = {}
    for name in ("x", "y", "minutes", "gameweek"):
        values = np.asarray(getattr(population, name), dtype="<f8").copy(order="C")
        values[np.isnan(values)] = np.nan  # canonicalise NaN payloads
        result[name] = digest(str(values.shape).encode() + values.tobytes())
    for name in ("season", "player_key", "position"):
        result[name] = digest(json.dumps(getattr(population, name).tolist(), separators=(",", ":")).encode())
    result["approved_features"] = digest(json.dumps(list(FEATURE_COLUMNS)).encode())
    return result


def provenance(config_path: Path, train: Population, validation: Population) -> dict:
    return {"config_sha256": digest(config_path.read_bytes()),
            "code_sha256": {name: digest((ROOT / name).read_bytes()) for name in CODE_FILES},
            "source_locks_sha256": {name: digest((ROOT / name).read_bytes()) for name in
                                    ("source-lock.json", "deadline-source-lock.json")},
            "training_rows_sha256": population_hashes(train),
            "validation_rows_sha256": population_hashes(validation), "versions": versions()}


def save_coefficients(output: Path, model) -> None:
    with (output / "coefficients.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["component", "feature", "coefficient_per_training_std", "coefficient_per_raw_unit"])
        for name, fitted in (("appearance_log_odds", model.appearance), ("conditional_points", model.conditional),
                             ("reference_ridge", model.reference)):
            for i, coefficient, scale in zip(model.preprocessor.kept, fitted.coef_.ravel(), model.preprocessor.scale):
                writer.writerow([name, FEATURE_COLUMNS[i], coefficient, coefficient / scale])


def safe_output(output: Path) -> None:
    if not output.resolve().is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Outputs must remain within ignored ml/artifacts/")


def run(data_dir: Path, output: Path, config_path: Path) -> dict:
    safe_output(output)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if (output / "started.json").exists() or (output / "results.json").exists():
        raise ValueError("Run already exists: use verify/report, or a fresh ignored directory for exact reproduction")
    started = perf_counter()
    # Do not load 2025/26 targets, features, historical experiment metrics or saved test models.
    population = load_population(data_dir, [config["splits"]["train"], config["splits"]["validation"]], config["expected_candidates"])
    train = subset(population, config["splits"]["train"])
    validation = subset(population, config["splits"]["validation"])
    fingerprint = provenance(config_path, train, validation)
    write_json(output / "started.json", {"started_utc": datetime.now(UTC).isoformat(), "config": config,
                                        "provenance": fingerprint, "consumed_season_loaded": False})
    model, fit_time = fit_two_stage(train.x, train.y, train.minutes, config)
    joblib.dump(model, output / "models.joblib")
    write_json(output / "preprocessing.json", model.preprocessor.metadata())
    write_json(output / "components.json", {
        "nonappearance_mean": model.nonappearance_mean, "appearance_training_mean": model.appearance_mean,
        "appearance_training_prevalence": model.appearance_prevalence,
        "intercepts": {"logistic_log_odds": model.appearance.intercept_.tolist(),
                       "conditional_points": float(model.conditional.intercept_),
                       "reference_ridge": float(model.reference.intercept_)},
        "coefficient_interpretation": "per training standard deviation; correlated features, not causal effects"})
    save_coefficients(output, model)
    prediction, predict_time = predict_two_stage(model, validation.x, config)
    evaluation = evaluate_two_stage(validation, prediction, model, config)
    pq.write_table(pa.table({"season": validation.season, "player_key": validation.player_key,
        "gameweek": validation.gameweek, "actual_points": validation.y, "observed_minutes": validation.minutes,
        "verified_evaluation_position": validation.position,
        "pre_deadline_regular_starter": regular_starters(validation.x, config), **prediction}),
        output / "validation_predictions.parquet", compression="zstd")
    result = {"config": config, "provenance": fingerprint, "exclusions": population.exclusions,
              "components": {"nonappearance_mean": model.nonappearance_mean, "appearance_mean": model.appearance_mean,
                             "appearance_prevalence": model.appearance_prevalence},
              "timing": {"fit": fit_time, "inference": predict_time, "total_seconds": perf_counter() - started},
              "evaluation": evaluation, "consumed_season_loaded": False,
              "artifacts_sha256": {name: digest((output / name).read_bytes()) for name in
                                   ("models.joblib", "preprocessing.json", "components.json", "coefficients.csv",
                                    "validation_predictions.parquet")}}
    write_json(output / "results.json", result)
    return result


def verify(data_dir: Path, output: Path, config_path: Path) -> dict:
    """Reload own fitted assets; reproduce predictions without fitting or later-season reads."""
    safe_output(output)
    result = json.loads((output / "results.json").read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    population = load_population(data_dir, [config["splits"]["train"], config["splits"]["validation"]], config["expected_candidates"])
    train, validation = subset(population, "2023-24"), subset(population, "2024-25")
    if result["config"] != config or result["provenance"] != provenance(config_path, train, validation):
        raise ValueError("Configuration, data, code or dependency fingerprints changed")
    for name, expected in result["artifacts_sha256"].items():
        if digest((output / name).read_bytes()) != expected:
            raise ValueError(f"Artifact checksum changed: {name}")
    model = joblib.load(output / "models.joblib")  # own ignored locally produced asset only
    prediction, _ = predict_two_stage(model, validation.x, config)
    saved = pq.read_table(output / "validation_predictions.parquet")
    if saved["player_key"].to_pylist() != validation.player_key.tolist() or saved["gameweek"].to_pylist() != validation.gameweek.tolist():
        raise ValueError("Saved observation order changed")
    for name, values in prediction.items():
        np.testing.assert_allclose(values, saved[name].to_numpy(), rtol=0, atol=1e-12)
    np.testing.assert_array_equal(regular_starters(validation.x, config), saved["pre_deadline_regular_starter"].to_numpy())
    if evaluate_two_stage(validation, prediction, model, config) != result["evaluation"]:
        raise ValueError("Recomputed evaluation differs")
    # Optional independent match against Phase 2b VALIDATION only, never its consumed test.
    previous = ROOT / "artifacts/baselines-v1/validation_predictions.parquet"
    matched_previous = False
    if previous.exists():
        reference = pq.read_table(previous, columns=["season", "player_key", "gameweek", "ridge"])
        if (reference["season"].to_pylist() != validation.season.tolist()
                or reference["player_key"].to_pylist() != validation.player_key.tolist()
                or reference["gameweek"].to_pylist() != validation.gameweek.tolist()):
            raise ValueError("Previous Ridge validation observations differ")
        np.testing.assert_allclose(prediction["ridge"], reference["ridge"].to_numpy(), rtol=0, atol=1e-12)
        matched_previous = True
    checks = {"rows": len(validation.y), "prediction_tolerance": 1e-12, "metrics_reproduced": True,
              "phase_2b_validation_ridge_matched": matched_previous, "consumed_season_loaded": False}
    write_json(output / "verification.json", checks)
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run", "verify", "report"))
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/two-stage-v1")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    safe_output(args.output_dir)
    if args.stage == "run":
        result = run(args.data_dir, args.output_dir, args.config)
        print(json.dumps({name: result["evaluation"]["points"][name]["all"] for name in ("ridge", "two_stage")}, indent=2))
    elif args.stage == "verify":
        print(json.dumps(verify(args.data_dir, args.output_dir, args.config), indent=2))
    else:
        from ml.evaluation.two_stage_report import render
        render(args.output_dir)
        print(f"Tables and charts: {args.output_dir}")


if __name__ == "__main__":
    main()
