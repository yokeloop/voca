import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from livekit.agents import APIConnectionError
from livekit.agents.llm import LLMError
from livekit.agents.metrics import LLMMetrics
from livekit.agents.stt import STTError
from livekit.agents.tts import TTSError
from livekit.agents.voice import ErrorEvent, MetricsCollectedEvent

from voca.clientio.cards import (
    CardState,
    component_of,
    task_item,
    tasks_message,
)
from voca.conversation.instructions import InstructionSet
from voca.delegation.model import Task, TaskStatus
from voca.delegation.sqlite import SqliteTaskRepository

INSTRUCTIONS = InstructionSet("text", "a" * 64, "fresh", None)
APPROVAL = {
    "description": "d",
    "command": "rm -r x",
    "choices": ["once", "deny"],
    "request_id": "r1",
}


def llm_metrics() -> LLMMetrics:
    return LLMMetrics(
        label="openai",
        request_id="r",
        timestamp=0.0,
        duration=1.0,
        ttft=0.8,
        cancelled=False,
        completion_tokens=20,
        prompt_tokens=900,
        prompt_cached_tokens=850,
        total_tokens=920,
        tokens_per_second=20.0,
    )


def error_event(error: Any) -> ErrorEvent:
    return ErrorEvent(error=error, source=None)


def llm_error(recoverable: bool) -> LLMError:
    return LLMError(
        timestamp=0.0, label="llm", error=APIConnectionError(), recoverable=recoverable
    )


Sent = list[tuple[str, str, list[str] | None]]


class FakeParticipant:
    def __init__(self) -> None:
        self.sent: Sent = []

    async def send_text(
        self,
        text: str,
        *,
        topic: str,
        destination_identities: list[str] | None = None,
    ) -> None:
        self.sent.append((text, topic, destination_identities))


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.local_participant = FakeParticipant()

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers[event] = handler


class FakeRemote:
    def __init__(self, identity: str) -> None:
        self.identity = identity


TASKS = [
    Task("call_a", "a", TaskStatus.SUCCEEDED, 2.0, result="готово"),
    Task("call_b", "b", TaskStatus.FAILED, 1.0, error="timeout"),
    Task(
        "call_c",
        "c",
        TaskStatus.WAITING_APPROVAL,
        3.0,
        run_id="run_1",
        approval=APPROVAL,
    ),
]


async def task_repo(path: Path) -> SqliteTaskRepository:
    repo = SqliteTaskRepository.open(path)
    for task in TASKS:
        await repo.insert(task)
    return repo


def test_message_schema() -> None:
    message = json.loads(CardState(INSTRUCTIONS).message())
    assert message == {
        "type": "state",
        "instructions_sha256": "a" * 64,
        "instructions_source": "fresh",
        "instructions_error": None,
        "components": {"stt": "ok", "llm": "ok", "tts": "ok"},
    }


def test_component_of() -> None:
    error = APIConnectionError()
    assert component_of(llm_error(False)) == "llm"
    stt = STTError(timestamp=0.0, label="stt", error=error, recoverable=False)
    tts = TTSError(timestamp=0.0, label="tts", error=error, recoverable=False)
    assert component_of(stt) == "stt"
    assert component_of(tts) == "tts"
    assert component_of(object()) is None


def test_down_and_ok() -> None:
    cards = CardState(INSTRUCTIONS)
    assert cards.on_error(error_event(llm_error(True))) is None
    assert cards.components["llm"] == "ok"
    assert cards.on_error(error_event(llm_error(False))) == "llm"
    assert cards.components["llm"] == "down"
    cards.on_metrics(MetricsCollectedEvent(metrics=llm_metrics()))
    assert cards.components["llm"] == "ok"


def test_publish_to_room() -> None:
    async def main() -> Sent:
        room = FakeRoom()
        cards = CardState(INSTRUCTIONS)
        cards.bind(room)  # type: ignore[arg-type]
        cards.on_error(error_event(llm_error(False)))
        room.handlers["participant_connected"](FakeRemote("user-phone"))
        assert cards.sending is not None
        await cards.sending
        cards.set_instructions(INSTRUCTIONS)
        await cards.sending
        return room.local_participant.sent

    sent = asyncio.run(main())
    assert len(sent) == 2
    assert all(topic == "voca.cards" for _, topic, _ in sent)
    assert json.loads(sent[0][0])["components"]["llm"] == "down"
    assert json.loads(sent[1][0])["components"]["llm"] == "down"


