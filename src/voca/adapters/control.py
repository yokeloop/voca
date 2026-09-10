from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from voca.core.session import (
    Session,
    SessionNotFound,
    SessionService,
    StorageBusy,
    StorageUnavailable,
    StoreClosed,
)
from voca.diagnostics import Diagnostics

MAX_CURSOR = 9223372036854775807
MAX_FRAME_BYTES = 8192


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: str
    session_id: str
    event_id: str

    @field_validator("session_id", "event_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (ValueError, TypeError) as error:
            raise ValueError("invalid identifier") from error
        if str(parsed) != value:
            raise ValueError("invalid identifier")
        return value


class ResumeCommand(Command):
    last_event_id: str | None

    @field_validator("last_event_id")
    @classmethod
    def canonical_cursor(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not is_canonical_cursor(value):
            raise ValueError("invalid cursor")
        cursor = int(value[1:])
        if cursor < 1 or cursor > MAX_CURSOR:
            raise ValueError("invalid cursor")
        return value


def create_control_router() -> APIRouter:
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
            with (
                diagnostics.operation("control", "http.session_create") as operation_id,
                diagnostics.operation(
                    "session", "session.create", operation_id=operation_id
                ),
            ):
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

    @router.websocket("/api/sessions/{session_id}/control")
    async def control_socket(socket: WebSocket, session_id: str) -> None:
        service: SessionService = socket.app.state.service
        diagnostics: Diagnostics = socket.app.state.diagnostics
        await socket.accept()
        if not is_canonical_uuid(session_id):
            await socket.close(code=4400, reason="invalid_session_id")
            return
        try:
            await run_in_threadpool(service.store.get, session_id)
        except SessionNotFound:
            await socket.close(code=4404, reason="session_not_found")
            return
        except (StorageBusy, StorageUnavailable, StoreClosed):
            await socket.close(code=1011, reason="storage_unavailable")
            return
        diagnostics.log_event(
            "control", "control.connect", "succeeded", session_id=session_id
        )
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))
                binary = message.get("bytes")
                if isinstance(binary, bytes) and len(binary) > MAX_FRAME_BYTES:
                    await socket.close(code=1009, reason="frame_too_large")
                    return
                if binary is not None:
                    await send_session_error(
                        socket,
                        service,
                        diagnostics,
                        session_id,
                        "invalid_command",
                        "Invalid command",
                    )
                    continue
                raw = message.get("text")
                if isinstance(raw, str) and len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                    await socket.close(code=1009, reason="frame_too_large")
                    return
                if not isinstance(raw, str):
                    await send_session_error(
                        socket,
                        service,
                        diagnostics,
                        session_id,
                        "invalid_command",
                        "Invalid command",
                    )
                    continue
                await handle_command(socket, service, diagnostics, session_id, raw)
        except WebSocketDisconnect:
            diagnostics.log_event(
                "control", "control.disconnect", "succeeded", session_id=session_id
            )
        except (StorageBusy, StorageUnavailable, StoreClosed):
            await socket.close(code=1011, reason="storage_unavailable")
        except (RuntimeError, ValueError, OSError):
            diagnostics.log_event(
                "app",
                "control.disconnect",
                "failed",
                reason="internal_error",
                session_id=session_id,
            )
            await socket.close(code=1011, reason="internal_error")

    return router


async def handle_command(
    socket: WebSocket,
    service: SessionService,
    diagnostics: Diagnostics,
    session_id: str,
    raw: str,
) -> None:
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise TypeError
        model = ResumeCommand if parsed.get("type") == "session.resume" else Command
        base = model.model_validate(parsed)
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
        await send_session_error(
            socket,
            service,
            diagnostics,
            session_id,
            "invalid_command",
            "Invalid command",
        )
        return
    if base.session_id != session_id:
        await send_session_error(
            socket,
            service,
            diagnostics,
            session_id,
            "session_mismatch",
            "Session does not match",
        )
        return
    if base.type != "session.resume":
        await send_session_error(
            socket,
            service,
            diagnostics,
            session_id,
            "unsupported_command",
            "Unsupported command",
            base.event_id,
        )
        return
    command = base
    if not isinstance(command, ResumeCommand):
        await send_session_error(
            socket,
            service,
            diagnostics,
            session_id,
            "invalid_command",
            "Invalid command",
            base.event_id,
        )
        return
    with (
        diagnostics.operation(
            "control",
            "control.receive",
            session_id=session_id,
            command_id=command.event_id,
        ) as operation_id,
        diagnostics.operation(
            "session",
            "session.resume",
            operation_id=operation_id,
            session_id=session_id,
            command_id=command.event_id,
        ),
    ):
        result = await run_in_threadpool(
            service.resume_session,
            session_id,
            command.last_event_id,
            operation_id,
        )
    if not result.cursor_valid:
        await send_session_error(
            socket,
            service,
            diagnostics,
            session_id,
            "invalid_cursor",
            "Invalid cursor",
            command.event_id,
            result.session,
        )
        return
    await send_snapshot(
        socket, diagnostics, result.session, command.event_id, operation_id
    )


async def send_snapshot(
    socket: WebSocket,
    diagnostics: Diagnostics,
    session: Session,
    command_id: str,
    operation_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "session.snapshot",
        "session_id": session.session_id,
        "event_id": f"e{session.event_cursor}",
        "connection": "disconnected",
        "conversation": "off",
        "active_turn": None,
        "messages": [],
        "tasks": [],
        "deliveries": [],
    }
    with diagnostics.operation(
        "control",
        "control.snapshot_send",
        operation_id=operation_id,
        session_id=session.session_id,
        command_id=command_id,
    ):
        await send_with_deadline(socket.send_json, payload)


async def send_session_error(
    socket: WebSocket,
    service: SessionService,
    diagnostics: Diagnostics,
    session_id: str,
    code: str,
    message: str,
    command_id: str | None = None,
    session: Session | None = None,
) -> None:
    diagnostics.log_event(
        "control",
        "control.command_rejected",
        "failed",
        level="WARNING",
        reason=code,
        session_id=session_id,
        command_id=command_id,
    )
    if session is None:
        current_session: Session = await run_in_threadpool(
            service.error_event, session_id
        )
    else:
        current_session = session
    payload: dict[str, Any] = {
        "type": "error",
        "session_id": session_id,
        "event_id": f"e{current_session.event_cursor}",
        "component": "control",
        "code": code,
        "message": message,
    }
    await send_with_deadline(socket.send_json, payload)
    diagnostics.log_event(
        "control",
        "control.error_send",
        "succeeded",
        session_id=session_id,
        command_id=command_id,
        server_event_id=f"e{current_session.event_cursor}",
    )


async def send_with_deadline(
    sender: Callable[..., Awaitable[None]], payload: dict[str, Any]
) -> None:
    async with asyncio.timeout(5):
        await sender(payload)


def error_response(
    component: str, code: str, message: str, status_code: int
) -> JSONResponse:
    return JSONResponse(
        {"error": {"component": component, "code": code, "message": message}},
        status_code=status_code,
    )


def is_canonical_cursor(value: str) -> bool:
    return (
        len(value) > 1
        and value.startswith("e")
        and value[1:].isascii()
        and value[1:].isdecimal()
        and value[1] != "0"
    )


def is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError):
        return False
