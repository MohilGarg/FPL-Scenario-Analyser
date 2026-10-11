"""Local browser rehearsal with labelled synthetic phases and an optional recorded real league."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from fpl_forfeit.api import FPLClient, FPLNotFoundError, load_snapshot
from fpl_forfeit.service import AnalysisService
from fpl_forfeit.web import create_app
from tests.validation_states import validation_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    raw = load_snapshot(args.snapshot) if args.snapshot else None

    class RecordedClient(FPLClient):
        def player_history(self, player_id):
            return (raw or {}).get("player_histories", {}).get(str(player_id), {})

        def fetch_snapshot(self, league_id, gameweek=None, **kwargs):
            if raw and league_id == raw["league_id"] and gameweek in (None, raw["gameweek"]):
                return raw
            raise FPLNotFoundError(
                "This local rehearsal only serves its recorded and synthetic inputs."
            )

    service = AnalysisService(RecordedClient(), cache_ttl_seconds=600)
    for remaining in (5, 3, 1):
        state = validation_state(remaining)
        state.league_id = 900000 + remaining
        state.league_name = f"Synthetic validation: {remaining} fixtures remaining"
        source = state.fetched_at.isoformat()
        snapshot = {"league_id": state.league_id, "fetched_at": source}
        service._snapshots.set((state.league_id, None), snapshot)
        service._generations.set((state.league_id, None, source), snapshot)
        service._states.set((state.league_id, None, source), state)
    uvicorn.run(create_app(service), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
