"""Optional real-browser product check. Run local API/site first; see README.

Uses the existing demos, not a second scoring implementation or a new demo system.
Playwright is a developer-only dependency and is not needed to build/deploy the site.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
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
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors: list[str] = []
        requests: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(args.url + "/?demo=late&view=overview")
        page.locator("#analysis:not(.hidden)").wait_for()
        page.wait_for_function(
            "appState.data.managers.filter(m => m.status !== 'safe').every(m => m.search.loaded)"
        )
        assert page.locator("#demo-select").is_hidden(), "Demo should live inside Settings"
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        cases = {
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
        for value, expected in cases.items():
            assert page.evaluate("value => FPLInput.parseLeagueId(value)", value) == expected, value
        page.locator("#overview-content .show-more").first.click()
        page.wait_for_function("appState.data.managers[0].search.requested_count === 6")
        assert (
            page.locator("#overview-content .risk-card").first.locator(".scenario-card").count() > 3
        )
        for width in (320, 390, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            for view in ("overview", "scenarios", "differentials", "managers"):
                page.locator(f"#tab-{view}").click()
                if view == "managers":
                    page.locator(".manager-card > summary").first.click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    width,
                    view,
                )
            page.locator("#tab-overview").click()
            page.screenshot(path=str(output / f"overview-{width}.png"), full_page=True)
        page.locator("#tab-scenarios").click()
        page.locator("#comparison-panel > summary").click()
        page.locator("#comparison-result strong").wait_for()
        assert "effective points" in page.locator("#comparison-result").inner_text()
        start = len(requests)
        page.locator("#scenario-detail .show-more").click()
        page.wait_for_function(
            "appState.data.managers.find(m => m.entry_id === appState.selectedManager).search.requested_count === 9"
        )
        more_requests = requests[start:]
        assert any("/managers/1/scenarios" in url and "scenarios=9" in url for url in more_requests)
        assert not any("detail=summary" in url for url in more_requests)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page.locator("#scenario-detail .copy-button").first.click()
        assert "For Alex to finish last" in page.evaluate("navigator.clipboard.readText()")
        assert "view=scenarios" in page.url
        page.locator(".settings-menu > summary").click()
        page.locator("#theme-toggle").click()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.locator(".settings-menu > summary").click()
        page.screenshot(path=str(output / "scenarios-dark.png"), full_page=True)
        page.goto(args.url + "/?demo=complete")
        page.locator(".completed-card").wait_for()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        assert "finishes last" in page.locator(".completed-card").inner_text()
        assert page.locator(".demo-button").count() == 0
        assert page.locator(".completed-card .scenario-card").count() == 0
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "completed-mobile.png"), full_page=True)
        page.locator("#change-league-button").click()
        page.locator("#league-id").fill("https://example.com/leagues/188263/standings/c/")
        page.locator("#league-form button").click()
        assert page.locator("#error-state").is_visible()
        assert page.locator("#retry-button").is_hidden()
        assert not errors, errors
        print(
            f"Browser checks passed: input parsing, four views at 320/390/1440px, themes, comparison, manager-only show more, completed UX. Screenshots: {output}"
        )
        browser.close()


if __name__ == "__main__":
    main()
