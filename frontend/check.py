"""Optional real-browser product check. Run the local API and site first; see README.

Exercises the neutral home page, league-ID parsing, the Live page (standings, SAFE / AT RISK,
squad click-through, separating players or final result), the Season view, both themes and
phone/desktop widths against a real public league. Playwright is a developer-only dependency.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

PARSE_CASES = {
    "188263": "188263",
    " 00188263 ": "188263",
    "https://fantasy.premierleague.com/leagues/188263/standings/c/": "188263",
    "https://fantasy.premierleague.com/en/leagues/188263/standings/c/?x=1": "188263",
    "fantasy.premierleague.com/leagues/188263/standings/c": "188263",
    "0": "",
    "-1": "",
    "YOUR_LEAGUE_ID": "",
    "https://example.com/leagues/188263/standings/c/": "",
    "https://fantasy.premierleague.com/leagues/not-a-number/standings/c/": "",
    "https://fantasy.premierleague.com/leagues/0/standings/c/": "",
    "https://fantasy.premierleague.com/leagues/123/standings/h/": "",
}


def fits(page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def check_home(page, url: str, output: Path, requests: list[str]) -> None:
    page.goto(url + "/")
    page.locator("#league-form").wait_for()
    assert page.locator("#recent-leagues").is_hidden()
    assert not any("/api/" in r for r in requests), "Root must not auto-load a league"
    page.evaluate(
        "() => localStorage.setItem('fpl-recent-leagues', JSON.stringify([{id:'188263',name:'Recent test league'}]))"
    )
    requests.clear()
    page.reload()
    page.locator("#recent-leagues:not(.hidden)").wait_for()
    assert not any("/api/" in r for r in requests), "Remembered league must not auto-load"
    assert page.locator("#league-id").input_value() == ""
    assert page.locator("#demo-select").count() == 0
    for value, expected in PARSE_CASES.items():
        assert page.evaluate("v => FPLInput.parseLeagueId(v)", value) == expected, value
    page.locator("#league-id").fill("https://example.com/leagues/188263/standings/c/")
    page.locator("#league-form button").click()
    assert page.locator("#error-state").is_visible()
    assert page.locator("#retry-button").is_hidden()
    page.screenshot(path=str(output / "home-error-mobile.png"), full_page=True)


def check_live(page, url: str, league: int, output: Path, gameweek: int | None = None) -> None:
    suffix = f"&gw={gameweek}" if gameweek else ""
    page.goto(url + f"/?league={league}{suffix}")
    page.locator("#live-content .manager-card").first.wait_for(timeout=120_000)
    tabs = page.locator(".view-tabs [role=tab]").evaluate_all("t => t.map(x => x.dataset.view)")
    assert tabs == ["live", "season"], tabs
    assert page.locator("#assistant-tab").get_attribute("href").endswith("/assistant/")
    data = page.evaluate("appState.data")
    cards = page.locator("#live-content .manager-card")
    assert cards.count() == len(data["managers"])
    complete = data["league"]["status"] == "complete"
    if complete:
        assert page.locator(".completed-card").count() == 1
        assert "last" in page.locator(".completed-card h2").inner_text()
        assert page.locator(".live-summary, .differential-table, .model-note").count() == 0
    else:
        at_risk = sum(m["status"] == "at_risk" for m in data["managers"])
        assert "can still finish last" in page.locator("#live-headline").inner_text()
        assert page.locator("#live-content > .section-block .status-risk").count() == at_risk
        note = page.locator(".model-note")
        note.locator("summary").click()
        assert "-10" in note.inner_text() and "+35" in note.inner_text()
        if not data["summary"]["last_place_decided"]:
            heading = page.locator("#separators-heading")
            assert heading.inner_text() == "What separates the bottom"
            rows = page.locator(".differential-table tbody tr")
            assert rows.count() == len(data["differentials"]), rows.count()
            assert rows.nth(min(11, rows.count() - 1)).is_visible()
    cards.first.locator("summary").click()
    squad = cards.first.locator(".squad-table tbody tr")
    assert squad.count() == 15, squad.count()
    if not complete:
        assert "under the envelope" in cards.first.locator(".safety-line").inner_text()
    for width in (320, 390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for theme in ("light", "dark"):
            page.evaluate("t => setTheme(t)", theme)
            assert fits(page), (width, theme, "live")
            if width in (390, 1440):
                name = f"live-{'final' if complete else 'live'}-{width}-{theme}.png"
                page.screenshot(path=str(output / name), full_page=True)
    page.evaluate("setTheme('light')")


def check_season(page, url: str, league: int, output: Path) -> None:
    page.locator("#tab-season").click()
    page.locator("#analytics-nav:not(.hidden)").wait_for()
    page.locator("#analytics-content .analytics-card, #analytics-content table").first.wait_for(
        timeout=120_000
    )
    assert "view=season" in page.url
    for section in ("summary", "gameweeks", "managers", "players", "head-to-head"):
        page.locator(f"#analytics-nav [data-section='{section}']").click()
        page.wait_for_function("!document.querySelector('#analytics-content .analytics-loading')")
        assert page.locator("#analytics-retry").count() == 0, section
        for width in (390, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            assert fits(page), (width, section)
    page.locator("#analytics-nav [data-section='gameweeks']").click()
    page.wait_for_function("!document.querySelector('#analytics-content .analytics-loading')")
    assert "Ranked" in page.locator("#analytics-content").inner_text()
    page.screenshot(path=str(output / "season-gameweeks-1440.png"), full_page=True)
    page.locator("#tab-live").click()
    page.locator("#live-content .manager-card").first.wait_for(timeout=120_000)
    assert "view=live" in page.url and "section=" not in page.url
    page.goto(url + f"/?league={league}&view=analytics")  # old shared links still open Season
    page.locator("#analytics-nav:not(.hidden)").wait_for(timeout=120_000)
    assert page.locator("#tab-season").get_attribute("aria-selected") == "true"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--league", type=int, default=188263, help="Real public classic league")
    parser.add_argument(
        "--final-gameweek", type=int, help="Also check a completed Gameweek's final result"
    )
    parser.add_argument(
        "--live-payload",
        type=Path,
        help="Replay a saved in-progress API payload (produced by the Python analysis) for Live",
    )
    parser.add_argument(
        "--browser-executable",
        default=(
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            if os.name == "nt"
            else None
        ),
    )
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="fpl-browser-"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser_executable)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        errors: list[str] = []
        requests: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        check_home(page, args.url, output, requests)
        if args.live_payload:
            body = args.live_payload.read_text(encoding="utf-8")
            pattern = f"**/api/league/{args.league}?*"
            page.route(
                pattern, lambda route: route.fulfill(content_type="application/json", body=body)
            )
            check_live(page, args.url, args.league, output)
            page.unroute(pattern)
        check_live(page, args.url, args.league, output)
        if args.final_gameweek:
            check_live(page, args.url, args.league, output, args.final_gameweek)
        check_season(page, args.url, args.league, output)
        assert not errors, errors
        browser.close()
    print(f"Browser checks passed for league {args.league}. Screenshots: {output}")


if __name__ == "__main__":
    main()
