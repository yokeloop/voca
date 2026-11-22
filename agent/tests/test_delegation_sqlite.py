import asyncio
import sqlite3
import stat
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from voca.delegation.danger import Danger
from voca.delegation.events import Event, EventKind, EventState, arrival
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version = 4")
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert stopped is not None
    assert stopped.stop_reason == "timeout"
    assert plain is not None
    assert plain.stop_reason is None


def test_migrates_v2_to_v3(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    with closing(sqlite3.connect(path, autocommit=True)) as db:
        db.executescript(MIGRATIONS[0].read_text(encoding="utf-8"))
        db.executescript(MIGRATIONS[1].read_text(encoding="utf-8"))
        db.execute("PRAGMA user_version = 2")
        db.execute(
            "INSERT INTO tasks (tool_call_id, text, status, result, created_at) "
            "VALUES ('ok', 'a', 'succeeded', 'готово', 1.0)"
        )
        db.execute(
            "INSERT INTO tasks (tool_call_id, text, status, error, created_at, "
            "delivered_at, stop_reason) "
            "VALUES ('cancelled', 'b', 'failed', 'cancelled', 2.0, 5.0, 'user')"
        )
        db.execute(
            "INSERT INTO tasks (tool_call_id, text, status, run_id, created_at, approval) "
            "VALUES ('asks', 'c', 'waiting_approval', 'r', 3.0, ?)",
            ('{"description": "d", "command": "rm -r x", "request_id": "r1"}',),
        )
        db.execute(
            "INSERT INTO tasks (tool_call_id, text, status, created_at) "
            "VALUES ('plain', 'd', 'running', 4.0)"
        )
    repo = SqliteTaskRepository.open(path)

    async def main() -> tuple[list[Task | None], list[Event]]:
        tasks = [await repo.get(key) for key in ("ok", "cancelled", "asks", "plain")]
        return tasks, await repo.list_events(open_only=False)

    (ok, cancelled, asks, plain), events = asyncio.run(main())
    repo.close()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert ok == Task("ok", "a", TaskStatus.SUCCEEDED, 1.0, result="готово")
    assert cancelled == Task(
        "cancelled",
        "b",
        TaskStatus.FAILED,
        2.0,
        error="cancelled",
        delivered_at=5.0,
        stop_reason="user",
    )
    assert asks == Task(
        "asks",
        "c",
        TaskStatus.WAITING_APPROVAL,
        3.0,
        run_id="r",
        approval={"description": "d", "command": "rm -r x", "request_id": "r1"},
    )
    assert plain == Task("plain", "d", TaskStatus.RUNNING, 4.0)
    assert [
        (
            event.tool_call_id,
            event.kind,
            event.request_id,
            event.danger,
            event.state,
            event.updated_at,
            event.closed_reason,
        )
        for event in events
    ] == [
        ("ok", EventKind.RESULT, "", None, EventState.ARRIVED, 1.0, None),
        (
            "cancelled",
            EventKind.FAILURE,
            "",
            None,
            EventState.CLOSED,
            5.0,
            "cancelled",
        ),
        ("asks", EventKind.APPROVAL, "r1", Danger.HIGH, EventState.ARRIVED, 3.0, None),
    ]


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
            ),
            4.0,
        )
        await repo.save(
            three.transition(TaskStatus.FAILED, error="unavailable", delivered_at=5.0),
            5.0,
        )
        waiting = one.transition(TaskStatus.RUNNING, run_id="r1").transition(
            TaskStatus.WAITING_APPROVAL,
            approval=approval,
            stop_requested_at=9.0,
            stop_reason="user",
        )
        await repo.save(waiting, 6.0)
        stored = await repo.get("1")
        await repo.save(waiting.transition(TaskStatus.RUNNING, approval=None), 7.0)
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


def result_event(key: str, at: float) -> Event:
    return Event(key, EventKind.RESULT, "", EventState.ARRIVED, at, at)


def approval_event(key: str, request: str, at: float) -> Event:
    return Event(
        key, EventKind.APPROVAL, request, EventState.ARRIVED, at, at, Danger.HIGH
    )


def test_save_records_event_once(tmp_path: Path) -> None:
    repo = SqliteTaskRepository.open(tmp_path / "tasks.sqlite3")
    task = Task("k", "a", TaskStatus.QUEUED, 1.0)
    done = task.transition(TaskStatus.FAILED, error="lost")

    async def main() -> list[Event]:
        await repo.insert(task)
        await repo.save(done, 2.0, result_event("k", 2.0))
        await repo.save(done, 3.0, result_event("k", 3.0))
        return await repo.list_events(open_only=False)

    events = asyncio.run(main())
    repo.close()
    assert len(events) == 1
    assert events[0].created_at == 2.0


def test_delivered_closes_result_event(tmp_path: Path) -> None:
    repo = SqliteTaskRepository.open(tmp_path / "tasks.sqlite3")
    task = Task("k", "a", TaskStatus.QUEUED, 1.0)
    done = task.transition(TaskStatus.RUNNING, run_id="r").transition(
        TaskStatus.SUCCEEDED, result="ok"
    )

    async def main() -> list[Event]:
        await repo.insert(task)
        await repo.save(done, 2.0, result_event("k", 2.0))
        await repo.save(replace(done, delivered_at=4.0), 4.5)
        return await repo.list_events(open_only=False)

    events = asyncio.run(main())
    repo.close()
    assert [
        (event.state, event.closed_reason, event.updated_at) for event in events
    ] == [(EventState.CLOSED, "delivered", 4.5)]


