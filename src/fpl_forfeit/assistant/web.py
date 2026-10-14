"""Same-origin private UI and authenticated APIs, isolated from public CORS/data."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..api import FPLAPIError, FPLClient, FPLGameweekUnavailable, FPLNotFoundError
from .auth import SessionStore
from .service import AssistantService


class SquadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    entry_id: int = Field(gt=0, le=100_000_000)


class LineupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gameweek: int = Field(ge=1, le=38)
    starting_ids: list[int] = Field(min_length=11, max_length=11)
    bench_ids: list[int] = Field(min_length=4, max_length=4)
    captain: int = Field(gt=0)
    vice_captain: int = Field(gt=0)
    model: Literal["statistical", "components"] = "statistical"


class TransferMove(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outgoing: int = Field(gt=0)
    incoming: int = Field(gt=0)


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    moves: list[TransferMove] = Field(default_factory=list, max_length=5)
    bank_tenths: int = Field(ge=0, le=2000)
    free_transfers: int = Field(ge=0, le=5)
    selling_prices: dict[str, int] = Field(default_factory=dict, max_length=15)
    account_confirmed: Literal[True]
    model: Literal["statistical", "components"] = "statistical"


class TransferSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outgoing: int = Field(gt=0)
    horizon: int = Field(ge=1, le=6)
    bank_tenths: int = Field(ge=0, le=2000)
    free_transfers: int = Field(ge=0, le=5)
    selling_prices: dict[str, int] = Field(default_factory=dict, max_length=15)
    account_confirmed: Literal[True]
    model: Literal["statistical", "components"] = "statistical"


def register_assistant(application: FastAPI, service: AssistantService, auth: SessionStore) -> None:
    application.state.assistant_service = service
    application.state.assistant_auth = auth

    def startup_recording() -> None:
        import os

        if (
            auth.settings.enabled
            and isinstance(service.client, FPLClient)
            and os.getenv("FPL_ML_AUTORECORD", "1") == "1"
        ):
            from .prospective import start_recording

            start_recording(service)

    def stop_recording() -> None:
        service.ml.stopped.set()
        if stop := getattr(service, "ml_recording_stop", None):
            stop.set()

    previous_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def recording_lifespan(app):
        async with previous_lifespan(app) as state:
            startup_recording()
            try:
                yield state
            finally:
                stop_recording()

    application.router.lifespan_context = recording_lifespan
    public_auth = {("GET", "/api/assistant/session"), ("POST", "/api/assistant/login")}

    @application.middleware("http")
    async def private_boundary(request: Request, call_next):
        private = request.url.path == "/api/assistant" or request.url.path.startswith(
            "/api/assistant/"
        )
        shell = request.url.path.startswith("/assistant")
        try:
            if private and (request.method, request.url.path) not in public_auth:
                request.state.assistant_session = auth.require(request)
            response = await call_next(request)
            if (
                private
                and (request.method, request.url.path) not in public_auth
                and request.url.path != "/api/assistant/logout"
            ):
                # A session revoked/expired while a long plan was computing cannot
                # receive its result. Status checks here do not extend idle time.
                current = auth.get(request.cookies.get(auth.settings.cookie_name), touch=False)
                if current is None or current.subject != request.state.assistant_session.subject:
                    raise HTTPException(401, "Sign in to use the private assistant.")
        except HTTPException as error:
            response = JSONResponse(
                {"detail": error.detail}, status_code=error.status_code, headers=error.headers
            )
        if private or shell:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    router = APIRouter(prefix="/api/assistant", tags=["private assistant"])

    @router.get("/session")
    def session_status(request: Request) -> dict:
        if not auth.settings.enabled:
            return {
                "enabled": False,
                "authenticated": False,
                "note": "Private access is disabled until a password hash and HTTPS origin are configured on the backend.",
            }
        auth.check_origin(request)
        session = auth.get(request.cookies.get(auth.settings.cookie_name), touch=False)
        return {
            "enabled": True,
            "authenticated": session is not None,
            **({"csrf": session.csrf, "expires_at": session.expires_at} if session else {}),
        }

    @router.post("/login")
    async def login(request: Request):
        if not auth.settings.enabled:
            raise HTTPException(503, "Private access has not been configured.")
        auth.check_origin(request, write=True)
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise HTTPException(403, "Use the private sign-in form.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                raise HTTPException(413, "Sign-in request is too large.")
        password = ""
        try:
            values = json.loads(body)
            if (
                isinstance(values, dict)
                and set(values) == {"password"}
                and isinstance(values["password"], str)
                and len(values["password"]) <= 256
            ):
                password = values["password"]
        except (ValueError, UnicodeDecodeError):
            pass
        token, session = await run_in_threadpool(
            auth.login, password, request.client.host if request.client else "unknown"
        )
        response = JSONResponse(
            {"authenticated": True, "csrf": session.csrf, "expires_at": session.expires_at}
        )
        response.set_cookie(
            auth.settings.cookie_name,
            token,
            max_age=auth.settings.session_seconds,
            path="/",
            secure=auth.settings.secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.post("/logout")
    def logout(request: Request):
        service.clear_session(request.state.assistant_session.subject)
        auth.logout(request.cookies.get(auth.settings.cookie_name))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(
            auth.settings.cookie_name,
            path="/",
            secure=auth.settings.secure,
            httponly=True,
            samesite="strict",
        )
        return response

    def result(call, model="statistical"):
        try:
            with service.using_model(model):
                return call()
        except FPLGameweekUnavailable as error:
            raise HTTPException(409, str(error)) from error
        except FPLNotFoundError as error:
            raise HTTPException(404, "The public FPL squad could not be found.") from error
        except FPLAPIError as error:
            raise HTTPException(
                502, "Official FPL data is temporarily unavailable. Try again shortly."
            ) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except (KeyError, TypeError, StopIteration) as error:
            raise HTTPException(
                502,
                "Official data is incomplete; the assistant will not guess missing account or fixture information.",
            ) from error

    @router.get("/predictions")
    def predictions(horizon: int = Query(default=3, ge=1, le=6)):
        return result(lambda: service.predictions(horizon))

    @router.get("/ml/status")
    def ml_status():
        return result(service.ml_status)

    @router.get("/players/{player_id}")
    def player(player_id: int, model: Literal["statistical", "components"] = "statistical"):
        return result(lambda: service.player(player_id), model)

    @router.post("/refresh")
    def refresh():
        return service.refresh()

    @router.post("/squad")
    def load_squad(body: SquadRequest, request: Request):
        return result(
            lambda: service.load_squad(request.state.assistant_session.subject, body.entry_id)
        )

    @router.get("/squad")
    def squad(request: Request):
        return result(lambda: service.squad(request.state.assistant_session.subject))

    @router.get("/lineup")
    def lineup(
        request: Request,
        gameweek: int | None = Query(default=None, ge=1, le=38),
        model: Literal["statistical", "components"] = "statistical",
    ):
        return result(
            lambda: service.lineup(request.state.assistant_session.subject, gameweek), model
        )

    @router.post("/lineup")
    def evaluate_lineup(body: LineupRequest, request: Request):
        return result(
            lambda: service.lineup(
                request.state.assistant_session.subject, body.gameweek, draft=body.model_dump()
            ),
            body.model,
        )

    @router.post("/transfers")
    def transfers(body: TransferRequest, request: Request):
        return result(
            lambda: service.transfers(
                request.state.assistant_session.subject,
                [(m.outgoing, m.incoming) for m in body.moves],
                body.bank_tenths,
                body.free_transfers,
                {int(pid): value for pid, value in body.selling_prices.items()},
            ),
            body.model,
        )

    @router.get("/chips")
    def chips(request: Request, model: Literal["statistical", "components"] = "statistical"):
        return result(lambda: service.chips(request.state.assistant_session.subject), model)

    @router.post("/transfers/search")
    def transfer_search(body: TransferSearchRequest, request: Request):
        return result(
            lambda: service.transfer_search(
                request.state.assistant_session.subject,
                body.outgoing,
                body.bank_tenths,
                body.free_transfers,
                {int(pid): value for pid, value in body.selling_prices.items()},
                body.horizon,
            ),
            body.model,
        )

    application.include_router(router)
    source = Path(__file__).resolve().parents[3] / "frontend" / "src"
    if not source.is_dir():
        # Source checkout on Render; installed wheels can use an explicit public-asset directory.
        import os

        source = Path(os.getenv("FPL_ASSISTANT_ASSETS", "frontend/src")).resolve()

    @application.get("/assistant", include_in_schema=False)
    def assistant_redirect():
        return RedirectResponse("/assistant/", status_code=307)

    @application.get("/assistant/", include_in_schema=False)
    def assistant_page():
        if not (source / "assistant.html").is_file():
            raise HTTPException(503, "Private dashboard assets are unavailable.")
        return FileResponse(source / "assistant.html")

    if source.is_dir():
        application.mount(
            "/assistant/assets", StaticFiles(directory=source), name="assistant-assets"
        )
