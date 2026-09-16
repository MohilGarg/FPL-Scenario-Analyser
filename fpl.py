"""Convenience launcher for running the project without installing it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fpl_forfeit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
