from __future__ import annotations

import sqlite3

from voca.application.ports import StorageUnavailable

SCHEMA = "CREATE TABLE sessions(session_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, event_cursor INTEGER NOT NULL CHECK(event_cursor >= 0))"


def initialize_schema(connection: sqlite3.Connection) -> None:
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(SCHEMA)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
        elif version != 1 or not has_expected_schema(connection):
            raise StorageUnavailable
    except sqlite3.Error as error:
        raise StorageUnavailable from error


def has_expected_schema(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
    ).fetchone()
    return (
        row is not None
        and isinstance(row[0], str)
        and normalize_schema(row[0]) == normalize_schema(SCHEMA)
    )


def normalize_schema(schema: str) -> str:
    return "".join(schema.upper().split())
