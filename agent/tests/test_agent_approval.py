import asyncio
import json
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from livekit.agents import RunContext, ToolError, llm
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import UserStateChangedEvent

from voca.clientio.cards import CardState
from voca.config import DecisionConfig
from voca.conversation.agent import (
    APPROVAL_GONE,
    ASK_NOTE,
    BUTTON_PROMPT,
    CLARIFY_WHICH_NOTE,
    CONFIRM_NOTE,
    EVENT_PROMPT,
    LEFT_NOTE,
    NO_REQUEST,
    PENDING_NOTE,
    REMIND_PROMPT,
    REQUEST_AMBIGUOUS,
    REQUEST_PROMPT,
    REQUEST_UNKNOWN,
    UNAVAILABLE_NOTE,
    VocaAgent,
    notification_view,
)
from voca.conversation.approval_judge import Rules
from voca.conversation.compaction import Limits
from voca.conversation.instructions import InstructionLoader
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.conversation.verdict import (
    DecisionFailure,
    Relation,
    Reply,
    Sorted,
    sorter_body,
)
from voca.delegation.danger import Danger
from voca.delegation.events import Event, EventState, advance, arrival
from voca.delegation.model import Task, TaskStatus
from voca.delegation.ports import ApprovalNotActive, RunState
from voca.delegation.service import DelegationService
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"
NOW = 10.0
SORTER_PROMPT = DecisionConfig().sorter_prompt()


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
        if event is not None and event.state != EventState.CLOSED:
            self.events[key] = replace(
                event, state=EventState.CLOSED, closed_reason=reason, updated_at=at
            )

    def event(self, event_id: int) -> Event:
        return next(event for event in self.events.values() if event.id == event_id)


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
        self.approvals: list[Any] = [None]
        self.posts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str | None]] = []
        self.stopped: list[tuple[str, str]] = []
        self.approved: list[tuple[str, str, dict[str, Any]]] = []
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
        self.approved.append((run_id, choice, approval))
        take(self.approvals)

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


def opened() -> asyncio.Event:
    event = asyncio.Event()
    event.set()
    return event


REPLY = "Кстати, Hermes прислал запрос."
READING = "Он хочет удалить каталог /tmp/x. Разрешить?"


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
        self.finished = False
        self.chat_items: list[llm.ChatItem] = []

    def exception(self) -> BaseException | None:
        return self.error

    def done(self) -> bool:
        return self.finished

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
        self.finished = True
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
        self.handles: list[FakeHandle] = []
        self.replies: list[tuple[str, Any, bool]] = []
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
        handle = FakeHandle(
            text, add_to_chat_ctx, self.events, self.agent, interrupted, gate, played
        )
        self.handles.append(handle)
        return handle

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
        handle = FakeHandle(
            REPLY, True, self.events, self.agent, interrupted, gate, played, error
        )
        self.handles.append(handle)
        return handle

    async def wait_for_idle(self) -> None:
        self.idles += 1


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


def sorted_(
    relation: Relation = "answers",
    reply: Reply = "approve",
    confidence: float = 1.0,
    target: int | None = None,
) -> Sorted:
    return Sorted(
        "reply",
        1.0,
        10,
        relation=relation,
        relation_confidence=1.0,
        answer=reply,
        answer_confidence=confidence,
        target=target,
        target_confidence=1.0,
    )


class FakeSorter:
    def __init__(
        self, script: dict[str, Sorted] | None = None, failure: str = "connection"
    ) -> None:
        self.script = script or {}
        self.failure = failure
        self.seen: list[dict[str, str]] = []
        self.counts: list[int] = []

    async def sort(
        self, state: dict[str, str], requests: int = 0
    ) -> Sorted | DecisionFailure:
        self.seen.append(state)
        self.counts.append(requests)
        found = self.script.get(state["фраза пользователя"])
        return found if found is not None else DecisionFailure(self.failure, 300)


