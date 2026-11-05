from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError

from fpl_forfeit.assistant.selection_inputs import (
    available_at,
    club_report,
    collect_calendar,
    competition_matches,
    congestion,
    official_availability,
)
from tests.assistant_fixtures import NOW


def bootstrap():
    return {
        "events": [{"id": 7, "deadline_time": (NOW + timedelta(days=1)).isoformat()}],
        "teams": [{"id": 1}, {"id": 2}],
        "elements": [
            {
                "id": 5,
                "team": 1,
                "status": "d",
                "chance_of_playing_next_round": 50,
                "news": "Uncertain",
                "news_added": (NOW - timedelta(hours=3)).isoformat(),
            }
        ],
    }


class SelectionEvidenceTests(unittest.TestCase):
    def test_official_publication_unknown_and_next_gw_scope_are_preserved(self):
        source = bootstrap()
        evidence = official_availability(source, NOW)
        row = evidence["rows"][0]
        self.assertEqual(row["chance_next_gw"], 50)
        self.assertEqual(row["horizon"], "next_gameweek_only")
        self.assertIsNone(row["return_date"])
        source["elements"][0]["news_added"] = None
        self.assertIsNone(official_availability(source, NOW)["rows"][0]["published_at"])
        source["elements"][0]["news_added"] = (NOW + timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):
            official_availability(source, NOW)

    def test_postdeadline_confirmed_lineup_cannot_enter_predeadline_evaluation(self):
        report = {
            "player_id": 5,
            "season": "2026-27",
            "published_at": (NOW + timedelta(hours=2)).isoformat(),
            "source_url": "https://www.arsenal.com/news/test",
            "fact": "confirmed_lineup",
            "confidence": "confirmed_statement",
        }
        evidence = club_report(
            report, bootstrap(), NOW + timedelta(hours=3), official_domains={"www.arsenal.com"}
        )
        self.assertFalse(available_at(evidence, NOW, NOW + timedelta(hours=1)))
        with self.assertRaises(ValueError):
            club_report(
                {**report, "source_url": "https://www.arsenal.com.evil.test/news"},
                bootstrap(),
                NOW + timedelta(hours=3),
                official_domains={"www.arsenal.com"},
            )

    def test_cross_competition_mapping_and_partial_coverage_do_not_invent_player_minutes(self):
        match = {
            "id": 40,
            "competition": {"code": "CL"},
            "season": {"startDate": "2026-09-01"},
            "homeTeam": {"id": 57},
            "awayTeam": {"id": 66},
            "lastUpdated": (NOW - timedelta(hours=1)).isoformat(),
            "utcDate": (NOW + timedelta(days=2)).isoformat(),
            "status": "SCHEDULED",
        }
        mapping = {"season": "2026-27", "teams": {"57": 1}}
        evidence = competition_matches({"matches": [match]}, bootstrap(), mapping, NOW)
        value = congestion(evidence, 1, NOW + timedelta(days=4), NOW)
        self.assertEqual(value["known_competition_matches_previous_7d"], 1)
        self.assertFalse(value["coverage_complete"])
        self.assertTrue(value["player_minutes_unknown"])
        with self.assertRaises(ValueError):
            competition_matches(
                {"matches": [match]}, bootstrap(), {**mapping, "season": "2025-26"}, NOW
            )

    def test_calendar_retries_and_cache_do_not_store_credentials_or_backdate(self):
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return b'{"matches":[]}'

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 503, "temporary", {}, None)
            return Response()

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, retrieved = collect_calendar(
                "CL", "private-token", root, opener=opener, now=NOW, sleeper=lambda _: None
            )
            self.assertEqual(retrieved, NOW)
            _, again = collect_calendar(
                "CL", "private-token", root, opener=opener, now=NOW + timedelta(minutes=10)
            )
            self.assertEqual(again, NOW)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("private-token", (root / "football-data-CL.json").read_text())

    def test_calendar_retrieval_is_recorded_after_response_completion(self):
        from unittest.mock import patch

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return b'{"matches":[]}'

        completed = NOW + timedelta(minutes=2)
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("fpl_forfeit.assistant.selection_inputs.datetime") as clock,
        ):
            clock.now.side_effect = [NOW, completed]
            _, retrieved = collect_calendar(
                "CL", "private-token", Path(folder), opener=lambda *_args, **_kwargs: Response()
            )
            self.assertEqual(retrieved, completed)


if __name__ == "__main__":
    unittest.main()
