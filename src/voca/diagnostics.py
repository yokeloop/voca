from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

LEVELS: Final = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
MAX_RECORD_BYTES: Final = 2048


class SafeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, path: Path) -> None:
        super().__init__(path, maxBytes=262144, backupCount=3, encoding="utf-8")
        self.disabled = False
        self.failure_reported = False

    def emit(self, record: logging.LogRecord) -> None:
        if not self.disabled:
            super().emit(record)

    def handleError(self, record: logging.LogRecord) -> None:
        del record
        self.disabled = True
        if not self.failure_reported:
            self.failure_reported = True
            try:
                sys.stderr.write("diagnostics/write_failed\n")
            except OSError:
                pass


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "payload", {})
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
            return '{"event":"diagnostics.record_dropped","outcome":"failed"}'
        return encoded


class ComponentFilter(logging.Filter):
    def __init__(self, default_level: str, component_levels: dict[str, str]) -> None:
        super().__init__()
        self.default_level = LEVELS[default_level]
        self.component_levels = {
            key: LEVELS[value] for key, value in component_levels.items()
        }

    def filter(self, record: logging.LogRecord) -> bool:
        component = getattr(record, "component", "app")
        threshold = self.component_levels.get(component, self.default_level)
        return record.levelno >= threshold


class Diagnostics:
    def __init__(self, logger: logging.Logger, handler: logging.Handler) -> None:
        self.logger = logger
        self.handler = handler

    def close(self) -> None:
        self.logger.removeHandler(self.handler)
        self.handler.flush()
        self.handler.close()

    def log_event(
        self,
        component: str,
        event: str,
        outcome: str,
        *,
        operation_id: str | None = None,
        duration_ms: int | None = None,
        reason: str | None = None,
        session_id: str | None = None,
        command_id: str | None = None,
        server_event_id: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "timestamp": self.format_timestamp(),
            "level": self.level_for(outcome),
            "event": event,
            "operation_id": operation_id or str(uuid.uuid4()),
            "component": component,
            "outcome": outcome,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if reason is not None:
            payload["reason"] = reason
        if session_id is not None:
            payload["session_id"] = session_id
        if command_id is not None:
            payload["command_id"] = command_id
        if server_event_id is not None:
            payload["server_event_id"] = server_event_id
        level = getattr(logging, str(payload["level"]))
        self.logger.log(
            level, "event", extra={"payload": payload, "component": component}
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
        operation_id = operation_id or str(uuid.uuid4())
        started = time.monotonic()
        self.log_event(
            component,
            event,
            "started",
            operation_id=operation_id,
            session_id=session_id,
            command_id=command_id,
        )
        try:
            yield operation_id
        except asyncio.CancelledError:
            duration = int((time.monotonic() - started) * 1000)
            self.log_event(
                component,
                event,
                "cancelled",
                operation_id=operation_id,
                duration_ms=duration,
                reason="operation_cancelled",
                session_id=session_id,
                command_id=command_id,
            )
            raise
        except BaseException:
            duration = int((time.monotonic() - started) * 1000)
            self.log_event(
                component,
                event,
                "failed",
                operation_id=operation_id,
                duration_ms=duration,
                reason="operation_failed",
                session_id=session_id,
                command_id=command_id,
            )
            raise
        else:
            duration = int((time.monotonic() - started) * 1000)
            self.log_event(
                component,
                event,
                "succeeded",
                operation_id=operation_id,
                duration_ms=duration,
                session_id=session_id,
                command_id=command_id,
            )

    @staticmethod
    def format_timestamp() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def level_for(outcome: str) -> str:
        if outcome == "failed":
            return "ERROR"
        if outcome in {"cancelled", "timeout"}:
            return "WARNING"
        return "INFO"


def configure_logging(
    log_dir: Path, log_level: str, log_levels: dict[str, str]
) -> Diagnostics:
    logger = logging.getLogger("voca")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = SafeRotatingFileHandler(log_dir / "voca.jsonl")
    except OSError:
        try:
            sys.stderr.write("diagnostics/write_failed\n")
        except OSError:
            pass
        handler = logging.NullHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ComponentFilter(log_level, log_levels))
    logger.addHandler(handler)
    return Diagnostics(logger, handler)
