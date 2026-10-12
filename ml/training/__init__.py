"""Reproducible offline experiments, independent of the production application."""

import os
from pathlib import Path

# Matplotlib can also be imported by LightGBM; keep its cache inside the ML area.
os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "artifacts/matplotlib"))
os.environ.setdefault("MPLBACKEND", "Agg")
