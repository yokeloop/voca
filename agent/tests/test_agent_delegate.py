import asyncio
import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from livekit.agents import RunContext, ToolError, llm, stt
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)

from voca.clientio.cards import CardState
from voca.config import DecisionConfig
from voca.conversation import agent as agent_module
from voca.conversation.agent import (
    EVENT_PROMPT,
    EVENT_SILENCE_S,
    REQUEST_PROMPT,
    SPEECH_LIMIT,
    UNHEARD_RETRY_S,
    VocaAgent,
    clip,
    events_prompt,
    last_message_at,
    notification_view,
    records,
    result_outcome,
)
from voca.conversation.compaction import Limits
from voca.conversation.instructions import InstructionLoader
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.conversation.verdict import DecisionFailure, Sorted
from voca.delegation.events import Event, EventKind, EventState, advance, arrival
from voca.delegation.model import Task, TaskStatus
from voca.delegation.ports import OutcomeUnknown, Rejected, RunState
from voca.delegation.service import DelegationService
from voca.session.store import SessionRecorder, SessionStore
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"
NOW = 10.0


class FakeRepo:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.events: dict[tuple[str, str, str], Event] = {}

    async def insert(self, task: Task) -> tuple[Task, bool]:
        if task.tool_call_id in self.tasks:
            return self.tasks[task.tool_call_id], False
        self.tasks[task.tool_call_id] = task
        return task, True

    async def save(self, task: Task, at: float, event: Event | None = None) -> None:
        self.tasks[task.tool_call_id] = task
        if event is not None:
            key = (event.tool_call_id, event.kind.value, event.request_id)
            if key not in self.events:
                self.events[key] = replace(event, id=len(self.events) + 1)

    async def get(self, key: str) -> Task | None:
        return self.tasks.get(key)

    async def list_open(self) -> list[Task]:
        found = [task for task in self.tasks.values() if task.is_open]
        return sorted(found, key=lambda task: task.created_at)

    async def list_undelivered(self) -> list[Task]:
        found = [
            task
            for task in self.tasks.values()
            if not task.is_open and task.delivered_at is None
        ]
        return sorted(found, key=lambda task: task.created_at)

    async def list_all(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda task: -task.created_at)

    async def list_events(self, open_only: bool) -> list[Event]:
        found = [
            event
            for event in self.events.values()
            if not open_only or event.state != EventState.CLOSED
        ]
        return sorted(found, key=lambda event: (event.created_at, event.id or 0))

    async def advance_event(self, event_id: int, state: EventState, at: float) -> bool:
        for key, event in self.events.items():
            if event.id == event_id:
                try:
                    self.events[key] = advance(event, state, at)
                except ValueError:
                    return False
                return True
        return False

    async def close_request(
        self, tool_call_id: str, request_id: str, reason: str, at: float
    ) -> None:
        key = (tool_call_id, "approval", request_id)
        event = self.events.get(key)
        if event is not None and (
            event.state != EventState.CLOSED or event.closed_reason == "resolved"
        ):
            self.events[key] = replace(
                event, state=EventState.CLOSED, closed_reason=reason, updated_at=at
            )


def take(queue: list[Any]) -> Any:
    item = queue.pop(0) if len(queue) > 1 else queue[0]
    if isinstance(item, Exception):
        raise item
    return item


class FakeBackend:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.runs: dict[str, list[Any]] = {}
        self.stops: list[Any] = [None]
        self.posts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str | None]] = []
        self.stopped: list[tuple[str, str]] = []
        self.resolved: list[tuple[str, dict[str, Any]]] = []
        self.steers: list[tuple[str, str]] = []
        self.steer_error: Exception | None = None

    async def create_run(self, key: str, text: str) -> str:
        self.posts.append((key, text))
        run_id: str = take(self.created)
        return run_id

    async def get_run(self, run_id: str, stop_reason: str | None) -> RunState:
        self.gets.append((run_id, stop_reason))
        state: RunState = take(self.runs[run_id])
        return state

    async def stop(self, run_id: str, reason: str) -> RunState | None:
        self.stopped.append((run_id, reason))
        state: RunState | None = take(self.stops)
        return state

    async def approve(
        self, run_id: str, choice: Literal["once", "deny"], approval: dict[str, Any]
    ) -> None:
        pass

    async def resolve_unknown(self, run_id: str, admission: dict[str, Any]) -> None:
        self.resolved.append((run_id, admission))

    async def steer(self, run_id: str, text: str) -> None:
        self.steers.append((run_id, text))
        if self.steer_error is not None:
            raise self.steer_error

    async def aclose(self) -> None:
        pass


class FakeSummarizer(Summarizer):
    def __init__(
        self, result: Summary | Failure, *, ready: bool = True, hold: bool = False
    ) -> None:
        super().__init__(None, model="fake")
        self.result = result
        self.prompts: list[str] = []
        self.gate = asyncio.Event()
        self.is_ready = ready
        if not hold:
            self.gate.set()

    @property
    def ready(self) -> bool:
        return self.is_ready

    async def summarize(self, prompt: str) -> Summary | Failure:
        self.prompts.append(prompt)
        await self.gate.wait()
        return self.result


