"""Validate a completed run and reproduce saved predictions without training/refitting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pyarrow.parquet as pq

from ml.evaluation.metrics import choose_model, evaluate
from ml.sources import ROOT, digest
from ml.training.data import load_population
from ml.training.experiment import DEFAULT_CONFIG, fingerprints
from ml.training.models import predict_models


def verify(directory: Path, data_dir: Path, config_path: Path) -> dict:
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    result = json.loads((directory / "test.json").read_text(encoding="utf-8"))
    frozen = json.loads((directory / "holdout_started.json").read_text(encoding="utf-8"))
    current = fingerprints(data_dir, config_path)
    recorded = selection["fingerprints"]
    for key in ("config_sha256", "inputs_sha256", "versions"):
        if current[key] != recorded[key]:
            raise ValueError(f"Experiment provenance changed: {key}")
    report_only_changes = []
    for name, expected in recorded["code_sha256"].items():
        if current["code_sha256"].get(name) != expected:
            # Chart layout may be corrected after scoring; the original rendering
            # source is retained. Fit/data/metrics code may not change silently.
            original = directory / "report_source_at_selection.py"
            if name.replace("\\", "/") != "evaluation/report.py" or not original.exists() or digest(original.read_bytes()) != expected:
                raise ValueError(f"Fitting/data/metric source changed: {name}")
            report_only_changes.append(name)
    if (selection["selected_approach"] != choose_model(selection["metrics"])
            or result["selected_before_test"] != selection["selected_approach"]
            or result["test_used_for_selection"] or not frozen["models_frozen_before_holdout_read"]):
        raise ValueError("Chronological selection/freeze protocol mismatch")
    for name, expected in frozen["fitted_model_hashes"].items():
        if digest((directory / "final_models" / name).read_bytes()) != expected:
            raise ValueError(f"Frozen fitted artifact changed: {name}")
    config = selection["config"]
    checks = {}
    for phase, season, model_dir, expected_metrics in (
        ("validation", config["splits"]["validation"], "validation_models", selection["metrics"]),
        ("test", config["splits"]["test"], "final_models", result["metrics"]),
    ):
        population = load_population(data_dir, [season], config["expected_candidates"])
        saved = pq.read_table(directory / f"{phase}_predictions.parquet")
        if (saved["player_key"].to_pylist() != population.player_key.tolist()
                or saved["gameweek"].to_pylist() != population.gameweek.tolist()):
            raise ValueError("Prediction row identity mismatch")
        np.testing.assert_array_equal(saved["actual_points"].to_numpy(), population.y)
        bundle = joblib.load(directory / model_dir / "ridge.joblib")  # own locally generated artifact
        models = {"recent_average": None, "ridge": bundle["model"],
                  "lightgbm": lgb.Booster(model_file=str(directory / model_dir / "lightgbm.txt"))}
        predictions, _ = predict_models(bundle["preprocessor"], models, population.x, config)
        for name, values in predictions.items():
            np.testing.assert_allclose(saved[name].to_numpy(), values, rtol=0, atol=1e-12)
        evaluated = evaluate(population, predictions)
        for name, groups in evaluated.items():
            for group, values in groups.items():
                if values["n"] != expected_metrics[name][group]["n"]:
                    raise ValueError("Metric group population mismatch")
                if values["n"]:
                    for metric in ("mae", "rmse", "bias"):
                        np.testing.assert_allclose(values[metric], expected_metrics[name][group][metric], rtol=0, atol=1e-12)
        checks[phase] = len(population.y)
    return {"prediction_rows_reproduced": checks, "selected": selection["selected_approach"],
            "training_performed": False, "report_only_layout_changes": report_only_changes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/baselines-v1")
    args = parser.parse_args()
    print(verify(args.output_dir, args.data_dir, args.config))


if __name__ == "__main__":
    main()
