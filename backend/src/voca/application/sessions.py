from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from voca.application.ports import Diagnostics, InvalidCursor, SessionStore
from voca.domain.session import Session

MAX_CURSOR = 9223372036854775807


@dataclass(frozen=True)
class ResumeResult:
    session: Session
    cursor_valid: bool


class SessionService:
    def __init__(self, store: SessionStore, diagnostics: Diagnostics) -> None:
        self._store = store
        self._diagnostics = diagnostics

    def create_session(self, operation_id: str | None = None) -> Session:
        session = Session(str(uuid4()), datetime.now(UTC), 0)
        with self._diagnostics.operation(
            "session", "session.create", operation_id=operation_id
        ):
            return self._store.create(session, operation_id)

    def get_session(self, session_id: str) -> Session:
        return self._store.get(session_id)

    def resume_session(
        self,
        session_id: str,
        previous_cursor: int,
        operation_id: str | None = None,
        command_id: str | None = None,
    ) -> ResumeResult:
        if previous_cursor < 0 or previous_cursor > MAX_CURSOR:
            raise InvalidCursor
        with self._diagnostics.operation(
            "session",
            "session.resume",
            operation_id=operation_id,
            session_id=session_id,
            command_id=command_id,
        ):
            session = self._store.advance_cursor(session_id, operation_id)
        return ResumeResult(session, previous_cursor <= session.event_cursor - 1)

    def error_event(self, session_id: str, operation_id: str | None = None) -> Session:
        return self._store.advance_cursor(session_id, operation_id)
