"""Two-stage CLI: select on validation, then freeze/refit before accessing holdout."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
from pathlib import Path
from time import perf_counter

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ml.dataset import FEATURE_COLUMNS
from ml.evaluation.metrics import choose_model, evaluate, paired_mae_uncertainty
from ml.sources import ROOT, digest
from ml.training.data import Population, load_population, subset
from ml.training.models import Preprocessor, fit_models, predict_models

DEFAULT_CONFIG = ROOT / "training/configs/baselines-v1.json"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_config(config: dict) -> None:
    if config["protocol_version"] != 1 or config["splits"] != {
        "train": "2023-24", "validation": "2024-25", "test": "2025-26"
    }:
        raise ValueError("Protocol requires fixed distinct chronological 2023/24 → 2024/25 → 2025/26 splits")
    if not 1 <= config["threads"] <= 4 or not 1 <= config["lightgbm"]["num_boost_round"] <= 400:
        raise ValueError("Experiment exceeds bounded thread/tree budget")
    if (config["selection"]["primary_metric"] != "mae" or config["selection"]["tie_breaker"] != "rmse"
            or config["selection"]["early_stopping"] or config["selection"]["prediction_clipping"]
            or not config["selection"]["refit_all_fixed_approaches_on_train_plus_validation"]):
        raise ValueError("Unsupported selection/test protocol")
    if config["recent_average"]["feature"] not in FEATURE_COLUMNS or config["ridge"]["alpha"] <= 0:
        raise ValueError("Invalid baseline feature or regularisation")


def versions() -> dict:
    return {"python": platform.python_version(), **{name: importlib.metadata.version(name) for name in
            ("numpy", "scipy", "scikit-learn", "lightgbm", "pyarrow", "matplotlib", "joblib", "threadpoolctl")}}


def fingerprints(data_dir: Path, config_path: Path) -> dict:
    # Hashing the files records provenance; it does not expose holdout outcomes to
    # selection. Season-filtered reads in load_population are the data access gate.
    code_files = sorted((ROOT / "training").rglob("*.py")) + sorted((ROOT / "evaluation").rglob("*.py"))
    return {"config_sha256": digest(config_path.read_bytes()),
            "code_sha256": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in code_files},
            "inputs_sha256": {name: digest((data_dir / name).read_bytes()) for name in
                              ("features.parquet", "player_gameweeks.parquet", "quality.parquet",
                               "feature_columns.json", "preparation_metadata.json")},
            "versions": versions()}


def save_fitted(path: Path, preprocessor: Preprocessor, models: dict, config: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    write_json(path / "preprocessing.json", preprocessor.metadata())
    write_json(path / "recent_average.json", config["recent_average"])
    joblib.dump({"preprocessor": preprocessor, "model": models["ridge"]}, path / "ridge.joblib")
    models["lightgbm"].save_model(str(path / "lightgbm.txt"))
    ridge = models["ridge"]
    with (path / "ridge_coefficients.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["feature", "coefficient_per_training_std", "coefficient_per_raw_unit"])
        for i, coefficient, scale in zip(preprocessor.kept, ridge.coef_, preprocessor.scale):
            writer.writerow([FEATURE_COLUMNS[i], coefficient, coefficient / scale])
    write_json(path / "ridge_intercept.json", {
        "standardised_intercept": float(ridge.intercept_),
        "raw_imputed_intercept": float(ridge.intercept_ - np.sum(ridge.coef_ * preprocessor.mean / preprocessor.scale)),
        "interpretation": "conditional, correlated-feature coefficients; not causal effects"})
    booster = models["lightgbm"]
    gain = booster.feature_importance(importance_type="gain")
    splits = booster.feature_importance(importance_type="split")
    with (path / "lightgbm_importance.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["feature", "gain", "gain_percent", "split_count"])
        for i, g, s in zip(preprocessor.kept, gain, splits):
            writer.writerow([FEATURE_COLUMNS[i], g, 100 * g / gain.sum() if gain.sum() else 0, s])


def save_predictions(path: Path, population: Population, predictions: dict) -> None:
    pq.write_table(pa.table({"season": population.season, "player_key": population.player_key,
                            "gameweek": population.gameweek, "actual_points": population.y,
                            "observed_minutes": population.minutes, "verified_evaluation_position": population.position,
                            **predictions}), path, compression="zstd")


def uncertainty(population: Population, predictions: dict, config: dict, selected: str) -> dict:
    comparisons = {f"{model}_minus_recent_average": paired_mae_uncertainty(
        population.y, predictions["recent_average"], predictions[model], population.gameweek,
        seed=config["seed"], draws=config["uncertainty"]["bootstrap_draws"],
        block_length=config["uncertainty"]["gameweek_block_length"])
        for model in ("ridge", "lightgbm")}
    for name in predictions:
        if name != selected and name != "recent_average":
            comparisons[f"{selected}_minus_{name}"] = paired_mae_uncertainty(
                population.y, predictions[name], predictions[selected], population.gameweek,
                seed=config["seed"], draws=config["uncertainty"]["bootstrap_draws"],
                block_length=config["uncertainty"]["gameweek_block_length"])
    return comparisons


def select(data_dir: Path, output_dir: Path, config_path: Path) -> dict:
    if (output_dir / "holdout_started.json").exists():
        raise ValueError("Holdout has already been accessed; re-selection in this run is forbidden")
    if (output_dir / "selection.json").exists():
        raise ValueError("Selection already exists; use the test stage or a fresh reproducibility directory")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    population = load_population(data_dir, [config["splits"]["train"], config["splits"]["validation"]],
                                 config["expected_candidates"])
    train = subset(population, config["splits"]["train"])
    validation = subset(population, config["splits"]["validation"])
    print(f"Selection stage: fit {len(train.y):,} rows; validate {len(validation.y):,}; holdout not loaded", flush=True)
    started = perf_counter()
    preprocessor, models, fit_timing = fit_models(train.x, train.y, config)
    predictions, inference_timing = predict_models(preprocessor, models, validation.x, config)
    scored = evaluate(validation, predictions)
    selected = choose_model(scored)
    save_fitted(output_dir / "validation_models", preprocessor, models, config)
    save_predictions(output_dir / "validation_predictions.parquet", validation, predictions)
    result = {"stage": "validation_selection", "selected_approach": selected,
              "selection_rule": "lowest validation MAE, then RMSE; one fixed config per approach",
              "holdout_loaded": False, "training_seasons": [config["splits"]["train"]],
              "validation_season": config["splits"]["validation"], "config": config,
              "fingerprints": fingerprints(data_dir, config_path), "exclusions": population.exclusions,
              "metrics": scored, "uncertainty": uncertainty(validation, predictions, config, selected),
              "fit_timing": fit_timing, "inference_timing": inference_timing,
              "stage_seconds": perf_counter() - started}
    write_json(output_dir / "selection.json", result)
    print(json.dumps({"selected": selected, "validation": {m: v["all"] for m, v in scored.items()}}, indent=2), flush=True)
    return result


def test_holdout(data_dir: Path, output_dir: Path, config_path: Path) -> dict:
    if (output_dir / "holdout_started.json").exists():
        raise ValueError("Holdout stage already started; use saved predictions for reporting/reproducibility")
    selection = json.loads((output_dir / "selection.json").read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if selection["fingerprints"] != fingerprints(data_dir, config_path) or selection["holdout_loaded"]:
        raise ValueError("Config, code, data or versions changed after validation selection")
    if selection["selected_approach"] != choose_model(selection["metrics"]):
        raise ValueError("Selection manifest winner was altered")
    training = load_population(data_dir, [config["splits"]["train"], config["splits"]["validation"]],
                               config["expected_candidates"])
    print(f"Frozen test stage: refit all three fixed approaches on {len(training.y):,} earlier rows", flush=True)
    started = perf_counter()
    preprocessor, models, fit_timing = fit_models(training.x, training.y, config)
    save_fitted(output_dir / "final_models", preprocessor, models, config)
    # This manifest exists before any holdout data read. It also prevents an accidental
    # re-selection/retest after outcomes have been revealed, even if scoring fails.
    write_json(output_dir / "holdout_started.json", {
        "selected_approach": selection["selected_approach"], "config_sha256": selection["fingerprints"]["config_sha256"],
        "training_seasons": [config["splits"]["train"], config["splits"]["validation"]],
        "preprocessor_frozen": True, "models_frozen_before_holdout_read": True,
        "fitted_model_hashes": {p.name: digest(p.read_bytes()) for p in (output_dir / "final_models").iterdir()}})
    holdout = load_population(data_dir, [config["splits"]["test"]], config["expected_candidates"])
    predictions, inference_timing = predict_models(preprocessor, models, holdout.x, config)
    scored = evaluate(holdout, predictions)
    save_predictions(output_dir / "test_predictions.parquet", holdout, predictions)
    result = {"stage": "holdout_test", "selected_before_test": selection["selected_approach"],
              "test_season": config["splits"]["test"], "exclusions": holdout.exclusions,
              "metrics": scored, "uncertainty": uncertainty(holdout, predictions, config, selection["selected_approach"]),
              "fit_timing": fit_timing, "inference_timing": inference_timing,
              "stage_seconds": perf_counter() - started, "test_used_for_selection": False}
    write_json(output_dir / "test.json", result)
    print(json.dumps({"selected_before_test": selection["selected_approach"],
                      "test": {m: v["all"] for m, v in scored.items()}}, indent=2), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("select", "test", "report"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/baselines-v1")
    args = parser.parse_args()
    if args.stage == "select":
        select(args.data_dir, args.output_dir, args.config)
    elif args.stage == "test":
        test_holdout(args.data_dir, args.output_dir, args.config)
    else:
        from ml.evaluation.report import render_report
        render_report(args.output_dir)


if __name__ == "__main__":
    main()
