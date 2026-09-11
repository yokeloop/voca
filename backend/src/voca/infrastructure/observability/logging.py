from __future__ import annotations

import json
import logging
import sys
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
        encoded = json.dumps(
            getattr(record, "payload", {}), ensure_ascii=True, separators=(",", ":")
        )
        if len(encoded.encode()) > MAX_RECORD_BYTES:
            return '{"event":"diagnostics.record_dropped","outcome":"failed"}'
        return encoded


class ComponentFilter(logging.Filter):
    def __init__(self, default_level: str, component_levels: dict[str, str]) -> None:
        super().__init__()
        self._default_level = LEVELS[default_level]
        self._component_levels = {
            key: LEVELS[value] for key, value in component_levels.items()
        }

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self._component_levels.get(
            getattr(record, "component", "app"), self._default_level
        )


def configure_logging(log_dir: Path, log_level: str, log_levels: dict[str, str]):
    from voca.infrastructure.observability.diagnostics import Diagnostics

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
