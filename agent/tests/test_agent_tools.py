import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError

from tests.test_agent_approval import (
    APPROVAL,
    FakeSession,
    FakeSorter,
    Stand,
    ctx,
    facts,
    finished,
    request,
    settle,
    sorted_,
    turn,
    until,
    waiting,
)
from voca.conversation.agent import (
    APPROVAL_GONE,
    BUTTON_PROMPT,
    DELEGATE_UNAVAILABLE,
    DENIED_NOTE,
    NOT_STEERABLE,
    TASK_AMBIGUOUS,
    TASK_CLOSED,
)
from voca.delegation.danger import Danger
from voca.delegation.events import Event, EventKind, EventState
from voca.delegation.model import Task, TaskStatus
from voca.delegation.ports import RunState


def running(key: str, created_at: float = 1.0) -> Task:
    return Task(
        key,
        f"Поручение {key}",
        TaskStatus.RUNNING,
        created_at=created_at,
        run_id=f"run_{key}",
    )


def test_tool_names(tmp_path: Path) -> None:
    stand = Stand(tmp_path)
    names = {tool.info.name for tool in stand.agent.tools}
    assert names == {
        "delegate",
        "list_tasks",
        "list_notifications",
        "cancel_task",
        "steer_task",
        "open_approval",
    }
    assert not any("approve" in name for name in names)


