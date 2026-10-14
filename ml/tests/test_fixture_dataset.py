from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import timedelta

from fpl_forfeit.api import parse_ml_history
from fpl_forfeit.assistant.component_features import FEATURE_COLUMNS
from ml.fixture_dataset import build
from tests.assistant_fixtures import NOW
from tests.test_fixture_components import context


class HistoricalFixtureDatasetTests(unittest.TestCase):
    def inputs(self):
        _, snapshot, histories = context()
        rows = parse_ml_history(histories[1]["payload"], snapshot["fixtures"], 1, 7, NOW)
        rows.append(
            {
                **rows[-1],
                "fixture_id": 111,
                "gameweek": 7,
                "kickoff_utc": NOW + timedelta(days=2),
                "total_points": 5,
            }
        )
        rows = [
            {**r, "season": "2023-24", "element": 1, "finished": True, "position": "GK"}
            for r in rows
        ]
        raw = deepcopy(snapshot["fixtures"])
        for f in raw:
            f.update(team_h_score=2, team_a_score=1)
        deadlines = {("2023-24", gw): NOW + timedelta(days=7 * (gw - 7)) for gw in range(1, 39)}
        return rows, deadlines, {"2023-24": raw}

    def test_future_results_and_other_season_identity_do_not_change_features(self):
        rows, deadlines, fixtures = self.inputs()
        a, _, coverage = build(rows, set(), deadlines, fixtures, [], ("2023-24",))
        changed = deepcopy(rows)
        changed[-1].update(minutes=0, starts=0, expected_goals=99, total_points=99)
        # An element ID reused next season cannot enter prior-season history.
        changed.append({**changed[0], "season": "2024-25", "minutes": 1, "expected_goals": 999})
        altered = deepcopy(fixtures)
        altered["2023-24"][-1].update(finished=True, team_h_score=99, team_a_score=99)
        b, _, _ = build(changed, set(), deadlines, altered, [], ("2023-24",))
        self.assertEqual(
            {c: a[-1][c] for c in FEATURE_COLUMNS}, {c: b[-1][c] for c in FEATURE_COLUMNS}
        )
        self.assertEqual(a[-1]["schedule_evidence"], "retrospective_final_schedule_proxy")
        self.assertEqual(coverage["2023-24:retrospective_final_schedule_proxy:gameweeks"], 38)

    def test_verified_schedule_conflict_never_silently_reassigned(self):
        rows, deadlines, fixtures = self.inputs()
        archived = deepcopy(fixtures["2023-24"])
        for f in archived:
            # Needed to associate the archive with its original season.
            f["kickoff_time"] = f["kickoff_time"].replace("2026", "2023")
        archived[-2]["event"] = 8  # originally scheduled later at the GW7 cutoff
        found, _, coverage = build(
            rows, set(), deadlines, fixtures, [(NOW - timedelta(hours=2), archived)], ("2023-24",)
        )
        self.assertFalse(any(r["fixture_id"] == 111 for r in found))
        self.assertGreater(coverage["2023-24:archive_assignment_conflicts"], 0)

    def test_transfer_and_ferguson_quality_are_exclusions_not_inputs(self):
        rows, deadlines, fixtures = self.inputs()
        clean, _, _ = build(rows, {("2023-24", 1, 7)}, deadlines, fixtures, [], ("2023-24",))
        self.assertFalse(any(r["gameweek"] == 7 for r in clean))
        rows[-1]["team_id"] = 3
        clean, _, coverage = build(rows, set(), deadlines, fixtures, [], ("2023-24",))
        self.assertFalse(any(r["gameweek"] == 7 for r in clean))
        self.assertEqual(coverage["2023-24:excluded:transfer_proxy"], 1)
        self.assertNotIn("quality_reasons", FEATURE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
