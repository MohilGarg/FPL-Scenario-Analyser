from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent
SOURCE_DIR = FRONTEND_DIR / "src"
DEFAULT_OUTPUT_DIR = FRONTEND_DIR / "dist"


def build(output_dir: Path, api_base_url: str, default_league_id: str) -> None:
    output = output_dir.resolve()
    source = SOURCE_DIR.resolve()
    if output == Path(output.anchor) or output in (
        source,
        FRONTEND_DIR.resolve(),
        FRONTEND_DIR.parent.resolve(),
    ):
        raise ValueError(f"Unsafe frontend output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for source_item in source.iterdir():
        destination = output / source_item.name
        if source_item.is_dir():
            shutil.copytree(source_item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source_item, destination)
    config = {
        "apiBaseUrl": api_base_url.rstrip("/"),
        "defaultLeagueId": default_league_id,
    }
    (output / "config.js").write_text(
        "window.APP_CONFIG = " + json.dumps(config, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (output / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the static GitHub Pages frontend")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", ""))
    parser.add_argument(
        "--default-league-id",
        default=os.getenv("DEFAULT_LEAGUE_ID", "188263"),
    )
    args = parser.parse_args()
    build(args.output, args.api_base_url, args.default_league_id)
    print(f"Built frontend at {args.output.resolve()}")
    if not args.api_base_url:
        print("Warning: API_BASE_URL is empty; the UI will show a configuration message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
