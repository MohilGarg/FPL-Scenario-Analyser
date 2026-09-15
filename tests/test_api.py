from __future__ import annotations

import unittest

from fpl_forfeit.api import state_from_snapshot
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
            "fixtures": [
                {"id": 44, "team_h": 1, "team_a": 2, "started": True, "finished": False}
            ],
            "live": {
                "elements": [
                    {
                        "id": value,
                        "stats": {"total_points": 7 if value == 1 else 0, "minutes": 61 if value == 1 else 0},
                        "explain": [
                            {
                                "fixture": 44,
                                "stats": [{"identifier": "minutes", "points": 2, "value": 61}],
                            }
                        ] if value == 1 else [],
                    }
                    for value in range(1, 16)
                ]
            },
            "standings": [{"entry": 8, "player_name": "A", "entry_name": "A FC"}],
            "picks": {
                "8": {
                    "active_chip": None,
                    "entry_history": {"event_transfers_cost": 4},
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
        self.assertEqual(state.managers[0].transfer_cost, 4)


if __name__ == "__main__":
    unittest.main()

