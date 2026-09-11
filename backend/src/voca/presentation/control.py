from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from voca.application.ports import (
    Diagnostics,
    SessionNotFound,
    StorageBusy,
    StorageUnavailable,
    StoreClosed,
)
from voca.application.sessions import SessionService
from voca.domain.session import Session
from voca.presentation.responses import (
    close_with_deadline,
    error_payload,
    send_snapshot,
    send_with_deadline,
)
from voca.presentation.schemas import (
    Command,
    ResumeCommand,
    decode_cursor,
    is_canonical_uuid,
)

MAX_FRAME_BYTES = 8192


def create_control_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/sessions/{session_id}/control")
    async def control_socket(socket: WebSocket, session_id: str) -> None:
        service: SessionService = socket.app.state.service
        diagnostics: Diagnostics = socket.app.state.diagnostics
        await socket.accept()
        if not is_canonical_uuid(session_id):
            await safe_close(socket, 4400, "invalid_session_id")
            return
        try:
            await run_in_threadpool(service.get_session, session_id)
        except SessionNotFound:
            await safe_close(socket, 4404, "session_not_found")
            return
        except (StorageBusy, StorageUnavailable, StoreClosed):
            await safe_close(socket, 1011, "storage_unavailable")
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
                    await safe_close(socket, 1009, "frame_too_large")
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
                if isinstance(raw, str) and len(raw.encode()) > MAX_FRAME_BYTES:
                    await safe_close(socket, 1009, "frame_too_large")
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
        except asyncio.CancelledError:
            await safe_close(socket, 1011, "cancelled")
            raise
        except (StorageBusy, StorageUnavailable, StoreClosed):
            await safe_close(socket, 1011, "storage_unavailable")
        except (RuntimeError, ValueError, OSError, TimeoutError):
            diagnostics.log_event(
                "app",
                "control.disconnect",
                "failed",
                reason="internal_error",
                session_id=session_id,
            )
            await safe_close(socket, 1011, "internal_error")

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
        base = (
            ResumeCommand if parsed.get("type") == "session.resume" else Command
        ).model_validate(parsed)
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
    if not isinstance(base, ResumeCommand):
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
    previous_cursor = decode_cursor(base.last_event_id)
    if previous_cursor is None:
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
    with diagnostics.operation(
        "control", "control.receive", session_id=session_id, command_id=base.event_id
    ) as operation_id:
        result = await run_in_threadpool(
            service.resume_session,
            session_id,
            previous_cursor,
            operation_id,
            base.event_id,
        )
    if not result.cursor_valid:
        await send_session_error(
            socket,
            service,
            diagnostics,
            session_id,
            "invalid_cursor",
            "Invalid cursor",
            base.event_id,
            result.session,
        )
        return
    await send_snapshot(
        socket.send_json, diagnostics, result.session, base.event_id, operation_id
    )


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
        current: Session = await run_in_threadpool(service.error_event, session_id)
    else:
        current = session
    await send_with_deadline(socket.send_json, error_payload(current, code, message))
    diagnostics.log_event(
        "control",
        "control.error_send",
        "succeeded",
        session_id=session_id,
        command_id=command_id,
        server_event_id=f"e{current.event_cursor}",
    )


async def safe_close(socket: WebSocket, code: int, reason: str) -> None:
    try:
        await close_with_deadline(socket.close, code=code, reason=reason)
    except TimeoutError:
        pass
