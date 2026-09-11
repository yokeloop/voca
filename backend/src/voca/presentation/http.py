from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from voca.application.ports import (
    Diagnostics,
    StorageBusy,
    StorageUnavailable,
    StoreClosed,
)
from voca.application.sessions import SessionService
from voca.presentation.schemas import EmptyBody


def create_http_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/sessions", status_code=201)
    async def create_session(request: Request) -> JSONResponse:
        service: SessionService = request.app.state.service
        diagnostics: Diagnostics = request.app.state.diagnostics
        if (
            request.headers.get("content-type", "").split(";", 1)[0].lower()
            != "application/json"
        ):
            return error_response(
                "control", "unsupported_media_type", "Unsupported media type", 415
            )
        try:
            payload = await request.json()
            EmptyBody.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return error_response("control", "invalid_request", "Invalid request", 400)
        try:
            with diagnostics.operation(
                "control", "http.session_create"
            ) as operation_id:
                session = await run_in_threadpool(service.create_session, operation_id)
        except StorageBusy:
            return error_response("storage", "storage_busy", "Storage is busy", 503)
        except (StorageUnavailable, StoreClosed):
            return error_response(
                "storage", "storage_unavailable", "Storage is unavailable", 503
            )
        return JSONResponse(
            {"session_id": session.session_id, "last_event_id": None},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    return router


def error_response(
    component: str, code: str, message: str, status_code: int
) -> JSONResponse:
    return JSONResponse(
        {"error": {"component": component, "code": code, "message": message}},
        status_code=status_code,
    )


async def internal_error(request: Request, error: Exception) -> JSONResponse:
    del error
    diagnostics = getattr(request.app.state, "diagnostics", None)
    if diagnostics is not None:
        diagnostics.log_event(
            "app", "http.session_create", "failed", reason="internal_error"
        )
    return error_response("app", "internal_error", "Internal error", 500)