class Stand:
    def __init__(
        self,
        tmp_path: Path,
        *,
        sorter: FakeSorter | None = None,
        rules: Rules | None = None,
        approval_reminder_s: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = tick,
        clock: Callable[[], float] = lambda: NOW,
        limits: Limits | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self.sorter = sorter
        directory = tmp_path / "instructions"
        shutil.copytree(EXAMPLE, directory, dirs_exist_ok=True)
        self.loader = InstructionLoader(directory)
        self.events = tmp_path / "events.jsonl"
        self.repo = FakeRepo()
        self.backend = FakeBackend()
        self.service = DelegationService(
            self.repo, self.backend, run_timeout=900, clock=clock, sleep=sleep
        )
        self.rules = rules
        self.approval_reminder_s = approval_reminder_s
        self.limits = limits
        self.summarizer = summarizer
        self.agent = self.make_agent()
        self.history = asyncio.Event()

    def make_agent(self) -> VocaAgent:
        instructions = self.loader.load()
        agent = VocaAgent(
            loader=self.loader,
            instructions=instructions,
            log=JsonlLog(self.events, session="t"),
            cards=CardState(instructions),
            ctx_high=13926,
            ctx_low=6553,
            limits=self.limits,
            summarizer=self.summarizer,
            delegation=self.service,
            sorter=self.sorter,
            sorter_prompt=SORTER_PROMPT,
            rules=self.rules,
            approval_reminder_s=self.approval_reminder_s,
        )
        self.service.on_change = agent.on_task_change
        return agent

    async def sent(self) -> None:
        await self.history.wait()

    def start(self, session: FakeSession, agent: VocaAgent | None = None) -> None:
        (agent or self.agent).start_delivery(
            cast(AgentSession[Any], session), self.sent
        )

    def open(self, agent: VocaAgent | None = None) -> None:
        (agent or self.agent).set_present(True)
        self.history.set()

    def all_rows(self, kind: str) -> list[dict[str, Any]]:
        found = [
            json.loads(line)
            for line in self.events.read_text(encoding="utf-8").splitlines()
        ]
        return [row for row in found if row["kind"] == kind]

    def rows(self, key: str) -> list[dict[str, object]]:
        return [row for row in self.all_rows("task") if row["tool_call_id"] == key]

    def put(self, task: Task) -> Task:
        self.repo.tasks[task.tool_call_id] = task
        return task


APPROVAL = {
    "description": "delete in root path",
    "command": "rm -r /tmp/x",
    "choices": ["once", "deny", "session", "always"],
    "request_id": "p1",
    "execution_generation": 3,
}
PACKAGE = {
    "description": "package manager uninstall",
    "command": "apt remove voca-48-none",
    "choices": ["once", "deny"],
    "request_id": "p2",
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


def finished(key: str, result: str = "Ответ.", created_at: float = 1.0) -> Task:
    return Task(key, "t", TaskStatus.SUCCEEDED, created_at=created_at, result=result)


def said(text: str, started: float | None = None) -> llm.ChatMessage:
    if started is None:
        return llm.ChatMessage(role="user", content=[text])
    return llm.ChatMessage(
        role="user", content=[text], metrics={"started_speaking_at": started}
    )


async def request(stand: Stand, task: Task) -> Event:
    await stand.repo.save(task, NOW, arrival(task, NOW))
    stand.agent.on_task_change(task)
    return next(
        event
        for event in stand.repo.events.values()
        if event.tool_call_id == task.tool_call_id
        and event.request_id == (task.approval or {}).get("request_id")
    )


async def mentioned(stand: Stand, task: Task) -> Event:
    event = await request(stand, task)
    assert event.id is not None
    await stand.repo.advance_event(event.id, EventState.MENTIONED, NOW)
    await say(stand, "Пришёл запрос от Hermes. Зачитать?")
    return event


def ctx(call_id: str, handle: Any) -> RunContext[Any]:
    return cast(
        RunContext[Any],
        SimpleNamespace(
            function_call=SimpleNamespace(call_id=call_id),
            speech_handle=handle,
        ),
    )


def reading(
    stand: Stand,
    played: str = READING,
    *,
    interrupted: bool = False,
    gate: asyncio.Event | None = None,
) -> FakeHandle:
    handle = FakeHandle(
        played, True, [], stand.agent, interrupted, gate or opened(), played
    )
    handle.chat_items.append(
        llm.FunctionCallOutput(
            call_id="read", name="open_approval", output="{}", is_error=False
        )
    )
    return handle


async def read(
    stand: Stand,
    played: str = READING,
    *,
    interrupted: bool = False,
    ids: tuple[str | None, ...] = (None,),
) -> FakeHandle:
    gate = asyncio.Event()
    handle = reading(stand, played, interrupted=interrupted, gate=gate)
    for id in ids:
        await stand.agent.open_approval(ctx("read", handle), id)
    gate.set()
    await until(lambda: stand.agent.reading is None)
    return handle


async def say(stand: Stand, text: str) -> None:
    ctx = stand.agent.chat_ctx.copy()
    ctx.add_message(role="assistant", content=text)
    await stand.agent.update_chat_ctx(ctx)


async def turn(
    stand: Stand, text: str, answer: str | None = "Ответ агента."
) -> llm.ChatContext:
    turn_ctx = stand.agent.chat_ctx.copy()
    message = said(text)
    await stand.agent.on_user_turn_completed(turn_ctx, message)
    ctx = stand.agent.chat_ctx.copy()
    ctx.items.append(message)
    if answer is not None:
        ctx.add_message(role="assistant", content=answer)
    await stand.agent.update_chat_ctx(ctx)
    return turn_ctx


def notes(turn_ctx: llm.ChatContext) -> list[str]:
    return [
        item.text_content or ""
        for item in turn_ctx.items
        if item.type == "message" and item.role == "system"
    ]


def facts(ctx: llm.ChatContext) -> list[tuple[str, dict[str, Any]]]:
    return [
        (item.name, json.loads(item.output))
        for item in ctx.items
        if item.type == "function_call_output"
    ]


def events_of(ctx: llm.ChatContext) -> list[object]:
    return [output.get("event") for _, output in facts(ctx)]


def summaries(agent: VocaAgent) -> int:
    return sum(
        1
        for item in agent.chat_ctx.items
        if item.type == "message" and item.extra.get("voca_summary")
    )


def approvals(stand: Stand, key: str) -> list[tuple[object, object]]:
    return [
        (row["approval"], row["by"]) for row in stand.rows(key) if "approval" in row
    ]


def outcomes(stand: Stand) -> list[object]:
    return [row["outcome"] for row in stand.all_rows("approval_judge")]


def test_request_mentioned_by_model(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        event = await request(stand, waiting("a"))
        assert event.id is not None
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        assert len(session.replies) == 1
        instructions, tool_choice, interruptible = session.replies[0]
        assert instructions.startswith(REQUEST_PROMPT)
        assert EVENT_PROMPT not in instructions
        assert (tool_choice, interruptible) == ("none", True)
        items = stand.agent.chat_ctx.items
        fact = next(
            index
            for index, item in enumerate(items)
            if item.type == "function_call_output"
        )
        spoken = next(
            index
            for index, item in enumerate(items)
            if item.type == "message" and item.role == "assistant"
        )
        assert fact < spoken
        assert facts(stand.agent.chat_ctx) == [
            (
                "list_notifications",
                {
                    "id": "1",
                    "event": "approval_mentioned",
                    "request": "удаление в корневом каталоге",
                    "command": "rm -r /tmp/x",
                    "danger": "high",
                },
            )
        ]
        assert stand.all_rows("approval_event") == [
            {
                **stand.all_rows("approval_event")[0],
                "event_id": "1",
                "tool_call_id": "a",
                "state": "mentioned",
            }
        ]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_interrupted_mention_repeated(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        gate = asyncio.Event()
        session = FakeSession(
            agent=stand.agent, interrupts=[True, False], gates=[opened(), gate]
        )
        stand.start(session)
        stand.open()
        await request(stand, waiting("a"))
        await until(lambda: len(session.replies) == 2)
        assert stand.repo.event(1).state == EventState.ARRIVED
        gate.set()
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        assert events_of(stand.agent.chat_ctx) == ["approval_mentioned"]
        assert all(REQUEST_PROMPT in reply[0] for reply in session.replies)
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_read_request_not_mentioned(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, sorter=FakeSorter({"Да": sorted_(confidence=0.96)}))
        session = FakeSession(agent=stand.agent)
        await request(stand, waiting("a"))
        gate = asyncio.Event()
        await stand.agent.open_approval(ctx("read", reading(stand, gate=gate)))
        stand.start(session)
        stand.open()
        await settle()
        gate.set()
        await until(lambda: stand.repo.event(1).state == EventState.ESSENCE)
        stand.agent.on_task_change(stand.repo.tasks["a"])
        await settle()
        assert session.replies == []
        assert [row["state"] for row in stand.all_rows("approval_event")] == ["essence"]
        assert events_of(stand.agent.chat_ctx) == []
        first = await turn(stand, "Да")
        assert notes(first)[0].startswith(CONFIRM_NOTE)
        assert outcomes(stand) == ["confirm"]
        assert stand.all_rows("approval_judge")[0]["binding"] == "place"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_mention_after_cut_reading(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        await request(stand, waiting("a"))
        gate = asyncio.Event()
        handle = reading(stand, "Он хочет удал", interrupted=True, gate=gate)
        await stand.agent.open_approval(ctx("read", handle))
        stand.start(session)
        stand.open()
        await settle()
        assert session.replies == []
        gate.set()
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        assert len(session.replies) == 1
        assert session.replies[0][0].startswith(REQUEST_PROMPT)
        assert [row["state"] for row in stand.all_rows("approval_event")] == [
            "mentioned"
        ]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_request_and_result_one_reply(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        done = finished("r")
        await stand.repo.save(done, NOW, arrival(done, NOW))
        stand.agent.enqueue(done)
        await request(stand, waiting("a"))
        await settle()
        stand.start(session)
        stand.open()
        await until(lambda: stand.repo.tasks["r"].delivered_at is not None)
        assert len(session.replies) == 1
        record = session.replies[0][0]
        assert record.index(REQUEST_PROMPT) < record.index(EVENT_PROMPT)
        assert stand.repo.event(2).state == EventState.MENTIONED
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_mentioned_request_not_mentioned_again(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        task = waiting("a")
        await request(stand, task)
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        stand.agent.on_task_change(task)
        await settle()
        assert len(session.replies) == 1
        await stand.agent.stop_delegation()
        other = waiting("b", PACKAGE, created_at=2.0)
        await stand.repo.save(other, NOW, arrival(other, NOW))
        second = stand.make_agent()
        fresh = FakeSession(agent=second)
        stand.start(fresh, second)
        stand.open(second)
        await second.enqueue_undelivered()
        await until(lambda: stand.repo.event(2).state == EventState.MENTIONED)
        await settle()
        assert len(fresh.replies) == 1
        assert '"id": "2"' in fresh.replies[0][0]
        assert '"id": "1"' not in fresh.replies[0][0]
        await second.stop_delegation()

    asyncio.run(main())


def test_open_approval_returns_request(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        task = waiting("a")
        event = await request(stand, task)
        handle = reading(stand, gate=asyncio.Event())
        bare = json.loads(await stand.agent.open_approval(ctx("x", handle)))
        named = json.loads(await stand.agent.open_approval(ctx("x", handle), "1"))
        assert bare == named == notification_view(event, task, NOW)
        assert stand.agent.reading is not None
        assert stand.agent.reading.ids == ["1", "1"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_open_approval_errors(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        handle = reading(stand, gate=asyncio.Event())
        with pytest.raises(ToolError, match=NO_REQUEST):
            await stand.agent.open_approval(ctx("x", handle))
        await request(stand, waiting("a"))
        with pytest.raises(ToolError, match=REQUEST_UNKNOWN):
            await stand.agent.open_approval(ctx("x", handle), "9")
        await request(stand, waiting("b", PACKAGE, created_at=2.0))
        with pytest.raises(ToolError, match=REQUEST_AMBIGUOUS):
            await stand.agent.open_approval(ctx("x", handle))
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_reading_marks_essence(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        await request(stand, waiting("a"))
        await read(stand, "Он хочет удалить каталог voca-48-a. Разрешить?")
        assert stand.repo.event(1).state == EventState.ESSENCE
        [row] = stand.all_rows("approval_read")
        assert (row["event_ids"], row["essence"], row["interrupted"]) == (
            ["1"],
            True,
            False,
        )
        assert row["played"] == "Он хочет удалить каталог voca-48-a. Разрешить?"
        assert [row["state"] for row in stand.all_rows("approval_event")] == ["essence"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_cut_before_first_sentence_keeps_state(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        await request(stand, waiting("a"))
        await read(stand, "Он хочет удал", interrupted=True)
        assert stand.repo.event(1).state == EventState.ARRIVED
        [row] = stand.all_rows("approval_read")
        assert (row["essence"], row["interrupted"]) == (False, True)
        assert stand.agent.about == {}
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_cut_after_first_sentence_marks_essence(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        await mentioned(stand, waiting("a", PACKAGE))
        await read(stand, "Он хочет удалить пакет.", interrupted=True)
        assert stand.repo.event(1).state == EventState.ESSENCE
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_hook_settles_finished_reading(tmp_path: Path) -> None:
    async def main() -> None:
        sorter = FakeSorter({"Да, удаляй": sorted_(confidence=0.97)})
        stand = Stand(tmp_path, sorter=sorter)
        await request(stand, waiting("a"))
        gate = asyncio.Event()
        handle = reading(stand, gate=gate)
        await stand.agent.open_approval(ctx("read", handle))
        await say(stand, READING)
        handle.chat_items.append(stand.agent.chat_ctx.items[-1])
        handle.finished = True
        await turn(stand, "Да, удаляй")
        assert stand.agent.reading is None
        assert stand.repo.event(1).state == EventState.ESSENCE
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        gate.set()
        await settle()
        assert len(stand.all_rows("approval_read")) == 1
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_a(tmp_path: Path) -> None:
    async def main() -> None:
        sorter = FakeSorter(
            {
                "Да, давай": sorted_(confidence=0.97),
                "Да, удаляй": sorted_(confidence=0.97),
            }
        )
        stand = Stand(tmp_path, sorter=sorter)
        await mentioned(stand, waiting("a"))
        turn_ctx = stand.agent.chat_ctx.copy()
        items = turn_ctx.items
        message = said("Да, давай")
        await stand.agent.on_user_turn_completed(turn_ctx, message)
        assert turn_ctx.items is items
        assert notes(turn_ctx) == []
        assert stand.backend.approved == []
        ctx = stand.agent.chat_ctx.copy()
        ctx.items.append(message)
        await stand.agent.update_chat_ctx(ctx)
        await read(stand)
        await turn(stand, "Да, удаляй")
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {
                "id": "a",
                "event": "approved",
                "by": "voice",
                "request": "удаление в корневом каталоге",
            },
        )
        assert approvals(stand, "a") == [("once", "voice")]
        assert outcomes(stand) == ["pass", "approve"]
        assert stand.all_rows("approval_judge")[1]["binding"] == "content"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_b(tmp_path: Path) -> None:
    async def main() -> None:
        sorter = FakeSorter(
            {
                "А что именно там лежит?": sorted_("asks"),
                "Не надо, отклони": sorted_(reply="deny", confidence=0.7),
            }
        )
        stand = Stand(tmp_path, sorter=sorter)
        await mentioned(stand, waiting("a"))
        await read(stand)
        asked = await turn(stand, "А что именно там лежит?")
        [text] = notes(asked)
        assert text.startswith(ASK_NOTE)
        assert "rm -r /tmp/x" in text
        assert stand.backend.approved == []
        await turn(stand, "Не надо, отклони")
        assert stand.backend.approved == [("run_a", "deny", APPROVAL)]
        assert stand.all_rows("approval_judge")[1]["binding"] == "place"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_v(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, sorter=FakeSorter({"Да": sorted_(confidence=0.96)}))
        await mentioned(stand, waiting("a"))
        await read(stand)
        first = await turn(stand, "Да")
        [text] = notes(first)
        assert text.startswith(CONFIRM_NOTE)
        assert stand.backend.approved == []
        await turn(stand, "Да")
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert outcomes(stand) == ["confirm", "approve"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_g(tmp_path: Path) -> None:
    async def main() -> None:
        mixed = "Пакет удаляй, а каталог не трогай"
        sorter = FakeSorter({"Да, разрешаю": sorted_(), mixed: sorted_(target=2)})
        stand = Stand(tmp_path, sorter=sorter)
        await mentioned(stand, waiting("a"))
        await request(stand, waiting("b", PACKAGE, created_at=2.0))
        await read(
            stand, "Первый удаляет каталог. Второй удаляет пакет.", ids=("1", "2")
        )
        first = await turn(stand, "Да, разрешаю")
        assert notes(first)[0].startswith(CLARIFY_WHICH_NOTE)
        second = await turn(stand, mixed)
        assert notes(second)[0].startswith(CLARIFY_WHICH_NOTE)
        third = await turn(stand, "Да, разрешаю")
        assert notes(third)[0].startswith(LEFT_NOTE)
        assert stand.backend.approved == []
        assert outcomes(stand) == ["clarify_which", "clarify_which", "left"]
        assert stand.all_rows("approval_judge")[2]["clarified"] == 2
        assert sorter.counts == [2, 2, 2]
        assert all(
            event.state != EventState.CLOSED for event in stand.repo.events.values()
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_zh(tmp_path: Path) -> None:
    async def main() -> None:
        time_ = "Подожди, а сколько сейчас времени?"
        sorter = FakeSorter({time_: sorted_("other"), "Да": sorted_()})
        stand = Stand(tmp_path, sorter=sorter)
        await mentioned(stand, waiting("a", PACKAGE))
        await read(stand, "Он хочет удалить пакет voca-48-none. Разрешить?")
        other = await turn(stand, time_, "Сейчас полдень. Запрос ждёт ответа.")
        [text] = notes(other)
        assert text.startswith(PENDING_NOTE)
        yes = await turn(stand, "Да")
        assert notes(yes) == []
        assert stand.backend.approved == []
        assert outcomes(stand) == ["pass", "pass"]
        assert stand.all_rows("approval_judge")[0]["event_ids"] == ["1"]
        assert stand.all_rows("approval_judge")[1]["event_ids"] == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_z(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(
            tmp_path, sorter=FakeSorter({"Да-да, удаляй": sorted_(confidence=0.9)})
        )
        await mentioned(stand, waiting("a", PACKAGE))
        await read(stand, "Он хочет удалить пакет.", interrupted=True)
        await turn(stand, "Да-да, удаляй")
        assert stand.backend.approved == [("run_a", "once", PACKAGE)]
        assert outcomes(stand) == ["approve"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conversation_i(tmp_path: Path) -> None:
    async def main() -> None:
        named = "Не надо читать, пусть удаляет каталог"
        stand = Stand(tmp_path, sorter=FakeSorter({named: sorted_(confidence=0.96)}))
        await mentioned(stand, waiting("a"))
        await turn(stand, named)
        assert stand.repo.event(1).state == EventState.MENTIONED
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert stand.all_rows("approval_judge")[0]["binding"] == "content"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_short_yes_after_unrelated_phrase(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, sorter=FakeSorter({"Да": sorted_()}))
        await mentioned(stand, waiting("a"))
        await read(stand)
        await say(stand, "Кстати, завтра будет дождь.")
        yes = await turn(stand, "Да")
        assert notes(yes) == []
        assert stand.backend.approved == []
        assert outcomes(stand) == ["pass"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_event_speech_after_confirm_is_no_place(tmp_path: Path) -> None:
    async def main() -> None:
        unsure = "Ну, может быть"
        sorter = FakeSorter(
            {"Да": sorted_(confidence=0.96), unsure: sorted_(confidence=0.3)}
        )
        stand = Stand(tmp_path, sorter=sorter)
        session = FakeSession(agent=stand.agent)
        await mentioned(stand, waiting("a"))
        await read(stand)
        await turn(stand, "Да", None)
        stand.start(session)
        stand.open()
        await say(stand, "Точно удалить каталог целиком?")
        done = finished("r")
        await stand.repo.save(done, NOW, arrival(done, NOW))
        stand.agent.enqueue(done)
        await until(lambda: stand.repo.tasks["r"].delivered_at is not None)
        yes = await turn(stand, "Да")
        assert notes(yes) == []
        assert stand.backend.approved == []
        await read(stand)
        await turn(stand, unsure, None)
        await say(stand, "Разрешить удаление каталога?")
        await stand.agent.approve_from_page("a", "p1", "deny")
        await until(lambda: len(session.replies) == 2)
        await settle()
        assert stand.backend.approved == [("run_a", "deny", APPROVAL)]
        assert outcomes(stand) == ["confirm", "pass", "clarify_sure"]
        assert stand.agent._place() is None
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_two_clarifications_then_left(tmp_path: Path) -> None:
    async def main() -> None:
        unsure = "Ну, может быть"
        other = "А какая погода?"
        sorter = FakeSorter({unsure: sorted_(confidence=0.3), other: sorted_("other")})
        stand = Stand(tmp_path, sorter=sorter)
        await mentioned(stand, waiting("a"))
        await read(stand)
        await turn(stand, unsure)
        assert stand.agent.clarified == 1
        await turn(stand, unsure)
        assert stand.agent.clarified == 2
        left = await turn(stand, unsure)
        assert notes(left)[0].startswith(LEFT_NOTE)
        assert stand.agent.clarified == 0
        assert outcomes(stand) == ["clarify_sure", "clarify_sure", "left"]
        await read(stand)
        await turn(stand, unsure)
        assert stand.agent.clarified == 1
        await turn(stand, other)
        assert stand.agent.clarified == 0
        assert stand.backend.approved == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_sorter_failure_never_approves(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, sorter=FakeSorter())
        await mentioned(stand, waiting("a"))
        await read(stand)
        answered = await turn(stand, "Да, удаляй")
        [text] = notes(answered)
        assert text.startswith(UNAVAILABLE_NOTE)
        assert stand.backend.approved == []
        assert outcomes(stand) == ["unavailable"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_sorter_off_never_approves(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        await mentioned(stand, waiting("a"))
        await read(stand)
        answered = await turn(stand, "Да, удаляй")
        assert notes(answered)[0].startswith(UNAVAILABLE_NOTE)
        assert stand.backend.approved == []
        assert stand.repo.event(1).danger == Danger.HIGH
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_resumed_speech_holds_decision(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(
            tmp_path, sorter=FakeSorter({"Да, удаляй": sorted_(confidence=0.97)})
        )
        await mentioned(stand, waiting("a"))
        agent = stand.agent
        agent.quiet_at = agent.clock()
        hook = asyncio.ensure_future(turn(stand, "Да, удаляй"))
        await asyncio.sleep(0.05)
        assert not hook.done()
        agent.on_user_state(
            UserStateChangedEvent(
                old_state="listening", new_state="speaking", created_at=agent.clock()
            )
        )
        turn_ctx = await hook
        assert notes(turn_ctx) == []
        assert stand.backend.approved == []
        assert outcomes(stand) == ["approve", "resumed"]
        await agent.stop_delegation()

    asyncio.run(main())


def test_decision_waits_for_silence(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(
            tmp_path, sorter=FakeSorter({"Да, удаляй": sorted_(confidence=0.97)})
        )
        await mentioned(stand, waiting("a"))
        agent = stand.agent
        agent.quiet_at = agent.clock() - 0.2
        started = time.monotonic()
        await turn(stand, "Да, удаляй")
        assert time.monotonic() - started >= 0.25
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        await agent.stop_delegation()

    asyncio.run(main())


def test_approval_gone_fact(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(
            tmp_path, sorter=FakeSorter({"Да, удаляй": sorted_(confidence=0.97)})
        )
        stand.backend.approvals = [ApprovalNotActive("a")]
        await mentioned(stand, waiting("a"))
        await turn(stand, "Да, удаляй")
        output = next(
            item
            for item in stand.agent.chat_ctx.items
            if item.type == "function_call_output" and "approval_failed" in item.output
        )
        assert output.is_error
        assert json.loads(output.output) == {
            "id": "a",
            "event": "approval_failed",
            "by": "voice",
            "error": APPROVAL_GONE,
        }
        assert approvals(stand, "a") == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_danger_by_rule_only(tmp_path: Path) -> None:
    async def main() -> None:
        sorter = FakeSorter({"Да, удаляй": sorted_(confidence=0.5)})
        stand = Stand(tmp_path, sorter=sorter)
        await mentioned(stand, waiting("a", PACKAGE))
        await turn(stand, "Да, удаляй")
        assert stand.repo.event(1).danger == Danger.MEDIUM
        [row] = stand.all_rows("approval_judge")
        assert (row["outcome"], row["threshold"]) == ("approve", 0.5)
        for state, count in zip(sorter.seen, sorter.counts, strict=True):
            body = sorter_body(SORTER_PROMPT, state, "d1-2B", count)
            assert list(body["questions"]) == ["kind", "relation", "answer"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_expiry_fact(tmp_path: Path) -> None:
    async def expire(stand: Stand, answer: bool) -> list[object]:
        task = waiting("a")
        await mentioned(stand, task)
        if answer:
            await turn(stand, "Да, удаляй")
        stand.agent.on_task_change(
            stand.put(replace(task, status=TaskStatus.RUNNING, approval=None))
        )
        await settle()
        assert stand.agent.waiting == {}
        await stand.agent.stop_delegation()
        return events_of(stand.agent.chat_ctx)

    async def main() -> None:
        sorter = FakeSorter({"Да, удаляй": sorted_(confidence=0.97)})
        silent = Stand(tmp_path / "silent", sorter=sorter)
        assert (await expire(silent, False))[-1] == "approval_expired"
        assert facts(silent.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "approval_expired", "by": "hermes"},
        )
        voiced = Stand(tmp_path / "voiced", sorter=sorter)
        assert "approval_expired" not in await expire(voiced, True)

    asyncio.run(main())


def test_button_reply(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent, interrupts=[False, True])
        stand.start(session)
        stand.open()
        await request(stand, waiting("a"))
        await until(lambda: len(session.replies) == 1)
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        result = await stand.agent.approve_from_page("a", "p1", "deny")
        assert result == {"ok": True}
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "denied", "by": "button"},
        )
        await until(lambda: len(session.replies) == 2)
        record = session.replies[1][0]
        assert record.startswith(BUTTON_PROMPT)
        assert '"pressed": [{"task": "a", "event": "denied"' in record
        assert REQUEST_PROMPT not in record
        await settle()
        assert len(session.replies) == 2
        assert stand.agent.pressed == {}
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_reminder_once_before_expiry(tmp_path: Path) -> None:
    async def main() -> None:
        pauses: list[float] = []
        alarm = asyncio.Event()

        async def sleep(seconds: float) -> None:
            pauses.append(seconds)
            if seconds > 60:
                await alarm.wait()
            await asyncio.sleep(0)

        stand = Stand(tmp_path, approval_reminder_s=60, sleep=sleep)
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        await request(stand, waiting("a"))
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        assert NOW + 300 - 60 - NOW in pauses
        assert len(session.replies) == 1
        alarm.set()
        await until(lambda: len(session.replies) == 2)
        assert session.replies[1][0].startswith(REMIND_PROMPT)
        assert '"expiring": [{"id": "1"' in session.replies[1][0]
        stand.agent.on_task_change(stand.repo.tasks["a"])
        await settle()
        assert len(session.replies) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_reminder_skipped_after_expiry(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, approval_reminder_s=60, clock=lambda: NOW + 301)
        session = FakeSession(agent=stand.agent)
        task = waiting("a")
        await stand.repo.save(task, NOW, arrival(task, NOW))
        await stand.repo.advance_event(1, EventState.MENTIONED, NOW)
        stand.start(session)
        stand.open()
        stand.agent.on_task_change(task)
        await settle()
        assert stand.agent.timers == {1}
        assert session.replies == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_facts_survive_restore(tmp_path: Path) -> None:
    async def main() -> None:
        deny = "Не надо, отклони"
        stand = Stand(tmp_path, sorter=FakeSorter({deny: sorted_(reply="deny")}))
        session = FakeSession(agent=stand.agent)
        stand.start(session)
        stand.open()
        await request(stand, waiting("a"))
        await until(lambda: stand.repo.event(1).state == EventState.MENTIONED)
        await turn(stand, deny)
        await stand.agent.stop_delegation()
        data = stand.agent.chat_ctx.to_dict(
            exclude_function_call=False, exclude_timestamp=False
        )
        restored = llm.ChatContext.from_dict(data)
        instructions = stand.loader.load()
        agent = VocaAgent(
            loader=stand.loader,
            instructions=instructions,
            log=JsonlLog(tmp_path / "second.jsonl", session="t2"),
            cards=CardState(instructions),
            ctx_high=13926,
            ctx_low=6553,
            chat_ctx=restored,
        )
        assert [(name, output["event"]) for name, output in facts(agent.chat_ctx)] == [
            ("list_notifications", "approval_mentioned"),
            ("list_tasks", "denied"),
        ]

    asyncio.run(main())


def test_pending_summary_waits_for_next_turn(tmp_path: Path) -> None:
    async def main() -> None:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200), hold=True)
        stand = Stand(tmp_path, limits=LIMITS, summarizer=fake, sorter=FakeSorter())
        agent = stand.agent
        await agent.update_chat_ctx(history(6))
        agent.maybe_compact()
        assert agent.pending is not None
        await request(stand, waiting("a"))
        await turn(stand, "Привет")
        assert agent.pending is not None
        assert summaries(agent) == 0
        fake.gate.set()
        await agent.pending.task
        await turn(stand, "Как дела?")
        assert agent.pending is None
        assert summaries(agent) == 1
        await agent.stop_delegation()

    asyncio.run(main())


def test_glue_reaches_turn_when_request_unrelated(tmp_path: Path) -> None:
    glued = "Попроси Гермеса создать папку отчёты, и в ней файл за ноябрь"

    async def main() -> None:
        sorter = FakeSorter({glued: sorted_("other")})
        stand = Stand(tmp_path, sorter=sorter)
        await request(stand, waiting("a"))
        base = 1000.0
        ctx = stand.agent.chat_ctx.copy()
        ctx.add_message(
            role="user",
            content="Попроси Гермеса создать папку",
            id="fragment",
            metrics={
                "started_speaking_at": base + 98.0,
                "stopped_speaking_at": base + 100.0,
            },
        )
        ctx.add_message(role="assistant", content="Сейчас", id="cut", interrupted=True)
        await stand.agent.update_chat_ctx(ctx)
        turn_ctx = stand.agent.chat_ctx.copy()
        message = said("отчёты, и в ней файл за ноябрь", base + 101.5)
        await stand.agent.on_user_turn_completed(turn_ctx, message)
        assert message.text_content == glued
        assert [state["фраза пользователя"] for state in sorter.seen] == [glued]
        assert [row["branch"] for row in stand.all_rows("sorter")] == ["judge"]
        ids = [item.id for item in turn_ctx.items]
        assert "fragment" not in ids and "cut" not in ids
        assert outcomes(stand) == ["pass"]
        assert stand.backend.approved == []
        await stand.agent.stop_delegation()

    asyncio.run(main())
