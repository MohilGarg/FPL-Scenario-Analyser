from __future__ import annotations

import unittest

import httpx

from fpl_forfeit.api import FPLNotFoundError
from fpl_forfeit.web import create_app


class _FakeService:
    def analyse_league(self, league_id: int, settings: object = None) -> dict[str, object]:
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
        transport = httpx.ASGITransport(app=create_app(_FakeService()))  # type: ignore[arg-type]
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_health_endpoint(self) -> None:
        response = await self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_serialises_processed_league_result(self) -> None:
        response = await self.client.get("/api/league/188263?scenarios=6")
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


if __name__ == "__main__":
    unittest.main()
