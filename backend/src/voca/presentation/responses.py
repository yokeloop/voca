from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from voca.application.ports import Diagnostics
from voca.domain.session import Session

IO_TIMEOUT_SECONDS = 5


def snapshot_payload(session: Session) -> dict[str, Any]:
    return {
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


def error_payload(session: Session, code: str, message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "session_id": session.session_id,
        "event_id": f"e{session.event_cursor}",
        "component": "control",
        "code": code,
        "message": message,
    }


async def send_snapshot(
    socket_sender: Callable[[dict[str, Any]], Awaitable[None]],
    diagnostics: Diagnostics,
    session: Session,
    command_id: str,
    operation_id: str | None = None,
) -> None:
    with diagnostics.operation(
        "control",
        "control.snapshot_send",
        operation_id=operation_id,
        session_id=session.session_id,
        command_id=command_id,
    ):
        await send_with_deadline(socket_sender, snapshot_payload(session))


async def send_with_deadline(
    sender: Callable[[dict[str, Any]], Awaitable[None]], payload: dict[str, Any]
) -> None:
    async with asyncio.timeout(IO_TIMEOUT_SECONDS):
        await sender(payload)


async def close_with_deadline(
    closer: Callable[..., Awaitable[None]], *, code: int, reason: str
) -> None:
    async with asyncio.timeout(IO_TIMEOUT_SECONDS):
        await closer(code=code, reason=reason)
