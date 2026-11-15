from __future__ import annotations

import unittest

import httpx

from fpl_forfeit.api import FPLDataError, FPLNotFoundError
from fpl_forfeit.web import create_app


class _FakeService:
    def __init__(self) -> None:
        self.last_settings = None

    def analyse_league(
        self, league_id: int, settings: object = None, gameweek: int | None = None
    ) -> dict[str, object]:
        self.last_settings = settings
        self.last_gameweek = gameweek
        if league_id == 404:
            raise FPLNotFoundError("League not found")
        return {
            "api_version": "1",
            "league": {"id": league_id, "name": "Friends", "gameweek": 1},
            "summary": {"current_last_entry_ids": [1]},
            "managers": [],
            "differentials": [],
            "model": {},
        }


class WebAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = _FakeService()
        transport = httpx.ASGITransport(app=create_app(self.service))  # type: ignore[arg-type]
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_health_endpoint(self) -> None:
        response = await self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("forecast_model", response.json())

    async def test_serialises_processed_league_result(self) -> None:
        response = await self.client.get("/api/league/188263")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["league"]["id"], 188263)
        self.assertIn("summary", response.json())

    async def test_zero_league_id_is_rejected(self) -> None:
        response = await self.client.get("/api/league/0")
        self.assertEqual(response.status_code, 400)
        self.assertIn("positive integer", response.json()["detail"])

    async def test_non_numeric_league_id_is_rejected(self) -> None:
        response = await self.client.get("/api/league/not-a-number")
        self.assertEqual(response.status_code, 422)

    async def test_missing_public_league_returns_not_found(self) -> None:
        response = await self.client.get("/api/league/404")
        self.assertEqual(response.status_code, 404)

    async def test_strict_tie_setting_and_gameweek_are_passed_through(self) -> None:
        response = await self.client.get("/api/league/188263?ties=strict&gameweek=4")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.service.last_settings.allow_tied_last)
        self.assertEqual(self.service.last_gameweek, 4)

    async def test_retired_routes_are_gone(self) -> None:
        for path in (
            "/api/demo/live",
            "/api/league/1/outlook?source=x",
            "/api/league/1/compare?a=1&b=2",
            "/api/league/1/managers/1/scenarios",
            "/api/league/1/risk-history?gameweek=1",
        ):
            with self.subTest(path=path):
                self.assertEqual((await self.client.get(path)).status_code, 404)


if __name__ == "__main__":
    unittest.main()


class _BrokenService(_FakeService):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def analyse_league(
        self, league_id: int, settings: object = None, gameweek: int | None = None
    ) -> dict[str, object]:
        raise self.error


class ErrorMappingTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, error: Exception) -> httpx.Response:
        app = create_app(_BrokenService(error))  # type: ignore[arg-type]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/api/league/188263")

    async def test_fpl_parsing_failure_is_a_502(self) -> None:
        response = await self.request(FPLDataError("Unexpected FPL data: KeyError: 'id'"))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"], "FPL returned data the analyser could not process."
        )

    async def test_internal_bugs_are_not_blamed_on_fpl(self) -> None:
        for error in (KeyError("x"), TypeError("x"), ValueError("x")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                await self.request(error)