def test_list_tasks_open_and_recent(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        for n in range(7):
            stand.put(finished(f"done{n}", created_at=float(n)))
        stand.put(running("b", created_at=20.0))
        stand.put(waiting("a", created_at=10.0))
        listed = json.loads(await stand.agent.list_tasks())["tasks"]
        assert [task["id"] for task in listed] == [
            "a",
            "b",
            "done6",
            "done5",
            "done4",
            "done3",
            "done2",
        ]
        assert listed[0]["status"] == "waiting_approval"
        assert listed[0]["approval"] == {
            "request": "удаление в корневом каталоге",
            "command": "rm -r /tmp/x",
        }
        assert listed[2]["result"] == "Ответ."

    asyncio.run(main())


def test_list_tasks_clips_result(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(finished("a", "слово " * 3800))
        [task] = json.loads(await stand.agent.list_tasks())["tasks"]
        assert 0 < len(task["result"]) <= 300

    asyncio.run(main())


def test_list_tasks_marks_denied(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(finished("a"))
        stand.put(finished("b", created_at=2.0))
        stand.repo.events = {
            ("a", "approval", "p1"): Event(
                "a",
                EventKind.APPROVAL,
                "p1",
                EventState.CLOSED,
                1.0,
                2.0,
                Danger.HIGH,
                closed_reason="denied",
                id=1,
            ),
        }
        listed = json.loads(await stand.make_agent().list_tasks())["tasks"]
        views = {task["id"]: task for task in listed}
        assert views["a"]["status"] == "succeeded"
        assert views["a"]["approval"] == "denied"
        assert "approval" not in views["b"]

    asyncio.run(main())


def test_list_tasks_waiting_after_deny_keeps_request(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a", {**APPROVAL, "request_id": "p2"}))
        stand.repo.events = {
            ("a", "approval", "p1"): Event(
                "a",
                EventKind.APPROVAL,
                "p1",
                EventState.CLOSED,
                1.0,
                2.0,
                Danger.HIGH,
                closed_reason="denied",
                id=1,
            ),
            ("a", "approval", "p2"): Event(
                "a",
                EventKind.APPROVAL,
                "p2",
                EventState.ARRIVED,
                3.0,
                3.0,
                Danger.HIGH,
                id=2,
            ),
        }
        [view] = json.loads(await stand.agent.list_tasks())["tasks"]
        assert view["status"] == "waiting_approval"
        assert view["approval"] == {
            "request": "удаление в корневом каталоге",
            "command": "rm -r /tmp/x",
        }

    asyncio.run(main())


def test_list_notifications_open_events(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a"))
        stand.put(finished("b"))
        stand.repo.events = {
            ("a", "approval", "r1"): Event(
                "a",
                EventKind.APPROVAL,
                "r1",
                EventState.ARRIVED,
                1.0,
                1.0,
                Danger.HIGH,
                id=1,
            ),
            ("b", "result", ""): Event(
                "b", EventKind.RESULT, "", EventState.ARRIVED, 2.0, 2.0, id=2
            ),
            ("b", "failure", ""): Event(
                "b",
                EventKind.FAILURE,
                "",
                EventState.CLOSED,
                0.5,
                3.0,
                closed_reason="delivered",
                id=3,
            ),
        }
        listed = json.loads(await stand.agent.list_notifications())
        assert list(listed) == ["notifications"]
        first, second = listed["notifications"]
        assert first == {
            "id": "1",
            "task": "a",
            "kind": "approval",
            "state": "arrived",
            "age_s": 9,
            "text": "Удали каталог /tmp/x",
            "danger": "high",
            "request": "удаление в корневом каталоге",
            "command": "rm -r /tmp/x",
        }
        assert second == {
            "id": "2",
            "task": "b",
            "kind": "result",
            "state": "arrived",
            "age_s": 8,
            "text": "t",
            "result": "Ответ.",
        }

    asyncio.run(main())


def test_list_notifications_unavailable(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.agent.delegation = None
        with pytest.raises(ToolError, match=DELEGATE_UNAVAILABLE):
            await stand.agent.list_notifications()

    asyncio.run(main())


def test_cancel_task(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        stand.open()
        stand.put(running("a"))
        stand.backend.stops = [RunState("run_a", TaskStatus.FAILED, error="cancelled")]
        output = json.loads(await stand.agent.cancel_task())
        assert output["id"] == "a"
        assert output["status"] == "failed"
        assert output["error"] == "отменено пользователем"
        assert stand.backend.stopped == [("run_a", "user")]
        assert stand.repo.tasks["a"].delivered_at is not None
        await settle()
        assert session.calls == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_cancel_task_ambiguous(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(running("call_a1"))
        stand.put(running("call_b2", created_at=2.0))
        stand.backend.stops = [
            RunState("run_call_b2", TaskStatus.FAILED, error="cancelled")
        ]
        with pytest.raises(ToolError, match=TASK_AMBIGUOUS):
            await stand.agent.cancel_task()
        with pytest.raises(ToolError, match=TASK_AMBIGUOUS):
            await stand.agent.cancel_task("call_")
        await stand.agent.cancel_task("call_b")
        assert stand.backend.stopped == [("run_call_b2", "user")]
        assert stand.repo.tasks["call_b2"].status == TaskStatus.FAILED
        assert stand.repo.tasks["call_a1"].status == TaskStatus.RUNNING

    asyncio.run(main())


def test_cancel_closed_task(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(finished("a"))
        with pytest.raises(ToolError, match=TASK_CLOSED):
            await stand.agent.cancel_task("a")
        assert stand.backend.stopped == []

    asyncio.run(main())


def test_steer_task(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(running("a"))
        output = json.loads(await stand.agent.steer_task("добавь b.txt"))
        assert output == {"id": "a", "event": "steered", "accepted": True}
        assert stand.backend.steers == [("run_a", "добавь b.txt")]

    asyncio.run(main())


def test_steer_not_running(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a"))
        with pytest.raises(ToolError, match=NOT_STEERABLE):
            await stand.agent.steer_task("добавь b.txt")
        assert stand.backend.steers == []

    asyncio.run(main())


def test_approve_from_page(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        await request(stand, waiting("a"))
        await until(lambda: len(session.replies) == 1)
        result = await stand.agent.approve_from_page("a", "p1", "once")
        assert result == {"ok": True}
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "approved", "by": "button"},
        )
        rows = [row for row in stand.rows("a") if "approval" in row]
        assert [(row["approval"], row["by"]) for row in rows] == [("once", "button")]
        await settle()
        assert len(session.replies) == 2
        assert session.replies[1][0].startswith(BUTTON_PROMPT)
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delegate_again_after_deny(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        await request(stand, waiting("a"))
        await until(lambda: len(session.replies) == 1)
        assert await stand.agent.approve_from_page("a", "p1", "deny") == {"ok": True}
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "denied", "by": "button", "note": DENIED_NOTE},
        )
        assert stand.repo.events[("a", "approval", "p1")].closed_reason == "denied"
        stand.backend.created = ["run_b"]
        stand.backend.runs["run_b"] = [RunState("run_b", TaskStatus.RUNNING)]
        handle = SimpleNamespace(interrupted=False)
        await stand.agent.delegate(ctx("b", handle), "Удали каталог /tmp/x")
        assert "b" in stand.repo.tasks
        assert ("b", "Удали каталог /tmp/x") in stand.backend.posts
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_approve_from_page_second_request(tmp_path: Path) -> None:
    async def main() -> None:
        sorter = FakeSorter({"Да, удаляй": sorted_(confidence=0.97)})
        stand = Stand(tmp_path, sorter=sorter)
        await request(stand, waiting("a"))
        await request(stand, waiting("b", created_at=2.0))
        result = await stand.agent.approve_from_page("b", "p1", "deny")
        assert result == {"ok": True}
        assert stand.backend.approved == [("run_b", "deny", APPROVAL)]
        assert stand.agent.waiting["a"] == "p1"
        pairs = await stand.agent._requests()
        assert [event.tool_call_id for event, _ in pairs] == ["a"]
        await turn(stand, "Да, удаляй")
        assert stand.backend.approved == [
            ("run_b", "deny", APPROVAL),
            ("run_a", "once", APPROVAL),
        ]
        assert sorter.counts == [1]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_approve_from_page_wrong_request(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a"))
        result = await stand.agent.approve_from_page("a", "old", "once")
        assert result == {"ok": False, "error": APPROVAL_GONE}
        assert stand.backend.approved == []

    asyncio.run(main())


def test_cancel_from_page(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        stand.open()
        stand.put(running("a"))
        stand.backend.stops = [RunState("run_a", TaskStatus.FAILED, error="cancelled")]
        assert await stand.agent.cancel_from_page("a") == {"ok": True}
        await until(lambda: bool(facts(stand.agent.chat_ctx)))
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {
                "id": "a",
                "event": "cancelled",
                "by": "button",
                "status": "failed",
                "error": "отменено пользователем",
            },
        )
        assert stand.repo.tasks["a"].delivered_at is not None
        await settle()
        assert session.calls == []
        assert await stand.agent.cancel_from_page("a") == {
            "ok": False,
            "error": TASK_CLOSED,
        }
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_steer_wrong_state_with_id(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a"))
        stand.put(running("b", created_at=2.0))
        with pytest.raises(ToolError, match=NOT_STEERABLE):
            await stand.agent.steer_task("добавь b.txt", "a")
        assert stand.backend.steers == []

    asyncio.run(main())
