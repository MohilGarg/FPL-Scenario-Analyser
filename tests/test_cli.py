from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fpl_forfeit.api import FPLAPIError
from fpl_forfeit.cli import configured_league_id


class ConfigTests(unittest.TestCase):
    def test_reads_saved_league_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"league_id": 188263}), encoding="utf-8")
            self.assertEqual(configured_league_id(None, path), 188263)

    def test_command_line_value_overrides_config(self) -> None:
        self.assertEqual(configured_league_id(42, Path("missing.json")), 42)

    def test_invalid_saved_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"league_id": "not-a-number"}), encoding="utf-8")
            with self.assertRaises(FPLAPIError):
                configured_league_id(None, path)


if __name__ == "__main__":
    unittest.main()