LIMITS = Limits(threshold=500, tail_budget=300, tail_turns_min=2)


def add_pairs(ctx: llm.ChatContext, first: int, count: int, start: float) -> None:
    for n in range(first, first + count):
        ctx.add_message(
            role="user", content=f"факт {n} " + "у" * 300, created_at=start + 2 * n
        )
        ctx.add_message(
            role="assistant", content="а" * 300, created_at=start + 2 * n + 1
        )


def history(count: int, start: float = 1000.0) -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    add_pairs(ctx, 1, count, start)
    ctx.items.insert(0, llm.ChatMessage(role="system", content=["Инструкции"]))
    return ctx


REPLY = "Кстати, Hermes закончил поручение."


def opened() -> asyncio.Event:
    event = asyncio.Event()
    event.set()
    return event


class FakeHandle:
    def __init__(
        self,
        text: str,
        add_to_chat_ctx: bool,
        events: list[tuple[str, str]],
        agent: VocaAgent | None,
        interrupted: bool,
        gate: asyncio.Event,
        played: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.text = text
        self.played = text if played is None else played
        self.add_to_chat_ctx = add_to_chat_ctx
        self.events = events
        self.agent = agent
        self.interrupted = interrupted
        self.gate = gate
        self.error = error
        self.chat_items: list[llm.ChatItem] = []

    def exception(self) -> BaseException | None:
        return self.error

    async def wait_for_playout(self) -> None:
        await self.gate.wait()
        if self.add_to_chat_ctx and self.played and self.error is None:
            if self.agent is not None:
                ctx = self.agent.chat_ctx.copy()
                self.chat_items.append(
                    ctx.add_message(role="assistant", content=self.played)
                )
                await self.agent.update_chat_ctx(ctx)
            else:
                self.chat_items.append(
                    llm.ChatMessage(role="assistant", content=[self.played])
                )
        self.events.append(("playout", self.text))


class FakeSession:
    def __init__(
        self,
        *,
        closing: bool = False,
        agent: VocaAgent | None = None,
        interrupts: list[bool] | None = None,
        gates: list[asyncio.Event] | None = None,
        played: list[str | None] | None = None,
        errors: list[BaseException | None] | None = None,
    ) -> None:
        self.closing = closing
        self.played = played or []
        self.errors = errors or []
        self.agent = agent
        self.interrupts = interrupts or []
        self.gates = gates or []
        self.calls: list[tuple[str, bool, bool]] = []
        self.events: list[tuple[str, str]] = []
        self.replies: list[tuple[str, Any, bool]] = []
        self.current_speech: Any = None
        self.idles = 0

    def say(
        self, text: str, *, allow_interruptions: bool, add_to_chat_ctx: bool
    ) -> FakeHandle:
        if self.closing:
            raise RuntimeError("closing")
        self.calls.append((text, allow_interruptions, add_to_chat_ctx))
        self.events.append(("say", text))
        gate = self.gates.pop(0) if self.gates else opened()
        interrupted = self.interrupts.pop(0) if self.interrupts else False
        played = self.played.pop(0) if self.played else None
        return FakeHandle(
            text,
            add_to_chat_ctx,
            self.events,
            self.agent,
            interrupted,
            gate,
            played,
        )

    def generate_reply(
        self, *, instructions: str, tool_choice: Any, allow_interruptions: bool
    ) -> FakeHandle:
        if self.closing:
            raise RuntimeError("closing")
        self.replies.append((instructions, tool_choice, allow_interruptions))
        self.events.append(("reply", instructions))
        gate = self.gates.pop(0) if self.gates else opened()
        interrupted = self.interrupts.pop(0) if self.interrupts else False
        played = self.played.pop(0) if self.played else None
        error = self.errors.pop(0) if self.errors else None
        return FakeHandle(
            REPLY,
            True,
            self.events,
            self.agent,
            interrupted,
            gate,
            played,
            error,
        )

    async def wait_for_idle(self) -> None:
        self.idles += 1
        self.events.append(("idle", ""))


class FakeTurn:
    def __init__(self) -> None:
        self.interrupted = False
        self.stop = asyncio.Event()

    def interrupt(self) -> None:
        self.interrupted = True
        self.stop.set()

    async def wait_if_not_interrupted(self, aw: list[asyncio.Future[Any]]) -> None:
        gathered = asyncio.ensure_future(asyncio.gather(*aw, return_exceptions=True))
        stopped = asyncio.ensure_future(self.stop.wait())
        await asyncio.wait({gathered, stopped}, return_when=asyncio.FIRST_COMPLETED)
        stopped.cancel()
        if not gathered.done():
            gathered.cancel()


def ctx(call_id: str, handle: FakeTurn | None = None) -> RunContext[Any]:
    return cast(
        RunContext[Any],
        SimpleNamespace(
            function_call=SimpleNamespace(call_id=call_id),
            speech_handle=handle or FakeTurn(),
        ),
    )


async def tick(_: float) -> None:
    await asyncio.sleep(0)


async def until(condition: Callable[[], bool]) -> None:
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


async def settle() -> None:
    for _ in range(50):
        await asyncio.sleep(0)


class Stand:
    def __init__(
        self,
        tmp_path: Path,
        *,
        limits: Limits | None = None,
        summarizer: Summarizer | None = None,
        chat_ctx: llm.ChatContext | None = None,
        action_silence_s: float = 0.5,
    ) -> None:
        directory = tmp_path / "instructions"
        shutil.copytree(EXAMPLE, directory, dirs_exist_ok=True)
        loader = InstructionLoader(directory)
        instructions = loader.load()
        self.events = tmp_path / "events.jsonl"
        self.repo = FakeRepo()
        self.backend = FakeBackend()
        self.service = DelegationService(
            self.repo, self.backend, run_timeout=900, clock=lambda: NOW, sleep=tick
        )
        self.agent = VocaAgent(
            loader=loader,
            instructions=instructions,
            log=JsonlLog(self.events, session="t"),
            cards=CardState(instructions),
            ctx_high=13926,
            ctx_low=6553,
            limits=limits,
            summarizer=summarizer,
            delegation=self.service,
            chat_ctx=chat_ctx,
            action_silence_s=action_silence_s,
            approval_reminder_s=0,
        )
        self.service.on_change = self.agent.on_task_change
        self.history = asyncio.Event()

    async def sent(self) -> None:
        await self.history.wait()

    def start(self, session: FakeSession) -> None:
        self.agent.start_delivery(cast(AgentSession[Any], session), self.sent)

    def open(self) -> None:
        self.agent.set_present(True)
        self.history.set()

    def rows(self, key: str) -> list[dict[str, object]]:
        found = [
            json.loads(line)
            for line in self.events.read_text(encoding="utf-8").splitlines()
        ]
        return [
            row for row in found if row["kind"] == "task" and row["tool_call_id"] == key
        ]

    def put(self, task: Task) -> Task:
        self.repo.tasks[task.tool_call_id] = task
        return task


def finished(key: str, result: str = "Ответ.", created_at: float = 1.0) -> Task:
    return Task(key, "t", TaskStatus.SUCCEEDED, created_at=created_at, result=result)


def test_delegate_returns_at_once(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        session = FakeSession()
        stand.start(session)
        stand.open()
        reply = await asyncio.wait_for(stand.agent.delegate(ctx("call-1"), "сделай"), 1)
        assert json.loads(reply.output) == {"id": "call-1", "event": "submitted"}
        assert reply.reply_required is False
        assert stand.repo.tasks["call-1"].status == TaskStatus.RUNNING
        assert stand.backend.posts == [("call-1", "сделай")]
        rows = stand.rows("call-1")
        assert [row["status"] for row in rows] == ["queued", "running"]
        assert {row["delivered"] for row in rows} == {False}
        await settle()
        assert session.calls == [
            ("Поручил Hermes: «сделай». Сообщу результат.", True, False)
        ]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def user_state(agent: VocaAgent, old: str, new: str) -> None:
    agent.on_user_state(
        UserStateChangedEvent(
            old_state=cast(Any, old), new_state=cast(Any, new), created_at=agent.clock()
        )
    )


def transcribed(agent: VocaAgent, text: str, *, final: bool = True) -> None:
    agent.on_user_transcribed(
        UserInputTranscribedEvent(
            transcript=text, is_final=final, created_at=agent.clock()
        )
    )


def test_delegate_holds_while_user_speaks(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, action_silence_s=0.05)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        session = FakeSession()
        stand.start(session)
        stand.open()
        user_state(stand.agent, "listening", "speaking")
        call = asyncio.ensure_future(stand.agent.delegate(ctx("call-1"), "сделай"))
        await asyncio.sleep(0.1)
        assert not call.done()
        assert stand.backend.posts == []
        user_state(stand.agent, "speaking", "listening")
        await asyncio.sleep(0.15)
        assert not call.done()
        assert stand.backend.posts == []
        transcribed(stand.agent, "и ещё отчёт")
        final = asyncio.get_running_loop().time()
        await asyncio.sleep(0.02)
        assert not call.done()
        reply = await asyncio.wait_for(call, 1)
        assert asyncio.get_running_loop().time() - final >= 0.05
        assert json.loads(reply.output) == {"id": "call-1", "event": "submitted"}
        assert stand.backend.posts == [("call-1", "сделай")]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delegate_not_sent_after_final_when_turn_interrupted(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, action_silence_s=0.05)
        session = FakeSession()
        stand.start(session)
        stand.open()
        turn = FakeTurn()
        call = asyncio.ensure_future(stand.agent.delegate(ctx("call-1", turn), "x"))
        user_state(stand.agent, "listening", "speaking")
        user_state(stand.agent, "speaking", "listening")
        await asyncio.sleep(0.1)
        assert not call.done()
        transcribed(stand.agent, "отчёты", final=False)
        await asyncio.sleep(0.1)
        assert not call.done()
        transcribed(stand.agent, "отчёты")
        turn.interrupt()
        reply = await asyncio.wait_for(call, 1)
        assert json.loads(reply.output) == {"id": "call-1", "event": "not_sent"}
        assert stand.backend.posts == []
        assert stand.repo.tasks == {}
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delegate_without_continuation_keeps_delay(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, action_silence_s=0.05)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        session = FakeSession()
        stand.start(session)
        stand.open()
        user_state(stand.agent, "listening", "speaking")
        user_state(stand.agent, "speaking", "listening")
        transcribed(stand.agent, "сделай")
        quiet = asyncio.get_running_loop().time()
        reply = await asyncio.wait_for(stand.agent.delegate(ctx("call-1"), "сделай"), 1)
        waited = asyncio.get_running_loop().time() - quiet
        assert 0.05 <= waited < 0.5
        assert json.loads(reply.output) == {"id": "call-1", "event": "submitted"}
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delegate_final_wait_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_module, "FINAL_WAIT_S", 0.1)

    async def main() -> None:
        stand = Stand(tmp_path, action_silence_s=0.05)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        session = FakeSession()
        stand.start(session)
        stand.open()
        user_state(stand.agent, "listening", "speaking")
        user_state(stand.agent, "speaking", "listening")
        quiet = asyncio.get_running_loop().time()
        reply = await asyncio.wait_for(stand.agent.delegate(ctx("call-1"), "сделай"), 1)
        assert asyncio.get_running_loop().time() - quiet >= 0.1
        assert json.loads(reply.output) == {"id": "call-1", "event": "submitted"}
        await stand.agent.stop_delegation()

    asyncio.run(main())


class NoiseSorter:
    async def sort(
        self, state: dict[str, str], requests: int = 0
    ) -> Sorted | DecisionFailure:
        return Sorted("noise", 0.9, 10)


def test_dropped_noise_releases_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_module, "FINAL_WAIT_S", 5.0)

    async def main() -> None:
        stand = Stand(tmp_path, action_silence_s=0.05)
        stand.agent.sorter = NoiseSorter()
        stand.agent.sorter_prompt = DecisionConfig().sorter_prompt()
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        session = FakeSession()
        stand.start(session)
        stand.open()
        call = asyncio.ensure_future(stand.agent.delegate(ctx("call-1"), "сделай"))
        user_state(stand.agent, "listening", "speaking")
        user_state(stand.agent, "speaking", "listening")
        await asyncio.sleep(0.1)
        assert not call.done()

        async def source() -> Any:
            yield stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(language="ru", text="Угу")],
            )

        out = [event async for event in stand.agent._screen(source(), lambda: True)]
        assert out == []
        dropped = asyncio.get_running_loop().time()
        reply = await asyncio.wait_for(call, 2)
        waited = asyncio.get_running_loop().time() - dropped
        assert 0.04 <= waited < 1.0
        assert json.loads(reply.output) == {"id": "call-1", "event": "submitted"}
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delegate_not_sent_when_turn_interrupted(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, action_silence_s=0.05)
        session = FakeSession()
        stand.start(session)
        stand.open()
        user_state(stand.agent, "listening", "speaking")
        turn = FakeTurn()
        call = asyncio.ensure_future(stand.agent.delegate(ctx("call-1", turn), "x"))
        await asyncio.sleep(0.02)
        turn.interrupt()
        reply = await asyncio.wait_for(call, 1)
        assert isinstance(reply, llm.ToolResult)
        assert reply.reply_required is False
        assert json.loads(reply.output) == {"id": "call-1", "event": "not_sent"}
        assert stand.backend.posts == []
        await settle()
        assert session.calls == []
        assert stand.repo.tasks == {}
        await stand.agent.stop_delegation()

    asyncio.run(main())


