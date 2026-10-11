from __future__ import annotations

import secrets
import unittest

import httpx

from fpl_forfeit.assistant.auth import AuthSettings, SessionStore, password_hash
from fpl_forfeit.assistant.service import AssistantService
from fpl_forfeit.web import create_app
from tests.assistant_fixtures import NOW, FixtureClient
from tests.test_web import _FakeService


class AssistantAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.encoded = password_hash(cls.password)

    async def asyncSetUp(self):
        self.time = 1_000.0
        self.store = SessionStore(
            AuthSettings("https://assistant.example", self.encoded, 120, 60),
            clock=lambda: self.time,
        )
        self.fpl = FixtureClient()
        self.service = AssistantService(self.fpl, samples=4, now=lambda: NOW)
        self.app = create_app(_FakeService(), assistant=self.service, auth=self.store)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://assistant.example"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def sign_in(self):
        response = await self.client.post(
            "/api/assistant/login",
            json={"password": self.password},
            headers={"Origin": "https://assistant.example"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf"]

    async def test_all_private_routes_guarded_before_validation_and_unknown_future_routes(self):
        for method, path in [
            ("GET", "predictions"),
            ("GET", "squad"),
            ("GET", "lineup"),
            ("GET", "players/1"),
            ("GET", "chips"),
            ("POST", "squad"),
            ("POST", "lineup"),
            ("POST", "transfers"),
            ("POST", "refresh"),
            ("POST", "logout"),
            ("GET", "future-sensitive-endpoint"),
        ]:
            response = await self.client.request(
                method,
                "/api/assistant/" + path,
                headers={"Origin": "https://assistant.example"},
                json={"invalid": True} if method == "POST" else None,
            )
            self.assertEqual(response.status_code, 401, (path, response.text))
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.fpl.catalogue_calls, 0)
        self.assertEqual(self.fpl.squad_calls, 0)

    async def test_cookie_is_opaque_secure_httponly_host_only_and_not_in_json(self):
        response = await self.client.post(
            "/api/assistant/login",
            json={"password": self.password},
            headers={"Origin": "https://assistant.example"},
        )
        cookie = response.headers["set-cookie"]
        self.assertIn("__Host-fpl-assistant", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertNotIn("token", response.json())
        self.assertNotIn(self.password, response.text)
        self.assertNotIn(self.encoded, response.text)
        self.assertTrue((await self.client.get("/api/assistant/session")).json()["authenticated"])
        self.assertEqual(
            (await self.client.get("/api/assistant/predictions?horizon=1")).status_code, 200
        )

    async def test_csrf_and_cross_origin_posts_rejected(self):
        csrf = await self.sign_in()
        for headers in (
            {"Origin": "https://assistant.example"},
            {"Origin": "https://mohilgarg.github.io", "X-CSRF-Token": csrf},
            {"Origin": "https://assistant.example", "X-CSRF-Token": "invalid"},
            {"X-CSRF-Token": csrf},
        ):
            response = await self.client.post(
                "/api/assistant/squad", json={"entry_id": 42}, headers=headers
            )
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.fpl.squad_calls, 0)
        response = await self.client.post(
            "/api/assistant/squad",
            json={"entry_id": 42},
            headers={"Origin": "https://assistant.example", "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)

    async def test_public_cors_does_not_grant_private_credentials(self):
        await self.sign_in()
        response = await self.client.get(
            "/api/assistant/predictions", headers={"Origin": "https://mohilgarg.github.io"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.headers.get("access-control-allow-credentials"), "true")
        public = await self.client.get(
            "/api/league/1", headers={"Origin": "https://mohilgarg.github.io"}
        )
        self.assertEqual(public.status_code, 200)
        self.assertEqual(
            public.headers["access-control-allow-origin"], "https://mohilgarg.github.io"
        )
        self.assertNotIn("Fixture squad", public.text)
        self.assertNotIn("csrf", public.text)

    async def test_idle_and_absolute_session_expiry(self):
        await self.sign_in()
        self.time += 61
        self.assertEqual((await self.client.get("/api/assistant/squad")).status_code, 401)
        await self.sign_in()
        self.time += 50
        self.assertEqual(
            (await self.client.get("/api/assistant/predictions?horizon=1")).status_code, 200
        )
        self.assertTrue((await self.client.get("/api/assistant/session")).json()["authenticated"])
        self.time += 50
        self.assertEqual(
            (await self.client.get("/api/assistant/predictions?horizon=1")).status_code, 200
        )
        self.assertTrue((await self.client.get("/api/assistant/session")).json()["authenticated"])
        self.time += 21
        self.assertEqual((await self.client.get("/api/assistant/squad")).status_code, 401)

    async def test_passive_status_checks_do_not_extend_idle_sessions(self):
        await self.sign_in()
        self.time += 50
        self.assertTrue((await self.client.get("/api/assistant/session")).json()["authenticated"])
        self.time += 11
        self.assertFalse((await self.client.get("/api/assistant/session")).json()["authenticated"])
        self.assertEqual((await self.client.get("/api/assistant/predictions")).status_code, 401)

    async def test_expiry_during_computation_blocks_result(self):
        await self.sign_in()

        def expire(_):
            self.time += 121
            return {"private_result": "must not be returned"}

        self.service.predictions = expire
        response = await self.client.get("/api/assistant/predictions")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("private_result", response.text)

    async def test_malformed_unicode_password_has_generic_error(self):
        response = await self.client.post(
            "/api/assistant/login",
            content=b'{"password":"\\ud800"}',
            headers={"Origin": "https://assistant.example", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Unable to sign in.")

    async def test_logout_revokes_cookie_and_clears_personal_cache(self):
        csrf = await self.sign_in()
        headers = {"Origin": "https://assistant.example", "X-CSRF-Token": csrf}
        await self.client.post("/api/assistant/squad", json={"entry_id": 42}, headers=headers)
        old = self.client.cookies.get("__Host-fpl-assistant")
        response = await self.client.post("/api/assistant/logout", json={}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.client.cookies.set("__Host-fpl-assistant", old, domain="assistant.example", path="/")
        self.assertEqual((await self.client.get("/api/assistant/squad")).status_code, 401)
        self.assertEqual(len(self.store.sessions), 0)
        self.client.cookies.clear()
        await self.sign_in()
        self.assertEqual((await self.client.get("/api/assistant/squad")).status_code, 422)

    async def test_generic_failure_rate_limit_and_recovery(self):
        for _ in range(5):
            response = await self.client.post(
                "/api/assistant/login",
                json={"password": secrets.token_urlsafe(24)},
                headers={"Origin": "https://assistant.example"},
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "Unable to sign in."})
        response = await self.client.post(
            "/api/assistant/login",
            json={"password": self.password},
            headers={"Origin": "https://assistant.example"},
        )
        self.assertEqual(response.status_code, 429)
        self.time += 601
        await self.sign_in()

    async def test_login_origin_content_type_and_body_bound(self):
        self.assertEqual(
            (
                await self.client.post("/api/assistant/login", json={"password": self.password})
            ).status_code,
            403,
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/api/assistant/login",
                    data={"password": self.password},
                    headers={"Origin": "https://assistant.example"},
                )
            ).status_code,
            403,
        )
        response = await self.client.post(
            "/api/assistant/login",
            content='{"password":"' + "x" * 9000 + '"}',
            headers={"Origin": "https://assistant.example", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    async def test_disabled_or_invalid_auth_never_breaks_public_api(self):
        for settings in (
            AuthSettings(),
            AuthSettings("http://example.com", self.encoded),
            AuthSettings("https://assistant.example", "malformed"),
        ):
            app = create_app(_FakeService(), auth=SessionStore(settings))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://assistant.example"
            ) as client:
                self.assertFalse((await client.get("/api/assistant/session")).json()["enabled"])
                self.assertEqual((await client.get("/api/assistant/predictions")).status_code, 503)
                self.assertEqual((await client.get("/api/health")).status_code, 200)
                self.assertEqual((await client.get("/api/league/1")).status_code, 200)

    async def test_same_origin_dashboard_csp_and_session_cache_isolation(self):
        page = await self.client.get("/assistant/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertNotIn("Fixture squad", page.text)
        first = await self.sign_in()
        await self.client.post(
            "/api/assistant/squad",
            json={"entry_id": 42},
            headers={"Origin": "https://assistant.example", "X-CSRF-Token": first},
        )
        self.client.cookies.clear()
        second = await self.sign_in()
        self.assertNotEqual(first, second)
        self.assertEqual((await self.client.get("/api/assistant/squad")).status_code, 422)
