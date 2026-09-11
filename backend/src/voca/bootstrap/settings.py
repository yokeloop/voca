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
    try:
        parsed = json.loads(values.get("VOCA_LOG_LEVELS", "{}"))
    except json.JSONDecodeError as error:
        raise ConfigurationError("Invalid logging configuration") from error
    if not isinstance(parsed, dict):
        raise ConfigurationError("Invalid logging configuration")
    levels: dict[str, str] = {}
    for component, level in parsed.items():
        if (
            not isinstance(component, str)
            or not isinstance(level, str)
            or component not in COMPONENTS
            or level.upper() not in LEVELS
        ):
            raise ConfigurationError("Invalid logging configuration")
        levels[component] = level.upper()
    return Settings(
        Path(values.get("VOCA_DB_PATH", ".local/voca.sqlite3")),
        Path(values.get("VOCA_LOG_DIR", ".local/logs")),
        log_level,
        levels,
    )