@pytest.mark.parametrize(
    ("created", "status"),
    [
        ([Rejected("503")], TaskStatus.FAILED),
        ([OutcomeUnknown(), OutcomeUnknown()], TaskStatus.QUEUED),
    ],
)
def test_delegate_unavailable_marks_delivered(
    tmp_path: Path, created: list[Exception], status: TaskStatus
) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = created
        session = FakeSession()
        stand.start(session)
        stand.open()
        with pytest.raises(ToolError, match="Делегирование сейчас недоступно"):
            await stand.agent.delegate(ctx("call-1"), "сделай")
        task = stand.repo.tasks["call-1"]
        assert task.status == status
        assert task.error == "unavailable"
        assert task.delivered_at is not None
        await settle()
        assert session.calls == []
        assert stand.agent.queued == set()
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delivery_waits_for_presence_and_history(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [
            RunState("run_1", TaskStatus.SUCCEEDED, result="Ответ.")
        ]
        session = FakeSession()
        stand.start(session)
        await stand.agent.delegate(ctx("call-1"), "сделай")
        await until(lambda: stand.repo.tasks["call-1"].status == TaskStatus.SUCCEEDED)
        await settle()
        assert len(session.calls) == 1
        stand.agent.set_present(True)
        await settle()
        assert len(session.calls) == 1
        stand.history.set()
        await until(lambda: len(session.replies) == 1)
        assert session.replies[0][1] == "none"
        assert session.replies[0][2] is True
        assert "Ответ." in session.replies[0][0]
        assert EVENT_PROMPT in session.replies[0][0]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delivery_once_per_task(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        task = stand.put(finished("a"))
        session = FakeSession()
        stand.start(session)
        stand.agent.on_task_change(task)
        await stand.agent.enqueue_undelivered()
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        stand.agent.enqueue(task)
        await settle()
        assert len(session.replies) == 1
        assert stand.repo.tasks["a"].delivered_at is not None
        last = stand.rows("a")[-1]
        assert last["delivered"] is True
        assert last["interrupted"] is False
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delivered_at_after_playout(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        gate = asyncio.Event()
        session = FakeSession(gates=[gate])
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: len(session.replies) == 1)
        await settle()
        assert stand.repo.tasks["a"].delivered_at is None
        gate.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_interrupted_delivery_repeated(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        task = stand.put(finished("a"))
        session = FakeSession(interrupts=[True, False])
        stand.start(session)
        stand.agent.enqueue(task)
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 2
        kinds = [kind for kind, _ in session.events]
        first = kinds.index("reply")
        second = kinds.index("reply", first + 1)
        assert "idle" in kinds[first:second]
        rows = stand.rows("a")
        assert any(
            row["interrupted"] is True and row["delivered"] is False for row in rows
        )
        assert rows[-1]["interrupted"] is False
        assert rows[-1]["delivered"] is True
        stand.agent.enqueue(task)
        await stand.agent.enqueue_undelivered()
        await settle()
        assert len(session.replies) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_unheard_delivery_repeated(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        gate = asyncio.Event()
        session = FakeSession(played=["", None], gates=[opened(), gate])
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: len(session.replies) == 2)
        await settle()
        assert stand.repo.tasks["a"].delivered_at is None
        gate.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_unheard_delivery_waits_before_repeat(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        pauses: list[float] = []
        release = asyncio.Event()

        async def sleep(seconds: float) -> None:
            if seconds == UNHEARD_RETRY_S:
                pauses.append(seconds)
                await release.wait()
            else:
                pauses.append(seconds)

        stand.service.sleep = sleep
        session = FakeSession(played=["", None])
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: pauses == [EVENT_SILENCE_S, UNHEARD_RETRY_S])
        await settle()
        assert len(session.replies) == 1
        release.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_interrupted_delivery_repeats_without_pause(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        pauses: list[float] = []

        async def sleep(seconds: float) -> None:
            pauses.append(seconds)

        stand.service.sleep = sleep
        session = FakeSession(interrupts=[True, False])
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert pauses == [EVENT_SILENCE_S]
        assert len(session.replies) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_reply_on_closing_session_keeps_task_undelivered(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.start(FakeSession(closing=True))
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        deliverer = stand.agent.deliverer
        assert deliverer is not None
        await until(deliverer.done)
        assert stand.repo.tasks["a"].delivered_at is None
        assert len(results(stand.agent)) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


LONG = " ".join(f"Фраза {n} о маршруте." for n in range(40))
PLAYED = "Кстати, Hermes закончил: фраза 0"
DUMP: dict[str, Any] = {
    "exclude_function_call": False,
    "exclude_timestamp": False,
    "exclude_config_update": True,
}


def results(agent: VocaAgent) -> list[llm.ChatItem]:
    return [
        item
        for item in agent.chat_ctx.items
        if item.type in ("function_call", "function_call_output")
    ]


def check_result(agent: VocaAgent, task: Task, outcome: dict[str, str]) -> None:
    call, output = results(agent)
    assert isinstance(call, llm.FunctionCall)
    assert isinstance(output, llm.FunctionCallOutput)
    assert call.name == "delegate"
    assert json.loads(call.arguments) == {"task": task.text}
    assert output.call_id == call.call_id
    assert json.loads(output.output) == outcome


def test_interrupted_delivery_puts_full_result_in_context(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        stand = Stand(tmp_path)
        task = stand.put(finished("a", LONG))
        gate = asyncio.Event()
        session = FakeSession(
            agent=stand.agent,
            interrupts=[True, False],
            played=[PLAYED, None],
            gates=[opened(), gate],
        )
        stand.start(session)
        stand.agent.enqueue(task)
        stand.open()
        await until(lambda: len(session.replies) == 2)
        await settle()
        assert stand.repo.tasks["a"].delivered_at is None
        gate.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        await stand.agent.stop_delegation()
        return stand.agent

    agent = asyncio.run(main())
    check_result(agent, finished("a"), {"status": "succeeded", "result": LONG})
    items = agent.chat_ctx.items
    assert [item.type for item in items] == [
        "function_call",
        "function_call_output",
        "message",
        "message",
    ]
    assert items[2].text_content == PLAYED
    assert items[3].text_content == REPLY


def test_long_delivery_puts_whole_result_in_context(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        stand = Stand(tmp_path)
        task = stand.put(finished("a", LONG))
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.agent.enqueue(task)
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        await stand.agent.stop_delegation()
        return stand.agent

    agent = asyncio.run(main())
    assert agent.chat_ctx.items[-1].text_content == REPLY
    check_result(agent, finished("a"), {"status": "succeeded", "result": LONG})


def test_failed_delivery_puts_reason_in_context(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        stand = Stand(tmp_path)
        task = stand.put(
            Task("b", "t", TaskStatus.FAILED, created_at=1.0, error="timeout")
        )
        stand.start(FakeSession(agent=stand.agent))
        stand.agent.enqueue(task)
        stand.open()
        await until(lambda: stand.repo.tasks["b"].delivered_at is not None)
        await stand.agent.stop_delegation()
        return stand.agent

    agent = asyncio.run(main())
    check_result(
        agent,
        finished("b"),
        {"status": "failed", "error": "не дождался результата", "detail": "timeout"},
    )
    assert results(agent)[1].is_error is True
    assert json.loads(result_outcome(failed("HTTP 500 boom"))) == {
        "status": "failed",
        "error": "Hermes вернул ошибку",
        "detail": "HTTP 500 boom",
    }
    assert json.loads(result_outcome(failed(""))) == {
        "status": "failed",
        "error": "Hermes вернул ошибку",
    }


def test_denied_delivery_marks_context(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        stand = Stand(tmp_path)
        task = stand.put(finished("b"))
        stand.repo.events[("b", "approval", "p1")] = Event(
            "b",
            EventKind.APPROVAL,
            "p1",
            EventState.CLOSED,
            0.5,
            0.5,
            closed_reason="denied",
            id=1,
        )
        stand.start(FakeSession(agent=stand.agent))
        stand.agent.enqueue(task)
        stand.open()
        await until(lambda: stand.repo.tasks["b"].delivered_at is not None)
        await stand.agent.stop_delegation()
        return stand.agent

    agent = asyncio.run(main())
    check_result(
        agent,
        finished("b"),
        {"status": "succeeded", "result": "Ответ.", "approval": "denied"},
    )
    assert json.loads(result_outcome(finished("b"))) == {
        "status": "succeeded",
        "result": "Ответ.",
    }


def test_result_in_context_once_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"

    async def first() -> None:
        stand = Stand(tmp_path)
        stand.put(finished("a", LONG))
        stand.start(FakeSession(agent=stand.agent))
        stand.agent.enqueue(stand.repo.tasks["a"])
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        await stand.agent.stop_delegation()
        store = SessionStore.open(path)
        recorder = SessionRecorder(store, store.open_session().id)
        recorder.flush(stand.agent.chat_ctx.to_dict(**DUMP))
        store.close()

    async def second() -> VocaAgent:
        store = SessionStore.open(path)
        restored = llm.ChatContext.from_dict(store.open_session().context)
        store.close()
        stand = Stand(tmp_path, chat_ctx=restored)
        check_result(
            stand.agent, finished("a"), {"status": "succeeded", "result": LONG}
        )
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a", LONG)))
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 1
        await stand.agent.stop_delegation()
        return stand.agent

    asyncio.run(first())
    agent = asyncio.run(second())
    check_result(agent, finished("a"), {"status": "succeeded", "result": LONG})
    spoken = [item for item in agent.chat_ctx.items if item.type == "message"]
    assert len(spoken) == 2


def test_recovered_undelivered_spoken_together(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(finished("a", "Первая.", 1.0))
        stand.put(Task("b", "t", TaskStatus.FAILED, created_at=2.0, error="timeout"))
        stand.put(Task("c", "t", TaskStatus.RUNNING, created_at=3.0, run_id="run_c"))
        stand.backend.runs["run_c"] = [
            RunState("run_c", TaskStatus.SUCCEEDED, result="Третья.")
        ]
        gate = asyncio.Event()
        session = FakeSession(gates=[gate])
        await stand.agent.enqueue_undelivered()
        stand.start(session)
        stand.open()
        await until(lambda: len(session.replies) == 1)
        stand.agent.start_recover()
        await until(lambda: stand.repo.tasks["c"].status == TaskStatus.SUCCEEDED)
        await settle()
        assert len(session.replies) == 1
        assert "Первая." in session.replies[0][0]
        assert "не дождался результата" in session.replies[0][0]
        gate.set()
        await until(
            lambda: all(stand.repo.tasks[key].delivered_at is not None for key in "abc")
        )
        assert len(session.replies) == 2
        assert "Третья." in session.replies[1][0]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_result_during_pending_summary_stays(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200), hold=True)
        stand = Stand(tmp_path, limits=LIMITS, summarizer=fake)
        agent = stand.agent
        await agent.update_chat_ctx(history(6))
        agent.maybe_compact()
        assert agent.pending is not None
        stand.start(FakeSession(agent=agent))
        agent.enqueue(stand.put(finished("a", "Результат.")))
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        fake.gate.set()
        assert agent.pending is not None
        await agent.pending.task
        await agent.on_user_turn_completed(
            agent.chat_ctx.copy(), llm.ChatMessage(role="user", content=["Привет"])
        )
        await agent.stop_delegation()
        return agent

    agent = asyncio.run(main())
    items = agent.chat_ctx.items
    summaries = [
        n
        for n, item in enumerate(items)
        if item.type == "message" and item.extra.get("voca_summary")
    ]
    spoken = [
        n
        for n, item in enumerate(items)
        if item.type == "message" and item.text_content == REPLY
    ]
    assert len(summaries) == 1
    assert len(spoken) == 1
    assert spoken[0] > summaries[0]


def test_stop_delegation_cancels(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        await stand.agent.delegate(ctx("call-1"), "сделай")
        stand.agent.start_recover()
        stand.start(FakeSession())
        await settle()
        assert stand.agent.background
        await stand.agent.stop_delegation()
        assert stand.agent.background == set()
        deliverer = stand.agent.deliverer
        assert deliverer is not None
        assert deliverer.done()

    asyncio.run(main())


def test_no_background_after_stop_delegation(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        await stand.agent.stop_delegation()
        assert await stand.agent.delegate(ctx("call-1"), "сделай")
        stand.agent.start_recover()
        stand.start(FakeSession())
        assert stand.agent.background == set()
        assert stand.agent.deliverer is None
        assert stand.repo.tasks["call-1"].is_open

    asyncio.run(main())


class CancellingSession(FakeSession):
    def __init__(self, *, cancel_on: set[int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cancel_on = cancel_on

    async def wait_for_idle(self) -> None:
        self.idles += 1
        self.events.append(("idle", ""))
        if self.idles in self.cancel_on:
            eou = asyncio.ensure_future(asyncio.sleep(10))
            asyncio.get_running_loop().call_soon(eou.cancel)
            await asyncio.shield(eou)


class StuckSession(FakeSession):
    async def wait_for_idle(self) -> None:
        self.idles += 1
        self.events.append(("idle", ""))
        await asyncio.Event().wait()


def test_cancelled_idle_keeps_delivery(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = CancellingSession(interrupts=[True, False], cancel_on={2})
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 2
        deliverer = stand.agent.deliverer
        assert deliverer is not None
        assert not deliverer.done()
        stand.agent.enqueue(stand.put(finished("b")))
        task = waiting("c")
        await stand.repo.save(task, NOW, arrival(task, NOW))
        stand.agent.on_task_change(task)
        await until(
            lambda: stand.repo.tasks["b"].delivered_at is not None
            and any(
                event.tool_call_id == "c" and event.state == EventState.MENTIONED
                for event in stand.repo.events.values()
            )
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_stop_delegation_cancels_idle_wait(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = StuckSession()
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: session.idles == 1)
        await asyncio.wait_for(stand.agent.stop_delegation(), 1)
        deliverer = stand.agent.deliverer
        assert deliverer is not None
        assert deliverer.done()
        assert deliverer.cancelled()

    asyncio.run(main())


def failed(error: str) -> Task:
    return Task("k", "t", TaskStatus.FAILED, created_at=0.0, error=error)


def sentence(letter: str, length: int) -> str:
    return letter * (length - 1) + "."


def test_clip() -> None:
    first, second, third = (sentence(letter, 120) for letter in "абв")
    clipped = clip(f"{first} {second} {third}")
    assert len(clipped) <= SPEECH_LIMIT
    assert clipped.endswith(".")
    long = " ".join(["слово"] * 70)
    cut = clip(long)
    assert len(cut) <= SPEECH_LIMIT
    assert long.startswith(cut)
    assert not cut.endswith(" ")
    assert len(cut) > SPEECH_LIMIT - 10
    assert clip("раз\nдва\n\n  три") == "раз два три"


def test_delegate_speaks_task_text(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = ["run_1"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        text = "Найди расписание автобусов из Лединци в Нови Сад"
        reply = await stand.agent.delegate(ctx("call-1"), text)
        assert reply.reply_required is False
        ack = f"Поручил Hermes: «{text}». Сообщу результат."
        assert session.calls == [(ack, True, False)]
        assert "Поручение принято" not in reply.output
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_delegate_asks_model_when_not_spoken(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.created = ["run_1", "run_2"]
        stand.backend.runs["run_1"] = [RunState("run_1", TaskStatus.RUNNING)]
        stand.backend.runs["run_2"] = [RunState("run_2", TaskStatus.RUNNING)]
        reply = await stand.agent.delegate(ctx("call-1"), "сделай")
        assert reply.reply_required is True
        stand.start(FakeSession(closing=True))
        reply = await stand.agent.delegate(ctx("call-2"), "сделай")
        assert reply.reply_required is True
        await stand.agent.stop_delegation()

    with caplog.at_level("WARNING", logger="voca.tasks"):
        asyncio.run(main())
    assert sum("not spoken" in record.message for record in caplog.records) == 2


def test_event_record_not_in_context(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        await stand.agent.stop_delegation()
        return stand.agent

    agent = asyncio.run(main())
    items = agent.chat_ctx.items
    assert not any(item.type == "message" and item.role == "system" for item in items)
    assert not any(
        EVENT_PROMPT in (item.text_content or "")
        for item in items
        if item.type == "message"
    )
    found = records(agent.chat_ctx)
    assert [item.kind for item in found] == [
        "function_call",
        "function_call_output",
        "message",
    ]
    assert found[0].text.startswith("delegate(")
    assert found[2].role == "assistant"
    assert found[2].text == REPLY
    assert last_message_at(agent.chat_ctx) == items[-1].created_at
    dumped = json.dumps(agent.chat_ctx.to_dict(**DUMP), ensure_ascii=False)
    assert EVENT_PROMPT not in dumped


def silence_stand(tmp_path: Path) -> tuple[Stand, list[float], asyncio.Event]:
    stand = Stand(tmp_path)
    pauses: list[float] = []
    release = asyncio.Event()

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)
        await release.wait()

    stand.service.sleep = sleep
    return stand, pauses, release


def test_event_waits_for_silence(tmp_path: Path) -> None:
    async def main() -> None:
        stand, pauses, release = silence_stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: pauses == [EVENT_SILENCE_S])
        await settle()
        assert session.replies == []
        user_state(stand.agent, "listening", "speaking")
        release.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert pauses == [EVENT_SILENCE_S, EVENT_SILENCE_S]
        assert len(session.replies) == 1
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_final_transcript_restarts_silence(tmp_path: Path) -> None:
    async def main() -> None:
        stand, pauses, release = silence_stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: pauses == [EVENT_SILENCE_S])
        transcribed(stand.agent, "текст", final=False)
        transcribed(stand.agent, "текст")
        release.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert pauses == [EVENT_SILENCE_S, EVENT_SILENCE_S]
        assert len(session.replies) == 1
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_event_after_agent_answer_without_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        pauses: list[float] = []

        async def sleep(seconds: float) -> None:
            pauses.append(seconds)

        stand.service.sleep = sleep
        session = FakeSession()
        monkeypatch.setattr(VocaAgent, "session", property(lambda _: session))
        stand.start(session)
        stand.agent.on_agent_state(
            AgentStateChangedEvent(old_state="listening", new_state="speaking")
        )
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 1
        assert pauses == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_two_events_one_reply(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a", "Ответ.", 1.0)))
        stand.agent.enqueue(
            stand.put(
                Task("b", "t", TaskStatus.FAILED, created_at=2.0, error="timeout")
            )
        )
        stand.open()
        await until(
            lambda: all(stand.repo.tasks[key].delivered_at is not None for key in "ab")
        )
        assert len(session.replies) == 1
        record = session.replies[0][0]
        assert '"id": "a"' in record
        assert '"id": "b"' in record
        assert "Ответ." in record
        assert "не дождался результата" in record
        for key in "ab":
            last = stand.rows(key)[-1]
            assert last["interrupted"] is False
            assert last["delivered"] is True
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_event_record_uses_notification_view(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        task = finished("a")
        await stand.repo.save(task, NOW, arrival(task, NOW))
        session = FakeSession()
        stand.start(session)
        stand.agent.enqueue(task)
        stand.open()
        await until(lambda: len(session.replies) == 1)
        (event,) = stand.repo.events.values()
        view = notification_view(event, task, NOW)
        assert view["kind"] == "result"
        assert isinstance(view["id"], str)
        assert session.replies[0][0] == events_prompt([view])
        body = session.replies[0][0].split("\n", 1)[1]
        assert json.loads(body) == {"events": [view]}
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_interrupted_reply_keeps_event_open(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        gate = asyncio.Event()
        session = FakeSession(interrupts=[True, False], gates=[opened(), gate])
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: len(session.replies) == 2)
        await settle()
        assert stand.repo.tasks["a"].delivered_at is None
        last = stand.rows("a")[-1]
        assert last["interrupted"] is True
        assert last["delivered"] is False
        gate.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_failed_reply_waits_for_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(errors=[RuntimeError("llm"), None])
        monkeypatch.setattr(VocaAgent, "session", property(lambda _: session))
        stand.start(session)
        stand.agent.enqueue(stand.put(finished("a")))
        stand.open()
        await until(lambda: len(session.replies) == 1)
        await settle()
        assert len(session.replies) == 1
        assert stand.repo.tasks["a"].delivered_at is None
        stand.agent.on_agent_state(
            AgentStateChangedEvent(old_state="listening", new_state="speaking")
        )
        await settle()
        assert len(session.replies) == 1
        user_state(stand.agent, "listening", "speaking")
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_events_prompt() -> None:
    prompt = events_prompt([{"id": "1"}])
    assert prompt.startswith(EVENT_PROMPT)
    assert prompt.endswith('{"events": [{"id": "1"}]}')


APPROVAL = {
    "description": "delete in root path",
    "command": "rm -r /tmp/x",
    "choices": ["once", "deny", "session", "always"],
    "request_id": "p1",
    "execution_generation": 3,
}


def waiting(
    key: str, approval: dict[str, object] = APPROVAL, created_at: float = 1.0
) -> Task:
    return Task(
        key,
        "Удали каталог /tmp/x",
        TaskStatus.WAITING_APPROVAL,
        created_at=created_at,
        run_id=f"run_{key}",
        approval=approval,
    )


def test_request_joins_waiting_reply(tmp_path: Path) -> None:
    async def main() -> None:
        stand, pauses, release = silence_stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        done = finished("a")
        await stand.repo.save(done, NOW, arrival(done, NOW))
        stand.agent.enqueue(done)
        stand.open()
        await until(lambda: pauses == [EVENT_SILENCE_S])
        task = waiting("b")
        await stand.repo.save(task, NOW, arrival(task, NOW))
        stand.agent.on_task_change(task)
        await settle()
        release.set()
        await until(lambda: stand.repo.tasks["a"].delivered_at is not None)
        assert len(session.replies) == 1
        record = session.replies[0][0]
        assert record.index(REQUEST_PROMPT) < record.index(EVENT_PROMPT)
        assert '"requests": [{"id": "2"' in record
        assert '"events": [{"id": "1"' in record
        await stand.agent.stop_delegation()

    asyncio.run(main())
