import asyncio
import json
from pathlib import Path

import pytest
from livekit.agents import ToolError

from tests.test_agent_approval import (
    APPROVAL,
    FakeSession,
    Stand,
    activate,
    facts,
    finished,
    settle,
    spoken,
    stopped,
    until,
    waiting,
)
from voca.conversation.agent import (
    APPROVAL_GONE,
    NO_REQUEST,
    NOT_STEERABLE,
    TASK_AMBIGUOUS,
    TASK_CLOSED,
    announcement_text,
)
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
        "cancel_task",
        "steer_task",
        "deny_approval",
        "repeat_approval",
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
            "active": False,
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


def test_deny_approval_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"), waiting("b", created_at=2.0))
        await settle()
        output = json.loads(await stand.agent.deny_approval())
        assert output == {"id": "a", "event": "denied"}
        assert stand.backend.approved == [("run_a", "deny", APPROVAL)]
        await until(lambda: len(session.calls) == 2)
        assert spoken(session)[-1] == announcement_text(waiting("b", created_at=2.0))
        rows = [row for row in stand.rows("a") if "approval" in row]
        assert [(row["approval"], row["by"]) for row in rows] == [("deny", "model")]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_repeat_approval(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        assert await stand.agent.repeat_approval() is None
        await until(lambda: len(session.calls) == 2)
        await until(lambda: stand.agent.active is not None)
        assert spoken(session) == [announcement_text(waiting("a"))] * 2
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_repeat_without_request(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        with pytest.raises(ToolError, match=NO_REQUEST):
            await stand.agent.repeat_approval()

    asyncio.run(main())


def test_approve_from_page(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        result = await stand.agent.approve_from_page("a", "p1", "once")
        assert result == {"ok": True}
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert stand.agent.active is None
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "approved", "by": "button"},
        )
        rows = [row for row in stand.rows("a") if "approval" in row]
        assert [(row["approval"], row["by"]) for row in rows] == [("once", "button")]
        await settle()
        assert len(session.calls) == 1
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_approve_from_page_queued_request(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"), waiting("b", created_at=2.0))
        result = await stand.agent.approve_from_page("b", "p1", "deny")
        assert result == {"ok": True}
        assert stand.backend.approved == [("run_b", "deny", APPROVAL)]
        assert not stand.agent.announcements
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stopped(stand.agent, "Подтверждаю")
        await settle()
        assert spoken(session) == [announcement_text(waiting("a")), "Подтверждено"]
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


def test_deny_wrong_state_with_id(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(running("a"))
        with pytest.raises(ToolError, match=NO_REQUEST):
            await stand.agent.deny_approval("a")
        assert stand.backend.approved == []

    asyncio.run(main())