def test_new_request_closes_previous(tmp_path: Path) -> None:
    repo = SqliteTaskRepository.open(tmp_path / "tasks.sqlite3")
    task = Task("k", "a", TaskStatus.QUEUED, 1.0)
    running = task.transition(TaskStatus.RUNNING, run_id="r")
    first = running.transition(
        TaskStatus.WAITING_APPROVAL, approval={"description": "d", "request_id": "r1"}
    )
    second = replace(first, approval={"description": "d", "request_id": "r2"})

    async def main() -> tuple[list[Event], list[Event]]:
        await repo.insert(task)
        await repo.save(first, 2.0, approval_event("k", "r1", 2.0))
        await repo.save(second, 3.0, approval_event("k", "r2", 3.0))
        middle = await repo.list_events(open_only=False)
        await repo.save(second.transition(TaskStatus.RUNNING, approval=None), 4.0)
        return middle, await repo.list_events(open_only=False)

    middle, final = asyncio.run(main())
    repo.close()
    assert [
        (event.request_id, event.state, event.closed_reason) for event in middle
    ] == [
        ("r1", EventState.CLOSED, "resolved"),
        ("r2", EventState.ARRIVED, None),
    ]
    assert [
        (event.request_id, event.state, event.closed_reason, event.updated_at)
        for event in final
    ] == [
        ("r1", EventState.CLOSED, "resolved", 3.0),
        ("r2", EventState.CLOSED, "resolved", 4.0),
    ]


def test_list_events_open_only_in_order(tmp_path: Path) -> None:
    repo = SqliteTaskRepository.open(tmp_path / "tasks.sqlite3")
    tasks = [Task(key, key, TaskStatus.QUEUED, 1.0) for key in ("a", "b", "c")]

    async def main() -> tuple[list[Event], list[Event]]:
        for task in tasks:
            await repo.insert(task)
        late, early, closed = tasks
        await repo.save(
            late.transition(TaskStatus.FAILED, error="lost"),
            5.0,
            result_event("a", 5.0),
        )
        await repo.save(
            early.transition(TaskStatus.FAILED, error="lost"),
            2.0,
            result_event("b", 2.0),
        )
        await repo.save(
            closed.transition(TaskStatus.FAILED, error="lost", delivered_at=3.0),
            3.0,
            result_event("c", 3.0),
        )
        return await repo.list_events(open_only=True), await repo.list_events(
            open_only=False
        )

    open_events, everything = asyncio.run(main())
    repo.close()
    assert [event.tool_call_id for event in open_events] == ["b", "a"]
    assert [event.tool_call_id for event in everything] == ["b", "c", "a"]
    assert all(isinstance(event.id, int) for event in everything)


def test_events_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    repo = SqliteTaskRepository.open(path)
    task = Task("k", "a", TaskStatus.QUEUED, 1.0)

    async def save() -> None:
        await repo.insert(task)
        await repo.save(
            task.transition(TaskStatus.FAILED, error="lost"),
            2.0,
            result_event("k", 2.0),
        )

    asyncio.run(save())
    repo.close()
    reopened = SqliteTaskRepository.open(path)
    events = asyncio.run(reopened.list_events(True))
    reopened.close()
    assert [(event.tool_call_id, event.state) for event in events] == [
        ("k", EventState.ARRIVED)
    ]


def test_advance_event(tmp_path: Path) -> None:
    repo = SqliteTaskRepository.open(tmp_path / "tasks.sqlite3")
    task = Task("k", "a", TaskStatus.QUEUED, 1.0)
    waiting = task.transition(TaskStatus.RUNNING, run_id="r").transition(
        TaskStatus.WAITING_APPROVAL,
        approval={"description": "delete in root path", "request_id": "r1"},
    )
    second = replace(
        waiting, approval={"description": "delete in root path", "request_id": "r2"}
    )

    async def main() -> tuple[list[bool], list[Event], list[Event]]:
        await repo.insert(task)
        await repo.save(waiting, 2.0, arrival(waiting, 2.0))
        (event,) = await repo.list_events(open_only=True)
        assert event.id is not None
        steps = [
            await repo.advance_event(event.id, EventState.MENTIONED, 3.0),
            await repo.advance_event(event.id, EventState.ESSENCE, 4.0),
        ]
        middle = await repo.list_events(open_only=False)
        steps.append(await repo.advance_event(event.id, EventState.MENTIONED, 5.0))
        await repo.save(second, 6.0)
        steps.append(await repo.advance_event(event.id, EventState.ESSENCE, 7.0))
        steps.append(
            await repo.advance_event(event.id + 100, EventState.MENTIONED, 7.0)
        )
        return steps, middle, await repo.list_events(open_only=False)

    steps, middle, final = asyncio.run(main())
    repo.close()
    assert steps == [True, True, False, False, False]
    assert [(event.state, event.updated_at) for event in middle] == [
        (EventState.ESSENCE, 4.0)
    ]
    assert [
        (event.state, event.closed_reason, event.updated_at) for event in final
    ] == [(EventState.CLOSED, "resolved", 6.0)]
    assert [event.danger for event in final] == [Danger.HIGH]
