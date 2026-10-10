from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from fpl_forfeit.api import (
    FPLAccessError,
    FPLAPIError,
    FPLClient,
    FPLNotFoundError,
    state_from_snapshot,
)
from fpl_forfeit.models import Position


class APIParsingTests(unittest.TestCase):
    def test_snapshot_parses_official_points_hits_and_fixture_minutes(self) -> None:
        elements = [
            {"id": player_id, "web_name": f"P{player_id}", "team": player_id, "element_type": kind}
            for player_id, kind in (
                [(1, 1)]
                + [(value, 2) for value in range(2, 7)]
                + [(value, 3) for value in range(7, 12)]
                + [(value, 4) for value in range(12, 15)]
                + [(15, 1)]
            )
        ]
        snapshot = {
            "schema_version": 1,
            "fetched_at": "2026-09-16T12:00:00+00:00",
            "league_id": 123,
            "league_name": "Friends",
            "gameweek": 4,
            "bootstrap": {
                "teams": [{"id": value, "name": f"T{value}"} for value in range(1, 16)],
                "elements": elements,
            },
            "fixtures": [{"id": 44, "team_h": 1, "team_a": 2, "started": True, "finished": False}],
            "live": {
                "elements": [
                    {
                        "id": value,
                        "stats": {
                            "total_points": 7 if value == 1 else 0,
                            "minutes": 61 if value == 1 else 0,
                            "saves": 2 if value == 1 else 0,
                        },
                        "explain": [
                            {
                                "fixture": 44,
                                "stats": [{"identifier": "minutes", "points": 2, "value": 61}],
                            }
                        ]
                        if value == 1
                        else [],
                    }
                    for value in range(1, 16)
                ]
            },
            "standings": [{"entry": 8, "player_name": "A", "entry_name": "A FC"}],
            "picks": {
                "8": {
                    "active_chip": None,
                    "entry_history": {"event_transfers_cost": 4, "points": 63},
                    "picks": [
                        {
                            "element": value,
                            "position": value,
                            "multiplier": 1 if value <= 11 else 0,
                            "is_captain": value == 1,
                            "is_vice_captain": value == 2,
                        }
                        for value in range(1, 16)
                    ],
                }
            },
        }
        state = state_from_snapshot(snapshot)
        self.assertEqual(state.players[1].position, Position.GOALKEEPER)
        self.assertEqual(state.live_scores[1].points, 7)
        self.assertEqual(state.fixture_minutes[(1, 44)], 61)
        self.assertEqual(state.fixture_stats[(1, 44)]["minutes"], 61)
        self.assertEqual(state.fixture_stats[(1, 44)]["saves"], 2)
        self.assertEqual(state.fixture_stats[(2, 44)]["minutes"], 0)
        self.assertEqual(state.managers[0].transfer_cost, 4)
        self.assertEqual(state.managers[0].official_points, 63)


class NativeHTTPTransportTests(unittest.TestCase):
    def client(self):
        client = FPLClient()
        client._curl = "curl.exe"
        return client

    def test_json_tls_and_timeouts_use_argument_list_without_shell(self):
        with patch(
            "fpl_forfeit.api.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b'{"ok":true}\n200', b""),
        ) as run:
            result = self.client()._get("bootstrap-static/")
        self.assertEqual(result, {"ok": True})
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("--insecure", args[0])
        self.assertNotIn("shell", kwargs)
        self.assertIn("--max-time", args[0])
        self.assertEqual(kwargs["timeout"], 23)

    def test_http_errors_keep_fpl_semantics(self):
        for status, error in ((404, FPLNotFoundError), (403, FPLAccessError), (500, FPLAPIError)):
            with (
                self.subTest(status=status),
                patch(
                    "fpl_forfeit.api.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        [], 0, f"{{}}\n{status}".encode(), b""
                    ),
                ),
                self.assertRaises(error),
            ):
                self.client()._get("test/")

    def test_timeout_and_bad_json_are_explicit_errors(self):
        for value in (
            subprocess.CompletedProcess([], 28, b"", b"timeout"),
            subprocess.CompletedProcess([], 0, b"not json\n200", b""),
        ):
            with (
                patch("fpl_forfeit.api.subprocess.run", return_value=value),
                self.assertRaises(FPLAPIError),
            ):
                self.client()._get("test/")
        with (
            patch("fpl_forfeit.api.subprocess.run", side_effect=subprocess.TimeoutExpired([], 23)),
            self.assertRaises(FPLAPIError),
        ):
            self.client()._get("test/")


if __name__ == "__main__":
    unittest.main()
