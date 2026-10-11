"""Optional browser check with an in-process API and synthetic correctness fixtures.

The random test password exists only in memory. No test mode is exposed by production.
Run from the repository root with PYTHONPATH=src and Playwright/Edge installed.
"""

from __future__ import annotations

import argparse
import secrets
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from fpl_forfeit.assistant.auth import AuthSettings, SessionStore, password_hash
from fpl_forfeit.assistant.service import AssistantService
from fpl_forfeit.web import create_app
from tests.assistant_fixtures import NOW, FixtureClient, catalogue
from tests.test_web import _FakeService


class BrowserFixtureClient(FixtureClient):
    def forecast_catalogue(self):
        self.catalogue_calls += 1
        source = catalogue()
        roster = dict(source.players)
        for pid, template, price in ((16, 3, 70), (17, 8, 55), (18, 13, 60)):
            roster[pid] = replace(
                roster[template],
                id=pid,
                name=f"Candidate {pid}",
                team_id=8,
                team_name="Club 8",
                price_tenths=price,
            )
        return replace(source, players=roster)


def check(page, origin, password, client, output):
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(origin + "/assistant/")
    page.locator("#assistant-login-form:not(.hidden)").wait_for()
    assert page.locator("#assistant-workspace").is_hidden()
    assert page.request.get(origin + "/api/assistant/predictions").status == 401
    assert client.catalogue_calls == 0
    page.locator("#assistant-password").fill(password)
    page.locator("#assistant-login-form button").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#assistant-prediction-content tbody tr').length===18",
        timeout=30000,
    )
    assert page.locator("#assistant-password").input_value() == ""
    assert client.catalogue_calls == 1
    assert page.request.post(origin + "/api/assistant/refresh", data={}).status == 403
    page.locator("#assistant-tab-predictions").click()
    for horizon in (1, 3, 5, 6):
        page.locator("#assistant-horizon").select_option(str(horizon))
        page.wait_for_function(
            "count=>document.querySelectorAll('[data-assistant-sort^=\"gw-\"]').length===count",
            arg=horizon,
        )
    page.locator("#assistant-search").fill("Candidate")
    assert page.locator("#assistant-prediction-content tbody tr").count() == 3
    page.locator("#assistant-position").select_option("DEF")
    assert page.locator("[data-assistant-player]").inner_text() == "Candidate 16"
    page.locator("[data-assistant-player]").click()
    page.locator("#assistant-player-dialog[open]").wait_for()
    assert "fixture" in page.locator("#assistant-player-detail").inner_text()
    page.locator("#assistant-close-dialog").click()
    page.locator("#assistant-search").fill("")
    page.locator("#assistant-position").select_option("")
    page.locator('[data-assistant-sort="name"]').click()
    page.locator('[data-assistant-sort="name"]').click()
    assert client.catalogue_calls == 1
    page.locator("#assistant-tab-team").click()
    page.locator("#assistant-team-id").fill("42")
    page.locator("#assistant-squad-form button").click()
    page.locator("[data-assistant-use-optimal]").wait_for(timeout=30000)
    assert page.locator(".assistant-pitch").count() == 2
    assert page.locator(".assistant-bench .assistant-player").count() == 8
    assert "last" in page.locator("#assistant-squad-content").inner_text().lower()
    # A DEF -> reserve DEF swap is legal; an outfield player replacing a GK is rejected.
    with page.expect_response(
        lambda r: r.url.endswith("/api/assistant/lineup") and r.request.method == "POST"
    ) as valid:
        page.locator('[data-assistant-swap="3"]').click()
        page.locator('[data-assistant-swap="6"]').click()
    assert valid.value.status == 200
    page.wait_for_function("() => !document.querySelector('#assistant-message').textContent")
    with page.expect_response(
        lambda r: r.url.endswith("/api/assistant/lineup") and r.request.method == "POST"
    ) as invalid:
        page.locator('[data-assistant-swap="1"]').click()
        page.locator('[data-assistant-swap="7"]').click()
    assert invalid.value.status == 422
    page.wait_for_function(
        "() => document.querySelector('#assistant-message').classList.contains('error')"
    )
    page.locator("[data-assistant-use-optimal]").click()
    page.wait_for_function(
        "() => document.querySelector('#assistant-squad-content .assistant-metrics article:last-child strong').textContent==='0'"
    )
    page.locator("#assistant-lineup-gameweek").select_option("12")
    page.wait_for_function(
        "() => document.querySelector('#assistant-squad-content').textContent.includes('16/11/2026')"
    )
    assert page.locator(".assistant-player small").filter(has_text="xPts").count() == 30
    page.locator("#assistant-tab-transfers").click()
    assert page.locator("#assistant-bank").input_value() == ""
    assert page.locator("#assistant-free").input_value() == ""
    page.locator("#assistant-bank").fill("3")
    page.locator("#assistant-free").fill("0")
    page.locator("#assistant-confirm-account").check()
    page.locator("#assistant-transfer-form button[type=submit]").click()
    page.locator("#assistant-transfer-result tbody tr").first.wait_for(timeout=30000)
    assert (
        page.locator(
            "#assistant-transfer-result .assistant-metrics article:first-child strong"
        ).inner_text()
        == "0"
    )
    page.locator("[data-assistant-add-move]").click()
    page.locator('[data-move-field="outgoing"]').select_option("3")
    page.locator('[data-move-field="selling"]').fill("5.1")
    page.locator("[data-assistant-search-transfer]").click()
    page.locator("[data-assistant-select-transfer]").wait_for(timeout=60000)
    assert "heuristic" in page.locator("#assistant-transfer-result").inner_text()
    page.locator("[data-assistant-select-transfer]").click()
    assert page.locator('[data-move-field="incoming"]').input_value() == "16"
    page.locator("#assistant-transfer-form button[type=submit]").click()
    page.wait_for_function(
        "() => document.querySelector('#assistant-transfer-result').textContent.includes('4 point prospective hit')"
    )
    assert (
        page.locator(
            "#assistant-transfer-result .assistant-metrics article:last-child strong"
        ).inner_text()
        == "£1.1m"
    )
    page.locator("#assistant-tab-chips").click()
    page.locator("#assistant-evaluate-chips").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.assistant-chip-grid tbody tr').length===12",
        timeout=60000,
    )
    assert "not definitive" in page.locator("#assistant-chip-content").inner_text()
    assert client.catalogue_calls == 1, "All sections must reuse the same forecast"
    for width in (1440, 390, 320):
        page.set_viewport_size({"width": width, "height": 1000})
        for theme in ("light", "dark"):
            page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            for view in ("team", "predictions", "transfers", "chips"):
                page.locator(f"#assistant-tab-{view}").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    width,
                    theme,
                    view,
                )
                if width in (1440, 390) and view in ("team", "predictions"):
                    page.screenshot(
                        path=str(output / f"{view}-{width}-{theme}.png"), full_page=True
                    )
    assert page.evaluate("Object.keys(localStorage).every(key=>key==='fpl-theme')")
    page.locator("#assistant-logout").click()
    page.locator("#assistant-locked:not(.hidden)").wait_for()
    assert page.locator("#assistant-squad-content").inner_text() == ""
    assert page.locator("#assistant-team-id").input_value() == ""
    assert page.request.get(origin + "/api/assistant/lineup").status == 401
    assert not failures, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-executable")
    parser.add_argument("--output", type=Path, default=Path(".browser-artifacts/assistant"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    password = f"{secrets.randbelow(100_000_000):08d}"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    auth = SessionStore(AuthSettings(origin, password_hash(password), development_http=True))
    client = BrowserFixtureClient()
    service = AssistantService(client, samples=24, now=lambda: NOW)
    application = create_app(_FakeService(), assistant=service, auth=auth)
    server = uvicorn.Server(uvicorn.Config(application, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                **({"executable_path": args.browser_executable} if args.browser_executable else {})
            )
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-GB")
            page = context.new_page()
            check(page, origin, password, client, args.output)
            auth.settings = AuthSettings()
            page.goto(origin + "/assistant/")
            page.wait_for_function(
                "() => document.querySelector('#assistant-access-note').textContent.includes('disabled')"
            )
            assert page.locator("#assistant-login-form").is_hidden()
            assert page.request.get(origin + "/api/league/1").status == 200
            browser.close()
        print(
            "Assistant browser checks passed: login/CSRF/logout, predictions, legal drafts, cached transfers/chips, disabled auth, desktop/mobile and both themes."
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    main()
