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


def check_simulations(page, url: str, output: Path, requests: list[str]) -> None:
    """Focused UI checks using an existing demo; history fixtures stay inside this browser."""
    page.goto(url + "/?demo=early&view=simulations")
    page.locator("#view-simulations:not(.hidden)").wait_for()
    page.wait_for_function(
        "document.querySelector('.simulation-meta').textContent.includes('existing shared draws')",
        timeout=60_000,
    )
    tabs = page.locator(".view-tabs button").evaluate_all("tabs => tabs.map(t => t.dataset.view)")
    assert tabs == ["overview", "scenarios", "differentials", "simulations", "managers", "analytics"]
    count = page.evaluate("appState.data.managers.length")
    assert page.locator(".simulation-probabilities .bar-row").count() == count
    assert page.locator(".simulation-scores .interval-row").count() == count
    values = page.locator(".simulation-probabilities .bar-row strong").all_inner_texts()
    risks = [float(value.rstrip("%")) for value in values]
    assert risks == sorted(risks, reverse=True), risks
    assert "Sampled 0%" in page.locator(".simulation-probabilities").inner_text()
    assert "no score bins" in page.locator(".simulation-distributions").inner_text()
    assert page.locator(".simulation-history svg").count() == 0
    assert page.locator(".simulation-checkpoints .chart-dot").count() > 0
    page.locator(".simulation-probabilities .bar-row").first.focus()
    assert "estimated last-place probability" in page.locator(".simulation-probabilities .chart-readout").inner_text()
    page.locator(".simulation-scores .interval-row").first.click()
    assert "10th–90th percentile" in page.locator(".simulation-scores .chart-readout").inner_text()
    page.locator(".simulation-checkpoints .chart-dot").first.focus()
    assert "Group 1:" in page.locator(".simulation-checkpoints .chart-readout").inner_text()
    assert "%" in page.locator(".simulation-checkpoints .chart-readout").inner_text()
    start = len(requests)
    page.locator("#simulation-selected-scores").check()
    assert page.locator(".simulation-scores .interval-row").count() == 3
    page.locator("[data-sim-action='none']").click()
    assert page.locator(".simulation-checkpoints .chart-dot").count() == 0
    assert page.locator(".simulation-probabilities .bar-row").count() == count
    page.locator("[data-sim-action='top']").click()
    page.locator(".simulation-checkpoints .chart-legend button").first.click()
    assert page.locator(".simulation-picker [aria-pressed='true']").count() == 2
    assert page.locator(".simulation-scores .interval-row").count() == 2
    assert not any("/outlook" in request for request in requests[start:]), "Selecting charts must not request a forecast"
    page.locator("[data-sim-action='top']").click()
    page.locator("#simulation-selected-scores").uncheck()
    for width in (320, 390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for theme in ("light", "dark"):
            page.evaluate("theme => { document.documentElement.dataset.theme = theme; }", theme)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, theme)
            page.screenshot(path=str(output / f"simulations-{width}-{theme}.png"), full_page=True)
        for view in ("overview", "scenarios", "differentials", "managers", "analytics", "simulations"):
            page.locator(f"#tab-{view}").click()
            assert page.locator(f"#view-{view}").is_visible(), view
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, view)
    # Rendering fixtures only: no invented observations enter the API, saved history or product demos.
    page.evaluate("""() => {
        const results = ForfeitOutlook.simulationData(appState.data);
        const managers = results.forecast.managers;
        window.simulationHistoryFixture = {series_id:'browser-only',configuration:{model_version:'browser-only fixture',simulations:results.forecast.simulations,seed:1,tie_rule:'include'},observations:[{source:'2026-10-11T12:00:00Z',recorded_at:'2026-10-11T12:00:01Z',managers}]};
        FPLSimulations.render(appState.data, {...results,history:{series:[simulationHistoryFixture]}});
    }""")
    assert page.locator(".simulation-history svg").count() == 0
    assert "Only one recorded observation" in page.locator(".simulation-history").inner_text()
    page.evaluate("""() => {
        simulationHistoryFixture.observations.push({...simulationHistoryFixture.observations[0],source:'2026-10-11T12:02:00Z',recorded_at:'2026-10-11T12:02:01Z'});
        FPLSimulations.render(appState.data, {...ForfeitOutlook.simulationData(appState.data),history:{series:[simulationHistoryFixture]}});
    }""")
    assert page.locator(".simulation-history .chart-dot").count() == 6
    page.locator(".simulation-history .chart-dot").first.focus()
    assert "%" in page.locator(".simulation-history .chart-readout").inner_text()
    page.locator(".simulation-history .chart-legend button").first.click()
    assert page.locator(".simulation-history .chart-dot").count() == 4
    page.goto(url + "/?demo=late&view=simulations")
    page.wait_for_function("document.querySelector('.simulation-meta')?.textContent.includes('existing shared draws')", timeout=60_000)
    assert "SAFE in configured envelope" in page.locator(".simulation-probabilities").inner_text()
    page.goto(url + "/?demo=complete&view=simulations")
    page.locator("#view-simulations:not(.hidden)").wait_for()
    assert "Gameweek complete" in page.locator(".simulation-meta").inner_text()
    assert page.locator(".simulation-scores").count() == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--simulations-only", action="store_true", help="Quick demo checks for the Simulations tab, chart interactions, responsive layout and themes")
    parser.add_argument(
        "--league", type=int, help="Also check Analytics against this real public league"
    )
    parser.add_argument(
        "--scenario-league",
        type=int,
        help="Also check candidate scenario loading for a live league",
    )
    parser.add_argument(
        "--phase-leagues",
        type=int,
        nargs=3,
        help="Local validation league IDs with five, three and one fixture remaining",
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
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors: list[str] = []
        requests: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        if args.simulations_only:
            check_simulations(page, args.url, output, requests)
            assert not errors, errors
            browser.close()
            print(f"Simulations browser checks passed: demo charts, manager selection, tooltips, zero/safe labels, unavailable/one/two-observation history, six views, 320/390/1440px, light/dark. Screenshots: {output}")
            return
        page.goto(args.url + "/")
        page.locator("#league-form").wait_for()
        assert page.locator("#recent-leagues").is_hidden()
        assert not any("/api/" in url for url in requests), "Root must not auto-load a league"
        page.evaluate("""() => {
            localStorage.setItem('fpl-last-place-league', '188263');
            localStorage.setItem('fpl-recent-leagues', JSON.stringify([{id:'188263',name:'Recent test league'}]));
        }""")
        requests.clear()
        page.reload()
        page.locator("#recent-leagues:not(.hidden)").wait_for()
        assert not any("/api/" in url for url in requests), "Remembered league must not auto-load"
        assert "188263" in page.locator("#recent-leagues").inner_text()
        assert page.locator("#league-id").input_value() == ""
        page.screenshot(path=str(output / "neutral-home-mobile.png"), full_page=True)
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
        page.locator("#tab-scenarios").click()
        page.locator("#scenario-detail .show-more").first.click()
        page.wait_for_function("appState.data.managers[0].search.requested_count === 6")
        assert page.locator("#scenario-detail .scenario-card").count() > 3
        for width in (320, 390, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            for view in ("overview", "scenarios", "differentials", "simulations", "managers", "analytics"):
                page.locator(f"#tab-{view}").click()
                if view == "managers":
                    page.locator(".manager-card > summary").first.click()
                    assert "Multiplier" not in page.locator(".squad-table").first.inner_text()
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
        page.goto(args.url + "/?demo=early")
        page.locator("#analysis:not(.hidden)").wait_for()
        page.wait_for_function(
            "document.querySelector('.outlook-headlines').textContent.includes('shared-fixture samples')"
        )
        assert "Projected bottom" in page.locator(".outlook-headlines").inner_text()
        assert page.locator(".decisive-card").count() > 0
        for kind in ("goal", "blank", "clean_sheet"):
            page.locator("#tab-overview").click()
            button = page.locator(f".decisive-card [data-explore-kind='{kind}']").first
            player_id = button.get_attribute("data-explore-player")
            fixture_id = button.get_attribute("data-explore-fixture")
            team_id = button.get_attribute("data-explore-team")
            button.click()
            assert page.locator("#what-if-kind").input_value() == kind
            assert page.locator("#what-if-fixture").input_value() == fixture_id
            if kind == "clean_sheet":
                assert page.locator("#what-if-team").input_value() == team_id
            else:
                assert page.locator("#what-if-player").input_value() == player_id
            assert page.locator(".condition-list li").count() == 0
        page.locator("#tab-scenarios").click()
        page.locator("#what-if-fixture").select_option(
            str(page.evaluate("appState.data.outlook.catalog[0].fixture_id"))
        )
        page.locator("#what-if-kind").select_option("goal")
        page.locator("#what-if-player").select_option("9001")
        page.locator("#what-if-form button[type=submit]").click()
        page.wait_for_function(
            "document.querySelector('#explorer-content').textContent.includes('Forced-event estimate ready')"
        )
        assert "Haaland scores" in page.locator(".condition-list").inner_text()
        assert "Projected bottom:" in page.locator("#explorer-content .condition-box").inner_text()
        assert page.locator("#explorer-content .outlook-table tbody tr").count() == 12
        page.locator("#what-if-kind").select_option("clean_sheet")
        page.locator("#what-if-form button[type=submit]").click()
        page.wait_for_function(
            "document.querySelector('.condition-list').children.length === 2 && document.querySelector('#explorer-content').textContent.includes('Forced-event estimate ready')"
        )
        page.locator("#what-if-kind").select_option("blank")
        page.locator("#what-if-form button[type=submit]").click()
        page.locator("#explorer-content [role=alert]").wait_for()
        assert "cannot blank" in page.locator("#explorer-content [role=alert]").inner_text()
        page.locator("#tab-overview").click()
        assert page.locator("#overview-content .outlook-message[role=status]").count() == 0
        page.locator("#tab-scenarios").click()
        page.locator("[data-remove-condition='2']").click()
        page.wait_for_function(
            "document.querySelector('#explorer-content').textContent.includes('Forced-event estimate ready')"
        )
        page.locator("[data-reset-conditions]").click()
        assert page.locator(".condition-list li").count() == 0
        for width in (320, 390, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                "explorer",
                width,
            )
        page.screenshot(path=str(output / "early-explorer-desktop.png"), full_page=True)
        start = len(requests)
        page.locator("#scenario-detail .show-more").click()
        page.wait_for_function("appState.data.managers.some(m => m.search.loaded)")
        assert page.evaluate("appState.data.managers.filter(m => m.search.loaded).length") == 1
        assert sum("/scenarios?" in url for url in requests[start:]) == 1
        assert page.evaluate(
            "appState.data.managers.find(m => m.search.loaded).search.assumptions_note"
        )
        page.route(
            "**/outlook?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"Temporary risk service error"}',
            ),
        )
        page.goto(args.url + "/?demo=early")
        page.locator("#analysis:not(.hidden)").wait_for()
        page.wait_for_function(
            "document.querySelector('.outlook-message')?.textContent.includes('Temporary risk service error')"
        )
        assert "Projected bottom" in page.locator(".outlook-headlines").inner_text()
        assert page.locator(".battle-card").count() > 0
        assert "Projected" in page.locator(".battle-card").first.inner_text()
        page.unroute("**/outlook?**")
        page.locator("#refresh-button").click()
        page.wait_for_function(
            "document.querySelector('.outlook-headlines').textContent.includes('shared-fixture samples')"
        )
        assert page.locator(".outlook-message[role=status]").count() == 0
        for remaining, league_id in zip((5, 3, 1), args.phase_leagues or (), strict=False):
            page.goto(args.url + f"/?league={league_id}")
            page.locator("#analysis:not(.hidden)").wait_for()
            page.wait_for_function(
                "document.querySelector('.outlook-headlines').textContent.includes('shared-fixture samples')"
            )
            assert page.locator(".battle-card").count() > 0
            assert page.locator(".decisive-card").count() > 0
            assert page.locator(".next-events h2").inner_text() == "What could change the result?"
            assert page.locator(".fixture-timeline > li").count() > 0
            assert "Bottom at a checkpoint" in page.locator(".fixture-intelligence").inner_text()
            assert "Only forecasts saved at the time" in page.locator(".risk-history").inner_text()
            assert page.locator(".event-card").count() <= 5
            if page.locator("[data-next-event]").count():
                page.locator("[data-next-event]").first.click()
                assert page.locator("#tab-scenarios").get_attribute("aria-selected") == "true"
                assert page.locator("#what-if-fixture").input_value()
                page.locator("#tab-overview").click()
            page.locator(".fixture-timeline details").first.locator("summary").first.click()
            assert (
                "Final forfeit chance"
                in page.locator(".fixture-timeline details").first.inner_text()
            )
            page.locator("[data-timeline-fixture]").first.click()
            assert page.locator("#tab-scenarios").get_attribute("aria-selected") == "true"
            page.locator("#tab-overview").click()
            for width in (320, 390, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    remaining,
                    width,
                )
            page.screenshot(path=str(output / f"{remaining}-fixtures-overview.png"), full_page=True)
            page.locator(".decisive-card [data-explore-kind=goal]").first.click()
            page.locator("#what-if-form button[type=submit]").click()
            page.wait_for_function(
                "document.querySelector('#explorer-content').textContent.includes('Forced-event estimate ready')"
            )
            assert page.locator("#explorer-content .outlook-table tbody tr").count() == 12
        if args.scenario_league:
            page.goto(args.url + f"/?league={args.scenario_league}&view=scenarios")
            page.locator("#analysis:not(.hidden)").wait_for(timeout=120_000)
            if page.evaluate("appState.data.league.status !== 'complete'"):
                page.wait_for_function(
                    "document.querySelector('.outlook-headlines').textContent.includes('shared-fixture samples')",
                    timeout=60000,
                )
                page.locator("#tab-overview").click()
                assert page.locator(".battle-card").count() > 0
                assert page.locator(".decisive-card").count() > 0
                if page.locator(".risk-history .chart-dot").count():
                    page.locator(".risk-history .chart-dot").first.focus()
                    assert "%" in page.locator(".risk-history .chart-readout").first.inner_text()
                    assert (
                        "GW " not in page.locator(".risk-history .chart-readout").first.inner_text()
                    )
                page.screenshot(path=str(output / "official-snapshot-overview.png"), full_page=True)
                page.locator("#tab-scenarios").click()
                page.locator("#what-if-kind").select_option("goal")
                page.locator("#what-if-form button[type=submit]").click()
                page.wait_for_function(
                    "document.querySelector('#explorer-content').textContent.includes('Forced-event estimate ready')",
                    timeout=60000,
                )
                assert page.locator(
                    "#explorer-content .outlook-table tbody tr"
                ).count() == page.evaluate("appState.data.managers.length")
                assert page.evaluate("""() => [...document.querySelectorAll('#explorer-content .outlook-table tbody tr')]
                    .some(row => Math.abs(Number(row.cells[3].textContent)) > 0)""")
                page.locator("[data-reset-conditions]").click()
            if page.evaluate(
                "appState.data.league.status !== 'complete' && appState.data.summary.scenario_search_state !== 'broad'"
            ):
                page.wait_for_function(
                    "appState.data.managers.filter(m => m.status !== 'safe').every(m => m.search.loaded || m.search.error)",
                    timeout=120_000,
                )
                managers = page.evaluate("appState.data.managers")
                assert not any(m["search"].get("error") for m in managers)
                for manager in managers:
                    if manager["status"] != "can_finish_last":
                        continue
                    page.locator("#scenario-manager").select_option(str(manager["entry_id"]))
                    count = page.locator("#scenario-detail .scenario-card").count()
                    assert count == len(manager["scenarios"]) and count > 0
                    assert count == 3 or manager["search"]["shortfall_note"]
                    page.locator("#scenario-detail .scenario-context > summary").first.click()
                    assert page.locator("#scenario-detail .availability-details").count() > 0
                page.screenshot(path=str(output / "live-scenarios.png"), full_page=True)
        if args.league:
            page.goto(
                args.url + f"/?league={args.league}&view=analytics&section=gameweeks&theme=light"
            )
            page.locator("[data-analytics-gw]").first.wait_for(timeout=120_000)
            assert "section=gameweeks" in page.url
            assert page.locator("#analytics-nav [aria-current='page']").inner_text() == "Gameweeks"
            page.wait_for_function(
                "document.querySelector('#analytics-progress').textContent.includes('final squads verified')",
                timeout=300_000,
            )
            page.locator("[data-analytics-gw]").first.click()
            page.locator("#analytics-gw-detail table").wait_for()
            for width in (320, 390, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                for theme in ("light", "dark"):
                    page.evaluate("theme => setTheme(theme)", theme)
                    for section in ("summary", "gameweeks", "managers", "players", "head-to-head"):
                        page.locator(f"#analytics-nav [data-section='{section}']").click()
                        page.wait_for_function(
                            "!document.querySelector('#analytics-content .analytics-loading')"
                        )
                        assert page.locator("#analytics-retry").count() == 0, page.locator(
                            "#analytics-content"
                        ).inner_text()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), (width, theme, section)
                        assert not page.locator("#view-analytics .status-badge").count()
                        if (width, theme, section) in (
                            (390, "light", "summary"),
                            (1440, "light", "summary"),
                            (390, "dark", "players"),
                            (1440, "dark", "managers"),
                        ):
                            page.screenshot(
                                path=str(output / f"analytics-{section}-{width}-{theme}.png"),
                                full_page=True,
                            )
            page.locator("#analytics-nav [data-section='players']").click()
            page.locator("#analytics-player-search").wait_for()
            page.locator("#analytics-ownership-trend .chart").wait_for()
            page.locator("#analytics-player-search").fill("nonexistent-player-zzzz")
            assert "No data available" in page.locator("#analytics-ownership").inner_text()
            page.locator("#analytics-player-search").fill("")
            page.locator("#analytics-similarity button").first.click()
            assert "shared" in page.locator("#analytics-pair").inner_text()
            page.locator("#analytics-nav [data-section='head-to-head']").click()
            page.locator("#analytics-load-overlap").wait_for()
            page.locator("#analytics-load-overlap").click()
            page.locator("#analytics-h2h-overlap .pair-detail").wait_for()
            page.locator("#analytics-nav [data-section='managers']").click()
            page.locator("#analytics-manager").wait_for()
            page.locator("#analytics-manager").select_option(index=1)
            page.wait_for_function(
                "!document.querySelector('#analytics-content .analytics-loading')"
            )
            page.locator(".chart-dot").first.click()
            assert "GW" in page.locator(".chart-readout").first.inner_text()
            page.locator("#tab-overview").click()
            page.locator("#overview-content h2").first.wait_for(timeout=120_000)
            assert "league=" in page.url and "view=overview" in page.url
            assert "section=" not in page.url
            page.locator(".brand").click()
            page.locator("#league-form").wait_for()
            assert "league=" not in page.url
        assert not errors, errors
        print(
            f"Browser checks passed: neutral home (new/recent visitor), input parsing, six views at 320/390/1440px, themes, outlook, combined what-ifs/conflicts/reset, comparison, manager-only show more, completed UX, event shortcuts and error recovery. Phase leagues: {args.phase_leagues}. Analytics league: {args.league}. Scenario league: {args.scenario_league}. Screenshots: {output}"
        )
        browser.close()


if __name__ == "__main__":
    main()
