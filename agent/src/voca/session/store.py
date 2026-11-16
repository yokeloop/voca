import json
import os
import sqlite3
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voca.session.model import ArchiveRow, archive_row, fold, snapshot_items

SCHEMA_VERSION = 1
MIGRATION = Path(__file__).parent / "migrations" / "001-session.sql"


class StoreError(Exception):
    pass


class SearchError(StoreError):
    pass


@dataclass(frozen=True)
class Session:
    id: str
    context: dict[str, Any]
    started_at: float
    compacted_at: float | None


@dataclass(frozen=True)
class Hit:
    id: int
    at: float
    kind: str
    snippet: str


def user_version(db: sqlite3.Connection) -> int:
    version: int = db.execute("PRAGMA user_version").fetchone()[0]
    return version


class SessionStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    @classmethod
    def open(cls, path: Path) -> "SessionStore":
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600))
        db = sqlite3.connect(path, timeout=5, autocommit=True)
        try:
            version = user_version(db)
            if version > SCHEMA_VERSION:
                raise StoreError(
                    f"схема хранилища новее приложения: user_version {version} > {SCHEMA_VERSION}"
                )
            if db.execute("PRAGMA journal_mode = WAL").fetchone()[0] != "wal":
                raise StoreError("не включается WAL")
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                version = user_version(db)
                if version > SCHEMA_VERSION:
                    raise StoreError(
                        f"схема хранилища новее приложения: user_version {version} > {SCHEMA_VERSION}"
                    )
                if version == 0:
                    db.executescript(MIGRATION.read_text(encoding="utf-8"))
                    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        except BaseException:
            db.close()
            raise
        os.chmod(path, 0o600)
        return cls(db)

    @classmethod
    def open_readonly(cls, path: Path) -> "SessionStore":
        if not path.exists():
            raise StoreError(f"нет файла сессий: {path}")
        db = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5, autocommit=True
        )
        try:
            version = user_version(db)
            if version != SCHEMA_VERSION:
                raise StoreError(
                    f"схема хранилища не совпадает: user_version {version}, нужна {SCHEMA_VERSION}"
                )
        except BaseException:
            db.close()
            raise
        return cls(db)

    def open_session(self) -> Session:
        row = self.db.execute(
            "SELECT id, context_json, started_at, compacted_at FROM sessions"
            " WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            session_id = uuid.uuid4().hex
            started_at = time.time()
            self.db.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, started_at),
            )
            return Session(
                id=session_id,
                context={"items": []},
                started_at=started_at,
                compacted_at=None,
            )
        session_id, context_json, started_at, compacted_at = row
        context = {"items": []} if context_json is None else json.loads(context_json)
        return Session(
            id=session_id,
            context=context,
            started_at=started_at,
            compacted_at=compacted_at,
        )

    def record(
        self,
        session_id: str,
        rows: Sequence[ArchiveRow],
        items: list[dict[str, Any]],
        *,
        compacted_at: float | None = None,
    ) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.executemany(
                "INSERT OR IGNORE INTO messages"
                " (session_id, at, kind, item_id, call_id, name, text, data_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        session_id,
                        row.at,
                        row.kind,
                        row.item_id,
                        row.call_id,
                        row.name,
                        row.text,
                        json.dumps(row.data, ensure_ascii=False),
                    )
                    for row in rows
                ],
            )
            self.db.execute(
                "UPDATE sessions SET context_json = ?, context_at = ? WHERE id = ?",
                (
                    json.dumps({"items": items}, ensure_ascii=False),
                    time.time(),
                    session_id,
                ),
            )
            if compacted_at is not None:
                self.db.execute(
                    "UPDATE sessions SET compacted_at = ?,"
                    " summary_count = summary_count + ? WHERE id = ?",
                    (
                        compacted_at,
                        sum(row.kind == "summary" for row in rows),
                        session_id,
                    ),
                )
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def recent(self, session_id: str, limit: int) -> list[ArchiveRow]:
        rows = self.db.execute(
            "SELECT kind, item_id, at, call_id, name, text, data_json"
            " FROM messages"
            " WHERE session_id = ? AND kind IN ('user', 'assistant', 'summary')"
            " ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            ArchiveRow(
                kind=kind,
                item_id=item_id,
                at=at,
                call_id=call_id,
                name=name,
                text=text,
                data=json.loads(data_json),
            )
            for kind, item_id, at, call_id, name, text, data_json in reversed(rows)
        ]

    def search(self, query: str, limit: int) -> list[Hit]:
        try:
            rows = self.db.execute(
                "SELECT m.id, m.at, m.kind, snippet(messages_fts, 0, '[', ']', '…', 12)"
                " FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
                " WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts), m.id LIMIT ?",
                (fold(query), limit),
            ).fetchall()
        except sqlite3.OperationalError as error:
            raise SearchError(str(error)) from error
        return [
            Hit(id=id, at=at, kind=kind, snippet=snippet)
            for id, at, kind, snippet in rows
        ]

    def close(self) -> None:
        self.db.close()


class SessionRecorder:
    def __init__(self, store: SessionStore, session_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.pending: list[ArchiveRow] = []
        self.compacted_at: float | None = None

    def add(self, item: dict[str, Any]) -> None:
        row = archive_row(item)
        if row is not None:
            self.pending.append(row)

    def add_row(self, row: ArchiveRow, *, compacted_at: float | None = None) -> None:
        self.pending.append(row)
        if compacted_at is not None:
            self.compacted_at = compacted_at

    def flush(self, context: dict[str, Any]) -> None:
        self.store.record(
            self.session_id,
            self.pending,
            snapshot_items(context["items"]),
            compacted_at=self.compacted_at,
        )
        self.compacted_at = None
        self.pending.clear()
