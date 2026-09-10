from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True)
class Session:
    session_id: str
    created_at: datetime
    event_cursor: int


class SessionNotFound(Exception):
    pass


class InvalidCursor(Exception):
    pass


class StorageBusy(Exception):
    pass


class StorageUnavailable(Exception):
    pass


class StoreClosed(Exception):
    pass


class SessionStore(Protocol):
    def create(self, session: Session, operation_id: str | None = None) -> Session: ...

    def get(self, session_id: str) -> Session: ...

    def advance_cursor(
        self, session_id: str, operation_id: str | None = None
    ) -> Session: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ResumeResult:
    session: Session
    cursor_valid: bool


class SessionService:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def create_session(self, operation_id: str | None = None) -> Session:
        session = Session(
            session_id=str(uuid4()),
            created_at=datetime.now(UTC),
            event_cursor=0,
        )
        return self.store.create(session, operation_id)

    def resume_session(
        self,
        session_id: str,
        last_event_id: str | None,
        operation_id: str | None = None,
    ) -> ResumeResult:
        previous_cursor = self.parse_cursor(last_event_id)
        session = self.store.advance_cursor(session_id, operation_id)
        return ResumeResult(
            session=session, cursor_valid=previous_cursor <= session.event_cursor - 1
        )

    def error_event(self, session_id: str, operation_id: str | None = None) -> Session:
        return self.store.advance_cursor(session_id, operation_id)

    @staticmethod
    def parse_cursor(event_id: str | None) -> int:
        if event_id is None:
            return 0
        if not is_canonical_cursor(event_id):
            raise InvalidCursor
        cursor = int(event_id[1:])
        if cursor <= 0 or cursor > 9223372036854775807:
            raise InvalidCursor
        return cursor


def is_canonical_cursor(value: str) -> bool:
    return (
        len(value) > 1
        and value.startswith("e")
        and value[1:].isascii()
        and value[1:].isdecimal()
        and value[1] != "0"
    )
