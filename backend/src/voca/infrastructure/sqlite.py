from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from voca.application.ports import (
    Diagnostics,
    SessionNotFound,
    StorageBusy,
    StorageUnavailable,
    StoreClosed,
)
from voca.domain.session import Session
from voca.infrastructure.sqlite_schema import initialize_schema


class SQLiteSessionStore:
    def __init__(
        self, connection: sqlite3.Connection, diagnostics: Diagnostics
    ) -> None:
        self._connection = connection
        self._diagnostics = diagnostics
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, diagnostics: Diagnostics) -> SQLiteSessionStore:
        if str(path) == ":memory:" or str(path).startswith("file:"):
            raise StorageUnavailable
        connection: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=1000")
            connection.execute("PRAGMA synchronous=FULL")
            initialize_schema(connection)
        except StorageUnavailable:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise cls.storage_error(error) from error
        store = cls(connection, diagnostics)
        diagnostics.log_event("storage", "storage.schema", "succeeded")
        diagnostics.log_event("storage", "storage.open", "succeeded")
        return store

    def create(self, session: Session, operation_id: str | None = None) -> Session:
        with (
            self._diagnostics.operation(
                "storage",
                "storage.session_insert",
                operation_id=operation_id,
                session_id=session.session_id,
            ),
            self._lock,
        ):
            self.ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "INSERT INTO sessions(session_id, created_at, event_cursor) VALUES (?, ?, ?)",
                    (
                        session.session_id,
                        session.created_at.isoformat(),
                        session.event_cursor,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as error:
                self._connection.rollback()
                raise self.storage_error(error) from error
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            self.ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT session_id, created_at, event_cursor FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            except sqlite3.Error as error:
                raise self.storage_error(error) from error
        if row is None:
            raise SessionNotFound
        return self.session_from_row(row)

    def advance_cursor(
        self, session_id: str, operation_id: str | None = None
    ) -> Session:
        with (
            self._diagnostics.operation(
                "storage",
                "storage.cursor_advance",
                operation_id=operation_id,
                session_id=session_id,
            ),
            self._lock,
        ):
            self.ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "UPDATE sessions SET event_cursor = event_cursor + 1 WHERE session_id = ? RETURNING session_id, created_at, event_cursor",
                    (session_id,),
                ).fetchone()
                if row is None:
                    self._connection.rollback()
                    raise SessionNotFound
                self._connection.commit()
            except SessionNotFound:
                raise
            except sqlite3.Error as error:
                self._connection.rollback()
                raise self.storage_error(error) from error
        return self.session_from_row(row)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True
        self._diagnostics.log_event("storage", "storage.close", "succeeded")

    def ensure_open(self) -> None:
        if self._closed:
            raise StoreClosed

    @staticmethod
    def session_from_row(row: tuple[str, str, int]) -> Session:
        return Session(row[0], datetime.fromisoformat(row[1]), row[2])

    @staticmethod
    def storage_error(error: sqlite3.Error) -> StorageBusy | StorageUnavailable:
        if (
            isinstance(error, sqlite3.OperationalError)
            and "locked" in str(error).lower()
        ):
            return StorageBusy()
        return StorageUnavailable()
