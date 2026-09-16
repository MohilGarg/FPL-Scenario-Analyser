from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .api import FPLAccessError, FPLAPIError, FPLLeagueSizeError, FPLNotFoundError
from .service import AnalysisService
from .settings import (
    DEFAULT_MAX_RELEVANT_PLAYERS,
    DEFAULT_MAX_SEARCH_NODES,
    AnalysisSettings,
)


def create_app(service: AnalysisService | None = None) -> FastAPI:
    analysis_service = service or AnalysisService()
    application = FastAPI(
        title="FPL Scenario Analyser API",
        version="1.0.0",
        description="Processed last-place analysis for public FPL classic mini-leagues.",
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
    ) -> dict[str, Any]:
        if league_id <= 0:
            raise HTTPException(status_code=400, detail="League ID must be a positive integer")
        settings = AnalysisSettings(
            scenario_count=scenarios,
            max_relevant_players=DEFAULT_MAX_RELEVANT_PLAYERS,
            max_search_nodes=DEFAULT_MAX_SEARCH_NODES,
        )
        try:
            return analysis_service.analyse_league(league_id, settings)
        except FPLNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FPLAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FPLLeagueSizeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FPLAPIError as exc:
            raise HTTPException(
                status_code=502,
                detail="FPL data is temporarily unavailable. Please try again shortly.",
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail="FPL returned data the analyser could not process.",
            ) from exc

    return application


def _allowed_origins() -> list[str]:
    configured = os.getenv(
        "FPL_ALLOWED_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,https://mohilgarg.github.io",
    )
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


app = create_app()