def test_task_item_schema() -> None:
    done = Task(
        "call_1",
        "сделай",
        TaskStatus.SUCCEEDED,
        1.0,
        result="готово",
        delivered_at=5.0,
    )
    assert json.loads(json.dumps(task_item(done))) == {
        "tool_call_id": "call_1",
        "status": "succeeded",
        "text": "сделай",
        "result": "готово",
        "error": None,
        "created_at": 1.0,
        "delivered_at": 5.0,
        "approval": None,
    }
    waiting = Task(
        "call_2",
        "удали",
        TaskStatus.WAITING_APPROVAL,
        2.0,
        run_id="run_1",
        approval=APPROVAL,
    )
    item = task_item(waiting)
    assert item["approval"] == {
        "description": "d",
        "command": "rm -r x",
        "choices": ["once", "deny"],
    }
    assert "run_id" not in item
    assert "stop_requested_at" not in item
    assert "request_id" not in item
    assert "request_id" not in item["approval"]
    text = tasks_message([done, waiting])
    assert json.loads(text)["type"] == "tasks"
    assert [item["tool_call_id"] for item in json.loads(text)["tasks"]] == [
        "call_1",
        "call_2",
    ]
    assert "сделай" in text
    assert "\\u" not in text


def test_publish_task_order() -> None:
    task = Task("call_1", "сделай", TaskStatus.RUNNING, 1.0, run_id="run_1")

    async def main() -> Sent:
        room = FakeRoom()
        cards = CardState(INSTRUCTIONS)
        cards.bind(room)  # type: ignore[arg-type]
        cards.publish()
        cards.publish_task(task)
        assert cards.sending is not None
        await cards.sending
        return room.local_participant.sent

    sent = asyncio.run(main())
    assert len(sent) == 2
    assert all(topic == "voca.cards" for _, topic, _ in sent)
    assert json.loads(sent[0][0])["type"] == "state"
    second = json.loads(sent[1][0])
    assert second["type"] == "task"
    assert second["tool_call_id"] == "call_1"
    unbound = CardState(INSTRUCTIONS)
    unbound.publish_task(task)
    assert unbound.sending is None


def test_sends_tasks_to_connected_participant(tmp_path: Path) -> None:
    async def main() -> Sent:
        repo = await task_repo(tmp_path / "tasks.sqlite3")
        try:
            room = FakeRoom()
            cards = CardState(INSTRUCTIONS)
            cards.bind(room)  # type: ignore[arg-type]
            cards.bind_tasks(repo)
            room.handlers["participant_connected"](FakeRemote("user-phone"))
            assert cards.sending is not None
            await cards.sending
            return room.local_participant.sent
        finally:
            repo.close()

    sent = asyncio.run(main())
    assert len(sent) == 2
    assert all(topic == "voca.cards" for _, topic, _ in sent)
    assert json.loads(sent[0][0])["type"] == "state"
    assert sent[0][2] is None
    text, _, destinations = sent[1]
    assert destinations == ["user-phone"]
    message = json.loads(text)
    assert message["type"] == "tasks"
    items = message["tasks"]
    assert [item["created_at"] for item in items] == [3.0, 2.0, 1.0]
    assert items[0]["approval"]["command"] == "rm -r x"
    assert items[2]["error"] == "timeout"


def test_sends_tasks_after_wait(tmp_path: Path) -> None:
    async def main() -> Sent:
        repo = await task_repo(tmp_path / "tasks.sqlite3")
        try:
            room = FakeRoom()
            cards = CardState(INSTRUCTIONS)
            cards.bind(room)  # type: ignore[arg-type]
            cards.bind_tasks(repo)
            cards.publish()
            cards.send_tasks("user-host")
            assert cards.sending is not None
            await cards.sending
            return room.local_participant.sent
        finally:
            repo.close()

    sent = asyncio.run(main())
    assert [json.loads(text)["type"] for text, _, _ in sent] == ["state", "tasks"]
    assert sent[1][2] == ["user-host"]
    assert len(json.loads(sent[1][0])["tasks"]) == 3


def test_no_tasks_without_store() -> None:
    async def main() -> Sent:
        room = FakeRoom()
        cards = CardState(INSTRUCTIONS)
        cards.bind(room)  # type: ignore[arg-type]
        room.handlers["participant_connected"](FakeRemote("user-phone"))
        cards.send_tasks("user-phone")
        assert cards.sending is not None
        await cards.sending
        return room.local_participant.sent

    sent = asyncio.run(main())
    assert [json.loads(text)["type"] for text, _, _ in sent] == ["state"]
