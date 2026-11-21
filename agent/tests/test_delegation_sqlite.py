import asyncio
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from voca.delegation.model import Task, TaskStatus
from voca.delegation.sqlite import MIGRATIONS, SqliteTaskRepository, TaskStoreError


def test_file_and_dir_modes(tmp_path: Path) -> None:
    path = tmp_path / "new" / "tasks.sqlite3"
    SqliteTaskRepository.open(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_wal_and_user_version(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    SqliteTaskRepository.open(path).close()
    SqliteTaskRepository.open(path).close()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version = 3")
    with pytest.raises(TaskStoreError):
        SqliteTaskRepository.open(path)


def test_migrates_v1_to_v2(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    with closing(sqlite3.connect(path, autocommit=True)) as db:
        db.executescript(MIGRATIONS[0].read_text(encoding="utf-8"))
        db.execute("PRAGMA user_version = 1")
        db.execute(
            "INSERT INTO tasks (tool_call_id, text, status, created_at, stop_requested_at) "
            "VALUES ('stopped', 'a', 'failed', 1.0, 2.0)"
        )
        db.execute(
            "INSERT INTO tasks (tool_call_id, text, status, created_at) "
            "VALUES ('plain', 'b', 'running', 1.0)"
        )
    repo = SqliteTaskRepository.open(path)

    async def main() -> tuple[Task | None, Task | None]:
        return await repo.get("stopped"), await repo.get("plain")

    stopped, plain = asyncio.run(main())
    repo.close()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert stopped is not None
    assert stopped.stop_reason == "timeout"
    assert plain is not None
    assert plain.stop_reason is None


def test_insert_or_get(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    repo = SqliteTaskRepository.open(path)

    async def main() -> tuple[tuple[Task, bool], tuple[Task, bool]]:
        first = await repo.insert(Task("k", "первый", TaskStatus.QUEUED, 1.0))
        second = await repo.insert(Task("k", "второй", TaskStatus.QUEUED, 2.0))
        return first, second

    (first, created), (second, again) = asyncio.run(main())
    repo.close()
    assert created
    assert not again
    assert second == first
    assert second.text == "первый"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_save_and_lists(tmp_path: Path) -> None:
    repo = SqliteTaskRepository.open(tmp_path / "tasks.sqlite3")
    approval = {"description": "удалить", "command": "rm -r x", "choices": ["once"]}
    one = Task("1", "a", TaskStatus.QUEUED, 1.0)
    two = Task("2", "b", TaskStatus.QUEUED, 2.0)
    three = Task("3", "c", TaskStatus.QUEUED, 3.0)

    async def main() -> tuple[Task | None, list[Task], list[Task], list[Task]]:
        for task in (one, two, three):
            await repo.insert(task)
        await repo.save(
            two.transition(TaskStatus.RUNNING, run_id="r2").transition(
                TaskStatus.SUCCEEDED, result="готово"
            )
        )
        await repo.save(
            three.transition(TaskStatus.FAILED, error="unavailable", delivered_at=5.0)
        )
        waiting = one.transition(TaskStatus.RUNNING, run_id="r1").transition(
            TaskStatus.WAITING_APPROVAL,
            approval=approval,
            stop_requested_at=9.0,
            stop_reason="user",
        )
        await repo.save(waiting)
        stored = await repo.get("1")
        await repo.save(waiting.transition(TaskStatus.RUNNING, approval=None))
        return (
            stored,
            await repo.list_open(),
            await repo.list_undelivered(),
            await repo.list_all(),
        )

    stored, open_tasks, undelivered, everything = asyncio.run(main())
    repo.close()
    assert stored is not None
    assert stored.approval == approval
    assert stored.status == TaskStatus.WAITING_APPROVAL
    assert stored.stop_requested_at == 9.0
    assert stored.stop_reason == "user"
    assert [task.tool_call_id for task in open_tasks] == ["1"]
    assert open_tasks[0].approval is None
    assert [task.tool_call_id for task in undelivered] == ["2"]
    assert undelivered[0].result == "готово"
    assert undelivered[0].run_id == "r2"
    assert [task.tool_call_id for task in everything] == ["3", "2", "1"]
    assert everything[0].delivered_at == 5.0
    assert everything[0].error == "unavailable"
