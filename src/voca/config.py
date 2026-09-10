from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

COMPONENTS: Final = frozenset({"app", "control", "session", "storage"})
LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(".local/voca.sqlite3")
    log_dir: Path = Path(".local/logs")
    log_level: str = "INFO"
    log_levels: dict[str, str] = field(default_factory=dict)


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    values = os.environ if environ is None else environ
    log_level = values.get("VOCA_LOG_LEVEL", "INFO").upper()
    if log_level not in LEVELS:
        raise ConfigurationError("Invalid logging configuration")
    raw_levels = values.get("VOCA_LOG_LEVELS", "{}")
    try:
        parsed_levels = json.loads(raw_levels)
    except json.JSONDecodeError as error:
        raise ConfigurationError("Invalid logging configuration") from error
    if not isinstance(parsed_levels, dict):
        raise ConfigurationError("Invalid logging configuration")
    levels: dict[str, str] = {}
    for component, level in parsed_levels.items():
        if not isinstance(component, str) or not isinstance(level, str):
            raise ConfigurationError("Invalid logging configuration")
        normalized_level = level.upper()
        if component not in COMPONENTS or normalized_level not in LEVELS:
            raise ConfigurationError("Invalid logging configuration")
        levels[component] = normalized_level
    return Settings(
        db_path=Path(values.get("VOCA_DB_PATH", ".local/voca.sqlite3")),
        log_dir=Path(values.get("VOCA_LOG_DIR", ".local/logs")),
        log_level=log_level,
        log_levels=levels,
    )
