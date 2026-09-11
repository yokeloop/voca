from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

MAX_CURSOR = 9223372036854775807


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
        if not is_canonical_uuid(value):
            raise ValueError("invalid identifier")
        return value


class ResumeCommand(Command):
    last_event_id: str | None

    @field_validator("last_event_id")
    @classmethod
    def canonical_cursor(cls, value: str | None) -> str | None:
        if value is not None and decode_cursor(value) is None:
            raise ValueError("invalid cursor")
        return value


def decode_cursor(value: str | None) -> int | None:
    if value is None:
        return 0
    if not is_canonical_cursor(value):
        return None
    cursor = int(value[1:])
    return cursor if cursor <= MAX_CURSOR else None


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
