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
    def create(self, session: Session) -> Session: ...

    def get(self, session_id: str) -> Session: ...

    def advance_cursor(self, session_id: str) -> Session: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ResumeResult:
    session: Session
    cursor_valid: bool


class SessionService:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def create_session(self) -> Session:
        session = Session(
            session_id=str(uuid4()),
            created_at=datetime.now(UTC),
            event_cursor=0,
        )
        return self.store.create(session)

    def resume_session(
        self, session_id: str, last_event_id: str | None
    ) -> ResumeResult:
        previous_cursor = self.parse_cursor(last_event_id)
        session = self.store.advance_cursor(session_id)
        return ResumeResult(
            session=session, cursor_valid=previous_cursor <= session.event_cursor - 1
        )

    def error_event(self, session_id: str) -> Session:
        return self.store.advance_cursor(session_id)

    @staticmethod
    def parse_cursor(event_id: str | None) -> int:
        if event_id is None:
            return 0
        if not event_id.startswith("e") or not event_id[1:].isdigit():
            raise InvalidCursor
        cursor = int(event_id[1:])
        if cursor <= 0 or cursor > 9223372036854775807:
            raise InvalidCursor
        return cursor
