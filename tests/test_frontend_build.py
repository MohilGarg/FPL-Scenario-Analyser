from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from frontend.build import build


class FrontendBuildTests(unittest.TestCase):
    def test_build_injects_public_configuration_and_static_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            build(output, "https://api.example.test/")
            self.assertTrue((output / "index.html").exists())
            self.assertTrue((output / "styles.css").exists())
            self.assertTrue((output / "app.js").exists())
            self.assertTrue((output / "favicon.svg").exists())
            self.assertTrue((output / ".nojekyll").exists())
            config = (output / "config.js").read_text(encoding="utf-8")
            self.assertIn('"apiBaseUrl": "https://api.example.test"', config)
            self.assertNotIn("defaultLeagueId", config)
            self.assertNotIn("188263", config)
            html = (output / "index.html").read_text(encoding="utf-8")
            css = (output / "styles.css").read_text(encoding="utf-8")
            javascript = (output / "app.js").read_text(encoding="utf-8")
            self.assertIn('data-theme="light"', html)
            self.assertIn('id="theme-toggle"', html)
            for view in ("overview", "scenarios", "differentials", "managers", "analytics"):
                self.assertIn(f'data-view="{view}"', html)
            self.assertIn(':root[data-theme="dark"]', css)
            self.assertIn("fpl-theme", javascript)
            self.assertIn("parseLeagueId", javascript)
            self.assertNotIn("defaultLeagueId", javascript)
            self.assertNotIn("rememberedLeague", javascript)


if __name__ == "__main__":
    unittest.main()
