from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from voca.domain.session import Session


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


class Diagnostics(Protocol):
    def operation(
        self,
        component: str,
        event: str,
        *,
        operation_id: str | None = None,
        session_id: str | None = None,
        command_id: str | None = None,
    ) -> AbstractContextManager[str]: ...

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
    ) -> None: ...
