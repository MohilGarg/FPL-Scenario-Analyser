from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .archive import policy, unpack_record
from .evaluate import from_releases, markdown
from .github import GitHubStore
from .run import RetryClient, run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Immutable prospective FPL recording; no fitting or personal data"
    )
    parser.add_argument(
        "command", choices=("record", "check", "sync", "evaluate", "recover", "extract")
    )
    parser.add_argument("--repo", default=policy()["repository"])
    parser.add_argument("--root", type=Path, default=Path("ml/artifacts/prospective-2026-27"))
    parser.add_argument("--gh-executable", default=os.getenv("GH_EXECUTABLE", "gh"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument(
        "--new-actuals-only",
        action="store_true",
        help="Workflow optimisation: evaluate only actuals published by this run",
    )
    args = parser.parse_args()
    store = GitHubStore(args.repo, executable=args.gh_executable)
    args.root.mkdir(parents=True, exist_ok=True)
    if args.command in ("record", "check"):
        if os.getenv("GITHUB_ACTIONS") == "true" and (
            os.getenv("GITHUB_REPOSITORY") != policy()["repository"]
            or os.getenv("GITHUB_REF") != "refs/heads/main"
        ):
            raise ValueError("Recording workflow must run on this repository's default branch")
        report = run(store, RetryClient(), args.root, check=args.command == "check")
        print(json.dumps(report, indent=2))
        lines = [
            "## Prospective recording",
            "",
            f"State: **{report['state']}**; due slot: {report.get('due_slot')}",
            "",
        ]
        lines.extend(f"- Published: {url}" for url in report["published"])
        lines.extend(f"- ERROR: {error}" for error in report.get("errors", []))
        lines.extend(f"- GAP: {gap}" for gap in report["gaps"])
        lines.extend(
            [
                "",
                "Recovery: see ml/reports/PROSPECTIVE_RECORDING.md. No post-deadline reconstruction.",
            ]
        )
        summary = "\n".join(lines) + "\n"
        (args.root / "last-run.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        if path := os.getenv("GITHUB_STEP_SUMMARY"):
            with Path(path).open("a", encoding="utf-8") as stream:
                stream.write(summary)
        for error in report.get("errors", []):
            print(f"::error::{error}")
        for gap in report["gaps"]:
            print(f"::warning::{gap}")
        if report["state"] == "failed":
            raise SystemExit(1)
    elif args.command == "evaluate":
        gameweeks = None
        if args.new_actuals_only:
            last = json.loads((args.root / "last-run.json").read_bytes())
            gameweeks = last.get("new_actual_gameweeks", [])
            if not gameweeks:
                print("No new official-final actuals this run; no historical downloads")
                return
        result = from_releases(store, args.root, gameweeks=gameweeks)
        text = markdown(result)
        print(text)
        (args.root / "evaluation.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        (args.root / "evaluation.md").write_text(text, encoding="utf-8")
        if path := os.getenv("GITHUB_STEP_SUMMARY"):
            with Path(path).open("a", encoding="utf-8") as stream:
                stream.write(text)
    elif args.command == "sync":
        for release in store.releases():
            if not release["draft"]:
                store.fetch(release, args.root / "archive")
        print("Downloaded and verified immutable recording releases; existing files retained")
    elif args.command == "recover":
        if not args.bundle:
            parser.error("recover requires --bundle pointing to the exact original candidate")
        release = store.recover(args.bundle, args.root / "archive")
        print(release["html_url"])
    else:
        if not args.bundle:
            parser.error("extract requires --bundle")
        import zipfile

        from fpl_forfeit.assistant.prospective import immutable

        from .archive import read_bundle

        manifest, _ = read_bundle(args.bundle)
        with zipfile.ZipFile(args.bundle) as archive:
            body = archive.read(manifest["record_file"])
        unpack_record(body)
        target = args.root / manifest["record_file"]
        if not immutable(target, body) and target.read_bytes() != body:
            raise ValueError("Refusing to overwrite extracted capture")
        print(target)


if __name__ == "__main__":
    main()
