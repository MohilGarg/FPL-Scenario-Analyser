"""Package the existing fitted Ridge as portable JSON. This command never fits a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def export(source: Path, output: Path) -> dict:
    import joblib

    from ml.dataset import FEATURE_COLUMNS

    bundle = joblib.load(source)  # own local Phase 2b artifact, never a remote pickle
    pre, model = bundle["preprocessor"], bundle["model"]
    if pre.names != FEATURE_COLUMNS or model.alpha != 100:
        raise ValueError("Expected the approved Phase 2b Ridge artifact")
    data = {
        "format_version": 1,
        "model_version": "ridge-baselines-v1-train-2023-25",
        "training_seasons": ["2023-24", "2024-25"],
        "training_rows": 53975,
        "alpha": 100,
        "source_artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "features": list(FEATURE_COLUMNS),
        "kept": pre.kept.tolist(),
        "medians": pre.median.tolist(),
        "means": pre.mean.tolist(),
        "scales": pre.scale.tolist(),
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "dropped_empty": pre.dropped_empty,
        "dropped_constant": pre.dropped_constant,
        "contract": "unmultiplied observed player-GW points; one-step, same-season lagged fixture history; 24h buffer; no schedule/availability/position inputs",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("ml/artifacts/baselines-v1/final_models/ridge.joblib")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("src/fpl_forfeit/assistant/ridge_v1.json")
    )
    args = parser.parse_args()
    data = export(args.source, args.output)
    print(
        f"Exported {data['model_version']}: {len(data['kept'])} retained coefficients; no fitting"
    )
