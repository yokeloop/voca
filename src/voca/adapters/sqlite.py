from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from voca.core.session import (
    Session,
    SessionNotFound,
    StorageBusy,
    StorageUnavailable,
    StoreClosed,
)
from voca.diagnostics import Diagnostics


class SQLiteSessionStore:
    def __init__(
        self, connection: sqlite3.Connection, diagnostics: Diagnostics
    ) -> None:
        self.connection = connection
        self.diagnostics = diagnostics
        self.lock = threading.RLock()
        self.closed = False

    @classmethod
    def open(cls, path: Path, diagnostics: Diagnostics) -> SQLiteSessionStore:
        if str(path) == ":memory:" or str(path).startswith("file:"):
            raise StorageUnavailable
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=1000")
            connection.execute("PRAGMA synchronous=FULL")
            store = cls(connection, diagnostics)
            store.initialize_schema()
        except sqlite3.Error as error:
            raise cls.storage_error(error) from error
        diagnostics.log_event("storage", "storage.open", "succeeded")
        return store

    def initialize_schema(self) -> None:
        with self.lock:
            self.ensure_open()
            try:
                version = self.connection.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    self.connection.execute("BEGIN IMMEDIATE")
                    try:
                        self.connection.execute(
                            "CREATE TABLE sessions(session_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, event_cursor INTEGER NOT NULL CHECK(event_cursor >= 0))"
                        )
                        self.connection.execute("PRAGMA user_version=1")
                        self.connection.commit()
                    except sqlite3.Error:
                        self.connection.rollback()
                        raise
                elif version != 1:
                    raise StorageUnavailable
                self.connection.execute(
                    "SELECT session_id, created_at, event_cursor FROM sessions LIMIT 1"
                )
            except sqlite3.Error as error:
                raise self.storage_error(error) from error
        self.diagnostics.log_event("storage", "storage.schema", "succeeded")

    def create(self, session: Session) -> Session:
        with self.lock:
            self.ensure_open()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute(
                    "INSERT INTO sessions(session_id, created_at, event_cursor) VALUES (?, ?, ?)",
                    (
                        session.session_id,
                        session.created_at.isoformat(),
                        session.event_cursor,
                    ),
                )
                self.connection.commit()
            except sqlite3.Error as error:
                self.connection.rollback()
                raise self.storage_error(error) from error
        self.diagnostics.log_event(
            "storage",
            "storage.session_insert",
            "succeeded",
            session_id=session.session_id,
        )
        return session

    def get(self, session_id: str) -> Session:
        with self.lock:
            self.ensure_open()
            try:
                row = self.connection.execute(
                    "SELECT session_id, created_at, event_cursor FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            except sqlite3.Error as error:
                raise self.storage_error(error) from error
        if row is None:
            raise SessionNotFound
        return self.session_from_row(row)

    def advance_cursor(self, session_id: str) -> Session:
        with self.lock:
            self.ensure_open()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                cursor = self.connection.execute(
                    "UPDATE sessions SET event_cursor = event_cursor + 1 WHERE session_id = ? RETURNING session_id, created_at, event_cursor",
                    (session_id,),
                ).fetchone()
                if cursor is None:
                    self.connection.rollback()
                    raise SessionNotFound
                self.connection.commit()
            except SessionNotFound:
                raise
            except sqlite3.Error as error:
                self.connection.rollback()
                raise self.storage_error(error) from error
        session = self.session_from_row(cursor)
        self.diagnostics.log_event(
            "storage",
            "storage.cursor_advance",
            "succeeded",
            session_id=session_id,
            server_event_id=f"e{session.event_cursor}",
        )
        return session

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.connection.close()
            self.closed = True
        self.diagnostics.log_event("storage", "storage.close", "succeeded")

    def ensure_open(self) -> None:
        if self.closed:
            raise StoreClosed

    @staticmethod
    def session_from_row(row: tuple[str, str, int]) -> Session:
        return Session(
            session_id=row[0],
            created_at=datetime.fromisoformat(row[1]),
            event_cursor=row[2],
        )

    @staticmethod
    def storage_error(error: sqlite3.Error) -> StorageBusy | StorageUnavailable:
        if (
            isinstance(error, sqlite3.OperationalError)
            and "locked" in str(error).lower()
        ):
            return StorageBusy()
        return StorageUnavailable()
