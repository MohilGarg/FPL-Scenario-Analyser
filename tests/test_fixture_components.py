from __future__ import annotations

import gzip
import json
import random
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from fpl_forfeit.api import FPLGameweekUnavailable, parse_ml_history
from fpl_forfeit.assistant.component_features import (
    FEATURE_COLUMNS,
    player_features,
    team_features,
)
from fpl_forfeit.assistant.component_forecast import ComponentSimulator, build_component_forecast
from fpl_forfeit.assistant.component_model import ComponentModel
from fpl_forfeit.assistant.ml_inference import RidgeModel, predict_player
from fpl_forfeit.assistant.prospective import ForecastArchive, canonical
from fpl_forfeit.assistant.service import AssistantService
from fpl_forfeit.forecasting.horizon import forecast_horizon
from fpl_forfeit.forecasting.statistics import player_rates
from fpl_forfeit.models import Fixture
from ml.recording.archive import create_bundle, read_bundle
from ml.recording.evaluate import evaluate
from tests.assistant_fixtures import NOW, FixtureClient, catalogue
from tests.test_live_ridge import source


def context():
    snapshot, history = source()
    snapshot["source_started_utc"] = NOW.isoformat()
    old, future = snapshot["fixtures"]
    old.update(team_h_score=2, team_a_score=1)
    snapshot["fixtures"] = [
        {
            **old,
            "id": 100 + i,
            "event": 4 + i,
            "kickoff_time": (NOW - timedelta(days=21 - 7 * i)).isoformat(),
        }
        for i in range(3)
    ]
    history["payload"]["history"] = [
        {
            **history["payload"]["history"][0],
            "round": 4 + i,
            "fixture": f["id"],
            "kickoff_time": f["kickoff_time"],
        }
        for i, f in enumerate(snapshot["fixtures"])
    ]
    snapshot["fixtures"] += [
        {**future, "id": 111 + i, "kickoff_time": (NOW + timedelta(days=2 + 3 * i)).isoformat()}
        for i in range(2)
    ]
    cat = replace(
        catalogue(),
        fixtures=tuple(
            Fixture(
                f["id"], 7, 1, 2, "Club 1", "Club 2", False, False, NOW + timedelta(days=2 + 3 * i)
            )
            for i, f in enumerate(snapshot["fixtures"][-2:])
        ),
    )
    return forecast_horizon(cat, (7, 8), samples=8), snapshot, {1: history}


