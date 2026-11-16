import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from voca.delegation.model import Task, TaskStatus

SCHEMA_VERSION = 1
MIGRATION = Path(__file__).parent / "migrations" / "001-tasks.sql"
COLUMNS = (
    "tool_call_id, text, status, run_id, result, error, "
    "created_at, delivered_at, stop_requested_at, approval"
)


class TaskStoreError(Exception):
    pass


def user_version(db: sqlite3.Connection) -> int:
    version: int = db.execute("PRAGMA user_version").fetchone()[0]
    return version


def dump_approval(task: Task) -> str | None:
    if task.approval is None:
        return None
    return json.dumps(task.approval, ensure_ascii=False)


def task_of(row: tuple[Any, ...]) -> Task:
    approval = row[9]
    return Task(
        tool_call_id=row[0],
        text=row[1],
        status=TaskStatus(row[2]),
        run_id=row[3],
        result=row[4],
        error=row[5],
        created_at=row[6],
        delivered_at=row[7],
        stop_requested_at=row[8],
        approval=None if approval is None else json.loads(approval),
    )


class SqliteTaskRepository:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.lock = asyncio.Lock()

    @classmethod
    def open(cls, path: Path) -> "SqliteTaskRepository":
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600))
        db = sqlite3.connect(path, timeout=5, autocommit=True, check_same_thread=False)
        try:
            version = user_version(db)
            if version > SCHEMA_VERSION:
                raise TaskStoreError(
                    f"схема базы задач новее приложения: user_version {version} > {SCHEMA_VERSION}"
                )
            if db.execute("PRAGMA journal_mode = WAL").fetchone()[0] != "wal":
                raise TaskStoreError("не включается WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                version = user_version(db)
                if version > SCHEMA_VERSION:
                    raise TaskStoreError(
                        f"схема базы задач новее приложения: user_version {version} > {SCHEMA_VERSION}"
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

    def close(self) -> None:
        self.db.close()

    def _insert(self, task: Task) -> tuple[Task, bool]:
        cursor = self.db.execute(
            f"INSERT INTO tasks ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tool_call_id) DO NOTHING",
            (
                task.tool_call_id,
                task.text,
                task.status.value,
                task.run_id,
                task.result,
                task.error,
                task.created_at,
                task.delivered_at,
                task.stop_requested_at,
                dump_approval(task),
            ),
        )
        if cursor.rowcount == 0:
            existing = self._get(task.tool_call_id)
            assert existing is not None
            return existing, False
        return task, True

    def _save(self, task: Task) -> None:
        self.db.execute(
            "UPDATE tasks SET status = ?, run_id = ?, result = ?, error = ?, "
            "delivered_at = ?, stop_requested_at = ?, approval = ? "
            "WHERE tool_call_id = ?",
            (
                task.status.value,
                task.run_id,
                task.result,
                task.error,
                task.delivered_at,
                task.stop_requested_at,
                dump_approval(task),
                task.tool_call_id,
            ),
        )

    def _get(self, key: str) -> Task | None:
        row = self.db.execute(
            f"SELECT {COLUMNS} FROM tasks WHERE tool_call_id = ?", (key,)
        ).fetchone()
        return None if row is None else task_of(row)

    def _select(self, where_order: str) -> list[Task]:
        rows = self.db.execute(f"SELECT {COLUMNS} FROM tasks {where_order}")
        return [task_of(row) for row in rows]

    async def insert(self, task: Task) -> tuple[Task, bool]:
        async with self.lock:
            return await asyncio.to_thread(self._insert, task)

    async def save(self, task: Task) -> None:
        async with self.lock:
            await asyncio.to_thread(self._save, task)

    async def get(self, key: str) -> Task | None:
        async with self.lock:
            return await asyncio.to_thread(self._get, key)

    async def list_open(self) -> list[Task]:
        async with self.lock:
            return await asyncio.to_thread(
                self._select,
                "WHERE status IN ('queued', 'running', 'waiting_approval', 'unknown') "
                "ORDER BY created_at",
            )

    async def list_undelivered(self) -> list[Task]:
        async with self.lock:
            return await asyncio.to_thread(
                self._select,
                "WHERE status IN ('succeeded', 'failed') AND delivered_at IS NULL "
                "ORDER BY created_at",
            )

    async def list_all(self) -> list[Task]:
        async with self.lock:
            return await asyncio.to_thread(self._select, "ORDER BY created_at DESC")
