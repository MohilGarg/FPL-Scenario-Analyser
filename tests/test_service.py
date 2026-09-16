from __future__ import annotations

import unittest
from unittest.mock import patch

from fpl_forfeit.service import AnalysisService
from tests.helpers import league_state, manager, scores


class _CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_snapshot(self, league_id: int, **_: object) -> dict[str, object]:
        self.calls += 1
        return {"league_id": league_id}


class ServiceCacheTests(unittest.TestCase):
    def test_repeated_analysis_uses_ttl_cache(self) -> None:
        client = _CountingClient()
        state = league_state((manager(1), manager(2)), scores())
        service = AnalysisService(client, cache_ttl_seconds=60)  # type: ignore[arg-type]
        with patch("fpl_forfeit.service.state_from_snapshot", return_value=state):
            first = service.analyse_league(123)
            second = service.analyse_league(123)
        self.assertEqual(client.calls, 1)
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])

    def test_invalid_league_id_is_rejected_before_fetch(self) -> None:
        client = _CountingClient()
        service = AnalysisService(client, cache_ttl_seconds=60)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            service.analyse_league(0)
        self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()
