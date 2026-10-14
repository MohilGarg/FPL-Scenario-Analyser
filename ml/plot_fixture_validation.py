"""Two compact figures from frozen chronological diagnostics; no fitting."""
from __future__ import annotations

import json
import os
from pathlib import Path


def main():
    os.environ.setdefault("MPLCONFIGDIR", "ml/artifacts/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path("ml/artifacts/fixture-components-v1")
    scores = json.loads((root/"simulation_validation.json").read_bytes())
    validation = json.loads((root/"validation.json").read_bytes())
    output = Path("ml/reports/figures")
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "fixture-components-v1"})
    fig, axes = plt.subplots(1, 2, figsize=(9,3.8), layout="constrained")
    bands = validation["calibration"]
    axes[0].plot([0,1],[0,1], color="#89929c", linestyle="--")
    axes[0].plot([b["predicted"] for b in bands], [b["observed"] for b in bands], "o-", color="#27699a")
    axes[0].set(xlabel="Appearance proposal probability", ylabel="Observed appearance frequency", title="Before football lineup constraints", xlim=(0,1), ylim=(0,1))
    axes[1].plot([0,90],[0,90], color="#89929c", linestyle="--")
    axes[1].plot([b["predicted_minutes"] for b in bands], [b["observed_minutes"] for b in bands], "o-", color="#27699a")
    axes[1].set(xlabel="Mean predicted proposal minutes", ylabel="Mean observed minutes", title="Same appearance bands", xlim=(0,90), ylim=(0,90))
    fig.suptitle("2024/25 fixture validation — historical schedule proxies")
    fig.savefig(output/"fixture-calibration.svg", metadata={"Date": None})
    plt.close(fig)
    fig, axes = plt.subplots(1,2,figsize=(9,3.8),layout="constrained")
    compare = scores.get("frozen_ridge_comparison", {}).get("metrics", scores["point_groups"]["all"])
    names = list(compare)
    axes[0].bar(names, [compare[n]["mae"] for n in names], color=["#747c86", "#27699a", "#d68031"][:len(names)])
    axes[0].set(ylabel="Points MAE (lower is better)", title=f"Paired eligible player GWs (N={compare[names[0]]['n']:,})", ylim=(0,2.6))
    compare = scores["point_groups"]["regular_starters"]
    axes[1].bar(list(compare), [compare[n]["mae"] for n in compare], color=["#27699a", "#d68031"])
    axes[1].set(ylabel="Points MAE", title="Regular starters: components are worse", ylim=(0,2.6))
    fig.suptitle("2024/25 diagnostic simulation — no untouched test claim")
    fig.savefig(output/"fixture-points-comparison.svg", metadata={"Date": None})
    plt.close(fig)
    print("Wrote two validation figures; no model changes.")


if __name__ == "__main__":
    main()