class FixtureComponentsTests(unittest.TestCase):
    def test_two_minute_scoring_outlier_does_not_drive_playing_time(self):
        baseline, snapshot, histories = context()
        for r in histories[1]["payload"]["history"]:
            r.update(minutes=0, starts=0, goals_scored=0, expected_goals=0)
        histories[1]["payload"]["history"][-1].update(
            minutes=2, goals_scored=1, expected_goals=0.15
        )
        model = ComponentModel()
        model.data["config"]["history_min_minutes"] = 90
        _, meta = build_component_forecast(baseline, snapshot, histories, NOW, model=model)
        record = next(r for r in meta["fixture_metadata"] if r["player_id"] == 1)
        self.assertEqual(record["learned_components"], [])
        self.assertIn("statistical_insufficient_minutes_for_per90_features", record["fallbacks"])

    def test_missing_fixture_context_and_unassigned_schedule_are_explicit(self):
        baseline, snapshot, histories = context()
        snapshot["fixtures"][-1]["kickoff_time"] = None
        result, meta = build_component_forecast(baseline, snapshot, histories, NOW)
        records = [m for m in meta["fixture_metadata"] if m["fixture_id"] == 112]
        self.assertTrue(records)
        self.assertTrue(
            all("statistical_missing_fixture_context" in m["fallbacks"] for m in records)
        )
        uncertain = replace(
            baseline.catalogue, unassigned_team_ids=frozenset({1}), schedule_complete=False
        )
        baseline = forecast_horizon(uncertain, (7, 8), samples=4)
        result, _ = build_component_forecast(baseline, snapshot, histories, NOW)
        self.assertIsNone(result.players[1][7]["expected_points"])
        self.assertIsNone(result.players[1][8]["expected_points"])

    def test_probabilities_missing_inputs_and_corrupt_portable_model(self):
        model = ComponentModel()
        values = dict.fromkeys(FEATURE_COLUMNS)
        values["position_GK"] = 1
        proposal = model.proposals(values)
        self.assertAlmostEqual(sum(proposal["state_probabilities"]), 1)
        self.assertTrue(all(0 <= p <= 1 for p in proposal["state_probabilities"]))
        self.assertTrue(0 <= proposal["cameo_probability"] <= 1)
        self.assertTrue(all(1 <= m <= 90 for m in proposal["starting_minutes"]))
        values["minutes5"] = float("inf")
        with self.assertRaises(ValueError):
            model.proposals(values)
        broken = deepcopy(model.data)
        broken["models"]["goals"]["coef"].pop()
        with self.assertRaises(ValueError):
            ComponentModel(data=broken)

    def test_live_feature_timing_future_mutation_and_missing_minutes(self):
        _, snapshot, histories = context()

        def features(s, h):
            rows = parse_ml_history(h[1]["payload"], s["fixtures"], 1, 7, NOW)
            return player_features(rows, "GK"), team_features(s["fixtures"], 7, NOW)

        before = features(snapshot, histories)
        modified, changed = deepcopy(snapshot), deepcopy(histories)
        modified["fixtures"][-1].update(finished=True, team_h_score=99, team_a_score=99)
        changed[1]["payload"]["history"].append({"round": 7, "fixture": 112, "minutes": 999})
        self.assertEqual(before, features(modified, changed))
        changed[1]["payload"]["history"][-2]["minutes"] = None
        self.assertIsNone(features(snapshot, changed)[0]["xg90"])
        modified["fixtures"][2]["kickoff_time"] = (NOW - timedelta(hours=23)).isoformat()
        self.assertEqual(team_features(modified["fixtures"], 7, NOW)[1]["count"], 2)

    def test_shared_adapter_reproducibility_double_and_blank_aggregation(self):
        baseline, snapshot, histories = context()
        one, meta = build_component_forecast(baseline, snapshot, histories, NOW)
        two, _ = build_component_forecast(baseline, snapshot, histories, NOW)
        self.assertEqual(one.outcomes, two.outcomes)
        self.assertEqual(baseline.inputs.model_version, "shared-fixtures-v4")
        self.assertEqual(one.inputs.model_version, "fixture-components-v1")
        self.assertEqual(one.players[1][7]["fixture_ids"], [111, 112])
        self.assertEqual(one.players[1][8]["expected_points"], 0)
        self.assertAlmostEqual(
            one.players[1][7]["expected_points"],
            sum(p.expected_points for p in one.fixtures if p.player_id == 1),
        )
        self.assertEqual(len(meta["fixture_metadata"]), 8)
        cold = next(r for r in meta["fixture_metadata"] if r["player_id"] == 2)
        self.assertIn("statistical_missing_or_transferred_history", cold["fallbacks"])
        self.assertTrue(
            next(r for r in meta["fixture_metadata"] if r["player_id"] == 1)["learned_components"]
        )

    def test_unavailable_and_transferred_players_keep_statistical_playing_time(self):
        baseline, snapshot, histories = context()
        baseline.catalogue.players[1] = replace(
            baseline.catalogue.players[1], availability_status="i", chance_of_playing=0
        )
        _, meta = build_component_forecast(baseline, snapshot, histories, NOW)
        row = next(r for r in meta["fixture_metadata"] if r["player_id"] == 1)
        self.assertNotIn("playing_time", row["learned_components"])
        for f in snapshot["fixtures"][:3]:
            f["team_h"] = 3
        _, meta = build_component_forecast(baseline, snapshot, histories, NOW)
        self.assertIn(
            "statistical_missing_or_transferred_history",
            next(r for r in meta["fixture_metadata"] if r["player_id"] == 1)["fallbacks"],
        )

    def test_adapter_keeps_one_score_and_correlated_clean_sheets(self):
        baseline, _, _ = context()
        rates = {
            (f.id, p.id): replace(
                player_rates(p), start=1, starting_minutes=(90,), cameo_probability=0
            )
            for f in baseline.inputs.fixtures
            for p in baseline.catalogue.players.values()
        }
        sim = ComponentSimulator(
            baseline.inputs,
            rates=rates,
            goals={(111, 1): 0, (111, 2): 0},
            version="fixture-components-v1",
        )
        draw = sim.sample_fixture(baseline.inputs.fixtures[0], random.Random(42))
        self.assertTrue(all(s["goals_scored"] == 0 for s in draw.stats.values()))
        self.assertTrue(
            all(s["clean_sheets"] == int(s["minutes"] >= 60) for s in draw.stats.values())
        )

    def test_model_selection_is_request_local_and_missing_model_fails_explicitly(self):
        service = AssistantService(FixtureClient(), samples=4, now=lambda: NOW)
        stat = service.forecast()
        with self.assertRaises(FPLGameweekUnavailable), service.using_model("components"):
            service.forecast()
        self.assertIs(service.forecast(), stat)
        component = replace(
            stat, inputs=replace(stat.inputs, model_version="fixture-components-v1")
        )
        service.ml.generation = (stat.catalogue.fetched_at.isoformat(), stat.gameweeks)
        service.ml.component_forecast = component
        with service.using_model("components"):
            self.assertIs(service.forecast(), component)
        self.assertIs(service.forecast(), stat)

    def test_archive_extension_immutable_and_paired_evaluation(self):
        baseline, snapshot, histories = context()
        component, meta = build_component_forecast(baseline, snapshot, histories, NOW)
        ridge = RidgeModel()
        rows = {
            pid: predict_player(ridge, baseline, snapshot, pid, histories.get(pid), NOW)
            for pid in baseline.players
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = ForecastArchive(root)
            old = archive.record(baseline, snapshot, histories, rows, NOW, ridge)
            raw = old.read_bytes()
            archive.record(
                baseline,
                snapshot,
                histories,
                rows,
                NOW,
                ridge,
                component=component,
                component_metadata=meta,
            )
            self.assertEqual(raw, old.read_bytes())
            later = NOW + timedelta(hours=6)
            new = archive.record(
                baseline,
                snapshot,
                histories,
                rows,
                later,
                ridge,
                feature_asof=NOW,
                component=component,
                component_metadata=meta,
            )
            bundle = create_bundle(new, "advance", 7, {}, root / "capture.zip")
            manifest, record = read_bundle(bundle)
            actuals = {
                7: {
                    "official_final_event": {
                        "finished": True,
                        "data_checked": True,
                        "deadline_time": manifest["deadline_utc"],
                    },
                    "actual_points": {str(pid): 0 for pid in rows},
                }
            }
            scored = evaluate([(manifest, record)], actuals)
            self.assertEqual(
                scored["metrics"]["fixture_component_paired"]["fixture_components"]["n"], len(rows)
            )
            self.assertNotIn("fixture_component", json.loads(gzip.decompress(raw)))
            self.assertEqual(
                record["fixture_component"]["prediction_sha256"],
                __import__("hashlib").sha256(canonical(component.players)).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
