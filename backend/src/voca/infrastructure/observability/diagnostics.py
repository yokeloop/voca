from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from voca.infrastructure.observability.logging import LEVELS


class Diagnostics:
    def __init__(self, logger: logging.Logger, handler: logging.Handler) -> None:
        self._logger = logger
        self._handler = handler

    def close(self) -> None:
        self._logger.removeHandler(self._handler)
        self._handler.flush()
        self._handler.close()

    def log_event(
        self,
        component: str,
        event: str,
        outcome: str,
        *,
        operation_id: str | None = None,
        level: str | None = None,
        duration_ms: int | None = None,
        reason: str | None = None,
        session_id: str | None = None,
        command_id: str | None = None,
        server_event_id: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level or self.level_for(outcome),
            "event": event,
            "operation_id": operation_id or str(uuid.uuid4()),
            "component": component,
            "outcome": outcome,
        }
        for key, value in {
            "duration_ms": duration_ms,
            "reason": reason,
            "session_id": session_id,
            "command_id": command_id,
            "server_event_id": server_event_id,
        }.items():
            if value is not None:
                payload[key] = value
        self._logger.log(
            LEVELS.get(str(payload["level"]), logging.INFO),
            "event",
            extra={"payload": payload, "component": component},
        )

    @contextmanager
    def operation(
        self,
        component: str,
        event: str,
        *,
        operation_id: str | None = None,
        session_id: str | None = None,
        command_id: str | None = None,
    ) -> Iterator[str]:
        correlation = operation_id or str(uuid.uuid4())
        started = time.monotonic()
        self.log_event(
            component,
            event,
            "started",
            operation_id=correlation,
            session_id=session_id,
            command_id=command_id,
        )
        try:
            yield correlation
        except asyncio.CancelledError:
            self.terminal(
                component,
                event,
                "cancelled",
                correlation,
                started,
                "operation_cancelled",
                session_id,
                command_id,
            )
            raise
        except TimeoutError:
            self.terminal(
                component,
                event,
                "timeout",
                correlation,
                started,
                "operation_timeout",
                session_id,
                command_id,
            )
            raise
        except BaseException:
            self.terminal(
                component,
                event,
                "failed",
                correlation,
                started,
                "operation_failed",
                session_id,
                command_id,
            )
            raise
        else:
            self.terminal(
                component,
                event,
                "succeeded",
                correlation,
                started,
                None,
                session_id,
                command_id,
            )

    def terminal(
        self,
        component: str,
        event: str,
        outcome: str,
        operation_id: str,
        started: float,
        reason: str | None,
        session_id: str | None,
        command_id: str | None,
    ) -> None:
        self.log_event(
            component,
            event,
            outcome,
            operation_id=operation_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            reason=reason,
            session_id=session_id,
            command_id=command_id,
        )

    @staticmethod
    def level_for(outcome: str) -> str:
        if outcome == "failed":
            return "ERROR"
        if outcome in {"cancelled", "timeout"}:
            return "WARNING"
        return "INFO"
