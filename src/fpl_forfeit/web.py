from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .analytics.service import AnalyticsService
from .api import (
    FPLAccessError,
    FPLAPIError,
    FPLGameweekUnavailable,
    FPLLeagueSizeError,
    FPLNotFoundError,
)
from .demo import DEMO_MODES, DemoMode
from .service import AnalysisService
from .settings import (
    DEFAULT_MAX_RELEVANT_PLAYERS,
    DEFAULT_MAX_SEARCH_NODES,
    AnalysisSettings,
)


def create_app(
    service: AnalysisService | None = None, analytics: AnalyticsService | None = None
) -> FastAPI:
    analysis_service = service or AnalysisService()
    analytics_service = analytics or AnalyticsService(
        analysis_service if isinstance(analysis_service, AnalysisService) else None
    )
    application = FastAPI(
        title="FPL Scenario Analyser API",
        version="3.0.0",
        description="Live last-place analysis and descriptive season analytics for public FPL classic mini-leagues.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @application.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/league/{league_id}", tags=["analysis"])
    def league_analysis(
        league_id: int,
        scenarios: int = Query(default=3, ge=1, le=12),
        ties: Literal["include", "strict"] = Query(default="include"),
        gameweek: int | None = Query(default=None, ge=1, le=38),
        detail: Literal["full", "summary"] = Query(default="full"),
    ) -> dict[str, Any]:
        if league_id <= 0:
            raise HTTPException(status_code=400, detail="League ID must be a positive integer")
        settings = AnalysisSettings(
            scenario_count=scenarios,
            max_relevant_players=DEFAULT_MAX_RELEVANT_PLAYERS,
            max_search_nodes=DEFAULT_MAX_SEARCH_NODES,
            allow_tied_last=ties == "include",
        )
        options = (
            {}
            if gameweek is None and detail == "full"
            else {"gameweek": gameweek, "include_scenarios": detail == "full"}
        )
        return _api_response(
            lambda: analysis_service.analyse_league(league_id, settings, **options)
        )

    @application.get("/api/demo/{mode}", tags=["analysis"])
    def demo_analysis(
        mode: DemoMode,
        scenarios: int = Query(default=3, ge=1, le=12),
        ties: Literal["include", "strict"] = Query(default="include"),
        detail: Literal["full", "summary"] = Query(default="full"),
    ) -> dict[str, Any]:
        if mode not in DEMO_MODES:
            raise HTTPException(status_code=404, detail="That demo state does not exist")
        settings = AnalysisSettings(
            scenario_count=scenarios,
            max_relevant_players=DEFAULT_MAX_RELEVANT_PLAYERS,
            max_search_nodes=DEFAULT_MAX_SEARCH_NODES,
            allow_tied_last=ties == "include",
        )
        options = {} if detail == "full" else {"include_scenarios": False}
        return _api_response(lambda: analysis_service.analyse_demo(mode, settings, **options))

    @application.get("/api/league/{league_id}/managers/{entry_id}/scenarios", tags=["analysis"])
    def manager_scenarios(
        league_id: int,
        entry_id: int,
        scenarios: int = Query(default=6, ge=1, le=12),
        ties: Literal["include", "strict"] = Query(default="include"),
        gameweek: int | None = Query(default=None, ge=1, le=38),
        demo: DemoMode | None = None,
    ) -> dict[str, Any]:
        if league_id <= 0 or entry_id <= 0:
            raise HTTPException(400, "League and manager IDs must be positive integers")
        settings = AnalysisSettings(scenario_count=scenarios, allow_tied_last=ties == "include")
        payload = _api_response(
            lambda: (
                analysis_service.analyse_demo(demo, settings, candidate_id=entry_id)
                if demo
                else analysis_service.analyse_league(
                    league_id, settings, gameweek=gameweek, candidate_id=entry_id
                )
            )
        )
        manager = next((m for m in payload["managers"] if m["entry_id"] == entry_id), None)
        if manager is None:
            raise HTTPException(404, "That manager is not in this league")
        return {"manager": manager, "league": payload["league"], "cache": payload["cache"]}

    @application.get("/api/league/{league_id}/compare", tags=["analysis"])
    def comparison(
        league_id: int,
        a: int = Query(ge=1),
        b: int = Query(ge=1),
        ties: Literal["include", "strict"] = Query(default="include"),
        gameweek: int | None = Query(default=None, ge=1, le=38),
        demo: DemoMode | None = None,
    ) -> dict[str, Any]:
        if league_id <= 0 or a == b:
            raise HTTPException(400, "Choose a valid league and two different managers")
        return _api_response(
            lambda: analysis_service.compare(
                league_id, a, b, gameweek=gameweek, demo=demo, allow_tied_last=ties == "include"
            )
        )

    @application.get("/api/league/{league_id}/analytics/details", tags=["analytics"])
    def analytics_detail_progress(league_id: int) -> dict[str, Any]:
        if league_id <= 0:
            raise HTTPException(400, "League ID must be a positive integer")
        return _api_response(lambda: analytics_service.detail_status(league_id))

    @application.get("/api/league/{league_id}/analytics/manager/{entry_id}", tags=["analytics"])
    def manager_analytics(league_id: int, entry_id: int) -> dict[str, Any]:
        if league_id <= 0 or entry_id <= 0:
            raise HTTPException(400, "League and manager IDs must be positive integers")
        return _api_response(
            lambda: analytics_service.section(league_id, "manager", entry_id=entry_id)
        )

    @application.get("/api/league/{league_id}/analytics/player/{player_id}", tags=["analytics"])
    def player_analytics(league_id: int, player_id: int) -> dict[str, Any]:
        if league_id <= 0 or player_id <= 0:
            raise HTTPException(400, "League and player IDs must be positive integers")
        return _api_response(
            lambda: analytics_service.section(league_id, "player", player_id=player_id)
        )

    @application.get("/api/league/{league_id}/analytics/{section}", tags=["analytics"])
    def league_analytics(
        league_id: int,
        section: Literal["summary", "gameweeks", "managers", "players", "head-to-head"],
        gameweek: int | None = Query(default=None, ge=1, le=38),
        a: int | None = Query(default=None, ge=1),
        b: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        if league_id <= 0:
            raise HTTPException(400, "League ID must be a positive integer")
        if section == "head-to-head" and (a is None or b is None or a == b):
            raise HTTPException(400, "Choose two different managers")
        return _api_response(
            lambda: analytics_service.section(league_id, section, gameweek=gameweek, a=a, b=b)
        )

    return application


def _api_response(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except FPLGameweekUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except FPLNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FPLAccessError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FPLLeagueSizeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FPLAPIError as exc:
        raise HTTPException(
            502, "FPL data is temporarily unavailable. Please try again shortly."
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, "FPL returned data the analyser could not process.") from exc


def _allowed_origins() -> list[str]:
    configured = os.getenv(
        "FPL_ALLOWED_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,https://mohilgarg.github.io",
    )
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


app = create_app()
