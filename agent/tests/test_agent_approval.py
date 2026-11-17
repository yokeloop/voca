import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from livekit.agents import StopResponse, llm
from livekit.agents.voice import AgentSession

from voca.clientio.cards import CardState
from voca.config import ApprovalConfig
from voca.conversation.agent import VocaAgent, announcement_text
from voca.conversation.approval import Vocabulary, unclear_text
from voca.conversation.compaction import Limits
from voca.conversation.instructions import InstructionLoader
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.conversation.verdict import Decision, DecisionFailure, Situation, Verdict
from voca.delegation.model import Task, TaskStatus
from voca.delegation.ports import ApprovalNotActive, OutcomeUnknown, RunState
from voca.delegation.service import DelegationService
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"
NOW = 10.0

VOCABULARY = ApprovalConfig().vocabulary()


class FakeRepo:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    async def insert(self, task: Task) -> tuple[Task, bool]:
        if task.tool_call_id in self.tasks:
            return self.tasks[task.tool_call_id], False
        self.tasks[task.tool_call_id] = task
        return task, True

    async def save(self, task: Task) -> None:
        self.tasks[task.tool_call_id] = task

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


class FakeHandle:
    def __init__(
        self,
        text: str,
        add_to_chat_ctx: bool,
        events: list[tuple[str, str]],
        agent: VocaAgent | None,
        interrupted: bool,
        gate: asyncio.Event,
    ) -> None:
        self.text = text
        self.add_to_chat_ctx = add_to_chat_ctx
        self.events = events
        self.agent = agent
        self.interrupted = interrupted
        self.gate = gate
        self.chat_items: list[llm.ChatItem] = []

    async def wait_for_playout(self) -> None:
        await self.gate.wait()
        if self.add_to_chat_ctx and self.agent is not None:
            ctx = self.agent.chat_ctx.copy()
            self.chat_items.append(ctx.add_message(role="assistant", content=self.text))
            await self.agent.update_chat_ctx(ctx)
        self.events.append(("playout", self.text))


class FakeSession:
    def __init__(
        self,
        *,
        closing: bool = False,
        agent: VocaAgent | None = None,
        interrupts: list[bool] | None = None,
        gates: list[asyncio.Event] | None = None,
    ) -> None:
        self.closing = closing
        self.agent = agent
        self.interrupts = interrupts or []
        self.gates = gates or []
        self.calls: list[tuple[str, bool, bool]] = []
        self.events: list[tuple[str, str]] = []
        self.handles: list[FakeHandle] = []

    def say(
        self, text: str, *, allow_interruptions: bool, add_to_chat_ctx: bool
    ) -> FakeHandle:
        if self.closing:
            raise RuntimeError("closing")
        self.calls.append((text, allow_interruptions, add_to_chat_ctx))
        self.events.append(("say", text))
        gate = self.gates.pop(0) if self.gates else opened()
        interrupted = self.interrupts.pop(0) if self.interrupts else False
        handle = FakeHandle(
            text, add_to_chat_ctx, self.events, self.agent, interrupted, gate
        )
        self.handles.append(handle)
        return handle


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


def decided(verdict: Verdict, confidence: float = 0.9, picked: str = "") -> Decision:
    return Decision(verdict, picked or verdict, confidence, 12)


class FakeDecider:
    def __init__(
        self,
        script: dict[str, Decision] | None = None,
        failure: str = "disabled",
    ) -> None:
        self.script = script or {}
        self.failure = failure
        self.seen: list[Situation] = []
        self.during: Callable[[], Awaitable[None]] | None = None

    async def decide(self, situation: Situation) -> Decision | DecisionFailure:
        self.seen.append(situation)
        if self.during is not None:
            await self.during()
        found = self.script.get(situation.phrase)
        return found if found is not None else DecisionFailure(self.failure, 0)


class Stand:
    def __init__(
        self,
        tmp_path: Path,
        *,
        limits: Limits | None = None,
        summarizer: Summarizer | None = None,
        vocabulary: Vocabulary = VOCABULARY,
        decider: FakeDecider | None = None,
    ) -> None:
        self.decider = decider or FakeDecider()
        directory = tmp_path / "instructions"
        shutil.copytree(EXAMPLE, directory)
        loader = InstructionLoader(directory)
        instructions = loader.load()
        self.events = tmp_path / "events.jsonl"
        self.repo = FakeRepo()
        self.backend = FakeBackend()
        self.service = DelegationService(
            self.repo, self.backend, run_timeout=900, clock=lambda: NOW, sleep=tick
        )
        self.agent = VocaAgent(
            vocabulary=vocabulary,
            decider=self.decider,
            loader=loader,
            instructions=instructions,
            log=JsonlLog(self.events, session="t"),
            cards=CardState(instructions),
            ctx_high=13926,
            ctx_low=6553,
            limits=limits,
            summarizer=summarizer,
            delegation=self.service,
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


def said(text: str, started: float | None = None) -> llm.ChatMessage:
    if started is None:
        return llm.ChatMessage(role="user", content=[text])
    return llm.ChatMessage(
        role="user", content=[text], metrics={"started_speaking_at": started}
    )


def finished(key: str, result: str = "Ответ.", created_at: float = 1.0) -> Task:
    return Task(key, "t", TaskStatus.SUCCEEDED, created_at=created_at, result=result)


async def activate(stand: Stand, session: FakeSession, *tasks: Task) -> None:
    for task in tasks:
        stand.agent.on_task_change(stand.put(task))
    stand.start(session)
    stand.open()
    await until(lambda: stand.agent.active is not None)


def spoken(session: FakeSession) -> list[str]:
    return [call[0] for call in session.calls]


async def answer(agent: VocaAgent, text: str) -> None:
    await agent.on_user_turn_completed(agent.chat_ctx.copy(), said(text))


async def stopped(agent: VocaAgent, text: str) -> None:
    with pytest.raises(StopResponse):
        await answer(agent, text)


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


def approvals(stand: Stand, key: str) -> list[object]:
    return [row["approval"] for row in stand.rows(key) if "approval" in row]


def test_announcement_text() -> None:
    text = announcement_text(waiting("a"), VOCABULARY)
    assert text == (
        "Hermes просит подтверждения по поручению «Удали каталог /tmp/x»: "
        "удаление в корневом каталоге. Подтвердите на экране или скажите "
        "„Подтверждаю“ или „Отклоняю“."
    )
    assert "rm -r" not in text
    long = ("слово " * 20)[:100]
    assert len(long) == 100
    quoted = announcement_text(replace(waiting("b"), text=long), VOCABULARY)
    assert 0 < len(quoted.split("«")[1].split("»")[0]) <= 60
    bare = waiting("c", {"command": "rm -r /tmp/x", "request_id": "p1"})
    assert announcement_text(bare, VOCABULARY) == (
        "Hermes просит подтверждения по поручению «Удали каталог /tmp/x»: "
        "опасная команда. Подтвердите на экране или скажите "
        "„Подтверждаю“ или „Отклоняю“."
    )


def test_interrupted_announcement_still_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(interrupts=[True, True])
        stand.agent.on_task_change(stand.put(waiting("a")))
        stand.start(session)
        stand.open()
        await until(lambda: stand.agent.active is not None)
        assert spoken(session) == [announcement_text(waiting("a"), VOCABULARY)] * 2
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        rows = [row for row in stand.rows("a") if row.get("announced")]
        assert [row["interrupted"] for row in rows] == [True, True]
        await stopped(stand.agent, "Подтверждаю")
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_announcement_fact_not_spoken_text(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        await activate(stand, session, waiting("a"))
        assert session.calls[0][2] is False
        ctx = stand.agent.chat_ctx
        texts = [
            item.text_content or "" for item in ctx.items if item.type == "message"
        ]
        assert not any("Hermes просит подтверждения" in text for text in texts)
        calls = [item.name for item in ctx.items if item.type == "function_call"]
        assert calls == ["list_tasks"]
        [(name, output)] = facts(ctx)
        assert name == "list_tasks"
        assert output == {
            "id": "a",
            "event": "approval_requested",
            "text": "Удали каталог /tmp/x",
            "request": "удаление в корневом каталоге",
            "command": "rm -r /tmp/x",
            "interrupted": False,
        }
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_expiry_fact(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        task = waiting("a")
        await activate(stand, session, task)
        expired = stand.put(replace(task, status=TaskStatus.RUNNING, approval=None))
        stand.agent.on_task_change(expired)
        await until(lambda: "approval_expired" in events_of(stand.agent.chat_ctx))
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "approval_expired", "by": "hermes"},
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_answered_not_reannounced(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        task = waiting("a")
        await activate(stand, session, task)
        await stopped(stand.agent, "Подтверждаю")
        stand.agent.on_task_change(task)
        await settle()
        assert not stand.agent.announcements
        assert stand.agent.active is None
        assert spoken(session) == [announcement_text(task, VOCABULARY), "Подтверждено"]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_facts_survive_restore(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "Отклоняю")
        await stand.agent.stop_delegation()
        data = stand.agent.chat_ctx.to_dict(
            exclude_function_call=False, exclude_timestamp=False
        )
        restored = llm.ChatContext.from_dict(data)
        directory = tmp_path / "instructions"
        loader = InstructionLoader(directory)
        instructions = loader.load()
        agent = VocaAgent(
            vocabulary=VOCABULARY,
            loader=loader,
            instructions=instructions,
            log=JsonlLog(tmp_path / "second.jsonl", session="t2"),
            cards=CardState(instructions),
            ctx_high=13926,
            ctx_low=6553,
            chat_ctx=restored,
        )
        ctx = agent.chat_ctx
        calls = [
            (item.name, json.loads(item.arguments))
            for item in ctx.items
            if item.type == "function_call"
        ]
        assert calls == [("list_tasks", {}), ("list_tasks", {})]
        assert [(name, output["event"]) for name, output in facts(ctx)] == [
            ("list_tasks", "approval_requested"),
            ("list_tasks", "denied"),
        ]

    asyncio.run(main())


def test_interrupted_announcement_repeated_once(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(interrupts=[True, False])
        await activate(stand, session, waiting("a"))
        assert len(session.calls) == 2
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_interrupted_first_holds_queue(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(interrupts=[True, True, False])
        await activate(stand, session, waiting("a"), waiting("b", created_at=2.0))
        await settle()
        assert spoken(session) == [announcement_text(waiting("a"), VOCABULARY)] * 2
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stopped(stand.agent, "Подтверждаю")
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        await until(lambda: len(session.calls) == 4)
        assert spoken(session)[-1] == announcement_text(
            waiting("b", created_at=2.0), VOCABULARY
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_next_announcement_waits_for_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"), waiting("b", created_at=2.0))
        await settle()
        await settle()
        assert len(session.calls) == 1
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stopped(stand.agent, "Подтверждаю")
        await until(lambda: len(session.calls) == 3)
        await until(lambda: stand.agent.active is not None)
        assert spoken(session)[1:] == [
            "Подтверждено",
            announcement_text(waiting("b", created_at=2.0), VOCABULARY),
        ]
        assert stand.agent.active is not None
        assert stand.agent.active.key == "b"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_announcement_before_result_and_results_play(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a"))
        stand.put(waiting("b", created_at=2.0))
        stand.put(finished("c", "Третья.", 3.0))
        session = FakeSession()
        await stand.agent.enqueue_undelivered()
        stand.start(session)
        stand.open()
        await until(lambda: stand.repo.tasks["c"].delivered_at is not None)
        await settle()
        assert spoken(session) == [
            announcement_text(waiting("a"), VOCABULARY),
            "Готово по поручению: Третья.",
        ]
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_expired_request_releases_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        task = waiting("a")
        await activate(stand, session, task)
        expired = stand.put(replace(task, status=TaskStatus.RUNNING, approval=None))
        stand.agent.on_task_change(expired)
        await settle()
        assert stand.agent.active is None
        assert len(session.calls) == 1
        await answer(stand.agent, "Подтверждаю")
        assert stand.backend.approved == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_new_request_replaces_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        task = waiting("a")
        await activate(stand, session, task)
        second = {**APPROVAL, "request_id": "p2"}
        stand.agent.on_task_change(stand.put(replace(task, approval=second)))
        await until(lambda: len(session.calls) == 2)
        await until(lambda: stand.agent.active is not None)
        assert stand.agent.active is not None
        assert stand.agent.active.approval["request_id"] == "p2"
        await stopped(stand.agent, "Подтверждаю")
        assert stand.backend.approved == [("run_a", "once", second)]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_waiting_task_announced_on_join(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.put(waiting("a"))
        session = FakeSession()
        await stand.agent.enqueue_undelivered()
        stand.start(session)
        stand.open()
        await until(lambda: len(session.calls) == 1)
        assert spoken(session) == [announcement_text(waiting("a"), VOCABULARY)]
        await stand.agent.stop_delegation()

    asyncio.run(main())


@pytest.mark.parametrize(
    ("text", "choice", "reply"),
    [("Подтверждаю.", "once", "Подтверждено"), ("Отклоняю", "deny", "Отклонено")],
)
def test_phrase_with_active_request_approves(
    tmp_path: Path, text: str, choice: str, reply: str
) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(agent=stand.agent)
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, text)
        assert stand.backend.approved == [("run_a", choice, APPROVAL)]
        assert session.calls[-1] == (reply, True, True)
        await session.handles[-1].wait_for_playout()
        event = {"once": "approved", "deny": "denied"}[choice]
        tail = [
            item.output if item.type == "function_call_output" else item.text_content
            for item in stand.agent.chat_ctx.items[-2:]
        ]
        assert json.loads(str(tail[0])) == {"id": "a", "event": event, "by": "voice"}
        assert tail[1] == reply
        assert stand.agent.active is None
        rows = [row for row in stand.rows("a") if "approval" in row]
        assert [(row["approval"], row["status"]) for row in rows] == [
            (choice, "waiting_approval")
        ]
        users = [
            item.text_content
            for item in stand.agent.chat_ctx.items
            if item.type == "message" and item.role == "user"
        ]
        assert text not in users
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_phrase_without_active_request_goes_on(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        stand.start(session)
        stand.open()
        await answer(stand.agent, "Подтверждаю")
        assert stand.backend.approved == []
        assert session.calls == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_other_reply_keeps_active(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({"да, давай": decided("unrelated")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await answer(stand.agent, "да, давай")
        assert stand.backend.approved == []
        assert stand.agent.active is not None
        assert stand.agent.active.key == "a"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_conflict_says_not_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.approvals = [ApprovalNotActive("409 approval_not_pending")]
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "Подтверждаю")
        assert spoken(session)[-1] == "Запрос подтверждения уже не активен"
        assert stand.agent.active is None
        assert approvals(stand, "a") == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_changed_task_goes_on(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        task = waiting("a")
        await activate(stand, session, task)
        stand.put(replace(task, status=TaskStatus.RUNNING))
        await answer(stand.agent, "Подтверждаю")
        assert stand.backend.approved == []
        assert stand.decider.seen == []
        assert len(session.calls) == 1
        assert approvals(stand, "a") == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_network_failure_keeps_active(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        stand.backend.approvals = [OutcomeUnknown("ReadTimeout")]
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        active = stand.agent.active
        await stopped(stand.agent, "Подтверждаю")
        assert spoken(session)[-1] == "Не удалось передать подтверждение"
        assert stand.agent.active is active
        assert approvals(stand, "a") == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_pending_summary_waits_for_next_turn(tmp_path: Path) -> None:
    async def main() -> None:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200), hold=True)
        stand = Stand(tmp_path, limits=LIMITS, summarizer=fake)
        agent = stand.agent
        await agent.update_chat_ctx(history(6))
        agent.maybe_compact()
        assert agent.pending is not None
        await activate(stand, FakeSession(agent=agent), waiting("a"))
        fake.gate.set()
        await agent.pending.task
        await stopped(agent, "Подтверждаю")
        assert agent.pending is not None
        assert summaries(agent) == 0
        await answer(agent, "Привет")
        assert agent.pending is None
        assert summaries(agent) == 1
        await agent.stop_delegation()

    asyncio.run(main())


def test_phrase_with_tail_goes_to_llm(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        turn_ctx = stand.agent.chat_ctx.copy()
        message = said("Подтверждаю. А что дальше?")
        await stand.agent.on_user_turn_completed(turn_ctx, message)
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert message.text_content == "А что дальше?"
        assert "approved" in events_of(turn_ctx)
        assert "Подтверждено" not in spoken(session)
        assert stand.agent.active is None
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_deny_with_tail(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        turn_ctx = stand.agent.chat_ctx.copy()
        message = said("Отклоняю, отмени вообще задачу.")
        await stand.agent.on_user_turn_completed(turn_ctx, message)
        assert stand.backend.approved == [("run_a", "deny", APPROVAL)]
        assert ("list_tasks", {"id": "a", "event": "denied", "by": "voice"}) in (
            facts(turn_ctx)
        )
        assert message.text_content == "отмени вообще задачу"
        assert "Отклонено" not in spoken(session)
        await stand.agent.stop_delegation()

    asyncio.run(main())


@pytest.mark.parametrize("text", ["подверждаю", "Подтверждаем", "отклоню"])
def test_near_miss_says_unclear(tmp_path: Path, text: str) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        active = stand.agent.active
        await stopped(stand.agent, text)
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert session.calls[-1] == (unclear_text(VOCABULARY), True, True)
        assert stand.backend.approved == []
        assert stand.agent.active is active
        assert facts(stand.agent.chat_ctx)[-1] == (
            "list_tasks",
            {"id": "a", "event": "approval_not_recognized", "by": "voice"},
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_both_keywords_unclear(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "Подтверждаю или отклоняю")
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert stand.backend.approved == []
        assert stand.agent.active is not None
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_echo_window_unclear(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        with pytest.raises(StopResponse):
            await agent.on_user_turn_completed(
                agent.chat_ctx.copy(), said("Подтверждаю", agent.heard_at - 1)
            )
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert stand.backend.approved == []
        assert agent.active is not None
        with pytest.raises(StopResponse):
            await agent.on_user_turn_completed(
                agent.chat_ctx.copy(), said("Подтверждаю", agent.heard_at + 1)
            )
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        await agent.stop_delegation()

    asyncio.run(main())


def test_other_phrase_with_active_goes_to_llm(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({"А что он удалит?": decided("unrelated")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        active = stand.agent.active
        await answer(stand.agent, "А что он удалит?")
        assert stand.backend.approved == []
        assert stand.agent.active is active
        assert len(session.calls) == 1
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_queue_waits_for_answer(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({"А что он удалит?": decided("unrelated")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"), waiting("b", created_at=2.0))
        await settle()
        assert spoken(session) == [announcement_text(waiting("a"), VOCABULARY)]
        await answer(stand.agent, "А что он удалит?")
        await settle()
        assert len(session.calls) == 1
        await stopped(stand.agent, "Подтверждаю")
        await until(lambda: len(session.calls) == 3)
        assert spoken(session)[-1] == announcement_text(
            waiting("b", created_at=2.0), VOCABULARY
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_unclear_reply_moves_echo_window(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        before = agent.heard_at
        await stopped(agent, "подверждаю")
        await until(lambda: agent.heard_at > before)
        with pytest.raises(StopResponse):
            await agent.on_user_turn_completed(
                agent.chat_ctx.copy(),
                said("Подтверждаю", (before + agent.heard_at) / 2),
            )
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert stand.backend.approved == []
        await agent.stop_delegation()

    asyncio.run(main())


async def heard(agent: VocaAgent, text: str, started: float) -> None:
    await agent.on_user_turn_completed(agent.chat_ctx.copy(), said(text, started))


def test_barge_in_phrase_counts(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(interrupts=[True, True])
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        with pytest.raises(StopResponse):
            await heard(agent, "Подтверждаю.", agent.heard_at - 1)
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert spoken(session)[-1] == "Подтверждено"
        await agent.stop_delegation()

    asyncio.run(main())


def test_barge_in_while_replaying(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        replay = asyncio.Event()
        session = FakeSession(interrupts=[True, False], gates=[opened(), replay])
        stand.agent.on_task_change(stand.put(waiting("a")))
        stand.start(session)
        stand.open()
        await until(lambda: len(session.calls) == 2)
        agent = stand.agent
        assert agent.active is None
        with pytest.raises(StopResponse):
            await heard(agent, "Подтверждаю", agent.heard_at - 1)
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        replay.set()
        await settle()
        assert agent.active is None
        assert len(session.calls) == 3
        await agent.stop_delegation()

    asyncio.run(main())


def test_barge_in_echo_rejected(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(interrupts=[True, True])
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        for text in (
            "Подтверждаю по поручению «Удали каталог",
            "Подтверждаю или Отклоняю",
        ):
            with pytest.raises(StopResponse):
                await heard(agent, text, agent.heard_at - 1)
            assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert stand.backend.approved == []
        assert agent.active is not None
        await agent.stop_delegation()

    asyncio.run(main())


def test_barge_in_filler_not_counted(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession(interrupts=[True, True])
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        with pytest.raises(StopResponse):
            await heard(agent, "Ммм, подтверждаю, хорошо. Давай.", agent.heard_at - 1)
        assert stand.backend.approved == []
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert agent.active is not None
        await agent.stop_delegation()

    asyncio.run(main())


def test_configured_keywords(tmp_path: Path) -> None:
    custom = ApprovalConfig(confirm=["Принимаю"], deny=["Запрещаю"]).vocabulary()

    async def main() -> None:
        stand = Stand(tmp_path, vocabulary=custom)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        assert spoken(session)[0].endswith(
            "Подтвердите на экране или скажите „Принимаю“ или „Запрещаю“."
        )
        await stopped(stand.agent, "Подтверждаю")
        assert stand.backend.approved == []
        assert spoken(session)[-1] == unclear_text(custom)
        await stopped(stand.agent, "Примаю")
        assert spoken(session)[-1] == unclear_text(custom)
        await stopped(stand.agent, "Принимаю.")
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_echo_checked_only_near_the_announcement(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        message = said("Подтверждаю, удали каталог /tmp/x в файл", agent.heard_at + 1)
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), message)
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert message.text_content == "удали каталог /tmp/x в файл"
        assert "a" not in agent.announced
        await agent.stop_delegation()

    asyncio.run(main())


PHRASE_OK = "Ну окей, давай подтвердим его."


async def turn(agent: VocaAgent, text: str) -> tuple[llm.ChatContext, llm.ChatMessage]:
    turn_ctx = agent.chat_ctx.copy()
    message = said(text)
    await agent.on_user_turn_completed(turn_ctx, message)
    return turn_ctx, message


def test_decision_approve(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, decider=FakeDecider({PHRASE_OK: decided("approve")}))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, PHRASE_OK)
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert spoken(session)[-1] == "Подтверждено"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_deny_more(tmp_path: Path) -> None:
    text = "Отклоняю, отмени вообще задачу"

    async def main() -> None:
        decider = FakeDecider({text: decided("deny_more")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        turn_ctx, message = await turn(stand.agent, text)
        assert stand.backend.approved == [("run_a", "deny", APPROVAL)]
        assert "Отклонено" not in spoken(session)
        assert message.text_content == text
        assert ("list_tasks", {"id": "a", "event": "denied", "by": "voice"}) in (
            facts(turn_ctx)
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_repeat(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({"Повтори запрос": decided("repeat")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "Повтори запрос")
        await until(lambda: len(session.calls) == 2)
        assert spoken(session) == [announcement_text(waiting("a"), VOCABULARY)] * 2
        assert stand.backend.approved == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


@pytest.mark.parametrize(
    "decision",
    [decided("unclear"), decided("unclear", confidence=0.4, picked="approve")],
    ids=["unclear", "low_confidence"],
)
def test_decision_unclear(tmp_path: Path, decision: Decision) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, decider=FakeDecider({PHRASE_OK: decision}))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        active = stand.agent.active
        await stopped(stand.agent, PHRASE_OK)
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert stand.backend.approved == []
        assert stand.agent.active is active
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_low_confidence(tmp_path: Path) -> None:
    async def main() -> None:
        decision = decided("unclear", confidence=0.4, picked="approve")
        stand = Stand(tmp_path, decider=FakeDecider({PHRASE_OK: decision}))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, PHRASE_OK)
        assert stand.backend.approved == []
        rows = [
            json.loads(line)
            for line in stand.events.read_text(encoding="utf-8").splitlines()
        ]
        [row] = [row for row in rows if row["kind"] == "approval_decision"]
        assert (row["verdict"], row["picked"]) == ("unclear", "approve")
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_unrelated_goes_to_llm(tmp_path: Path) -> None:
    text = "А что он хочет удалить?"

    async def main() -> None:
        decider = FakeDecider({text: decided("unrelated")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        _, message = await turn(stand.agent, text)
        assert message.text_content == text
        assert stand.backend.approved == []
        assert stand.agent.active is not None
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_situation(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({PHRASE_OK: decided("unrelated")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await answer(stand.agent, PHRASE_OK)
        [situation] = decider.seen
        assert situation == Situation(
            task="Удали каталог /tmp/x",
            request="удаление в корневом каталоге",
            command="rm -r /tmp/x",
            announcement=announcement_text(waiting("a"), VOCABULARY),
            phrase=PHRASE_OK,
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_fallback_keyword(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, decider=FakeDecider(failure="timeout"))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "Подтверждаю")
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert spoken(session)[-1] == "Подтверждено"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_fallback_other_unclear(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, decider=FakeDecider(failure="timeout"))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "да, давай")
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        assert stand.backend.approved == []
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_after_button(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({PHRASE_OK: decided("approve")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))

        async def press() -> None:
            await stand.agent.approve_from_page("a", "p1", "once")

        decider.during = press
        _, message = await turn(stand.agent, PHRASE_OK)
        assert message.text_content == PHRASE_OK
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert "Подтверждено" not in spoken(session)
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_logged(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, decider=FakeDecider({PHRASE_OK: decided("approve")}))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, PHRASE_OK)
        rows = [
            json.loads(line)
            for line in stand.events.read_text(encoding="utf-8").splitlines()
        ]
        found = [row for row in rows if row["kind"] == "approval_decision"]
        assert found[0]["tool_call_id"] == "a"
        assert found[0]["phrase"] == PHRASE_OK
        assert found[0]["verdict"] == "approve"
        assert found[0]["confidence"] == 0.9
        assert "extra" not in found[0]
        assert found[0]["ms"] == 12
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_fallback_logged(tmp_path: Path) -> None:
    async def main() -> None:
        stand = Stand(tmp_path, decider=FakeDecider(failure="connection"))
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        await stopped(stand.agent, "Подтверждаю")
        rows = [
            json.loads(line)
            for line in stand.events.read_text(encoding="utf-8").splitlines()
        ]
        [row] = [row for row in rows if row["kind"] == "approval_decision"]
        assert row["fallback"] == "connection"
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_echo_rejected_before_decision(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({PHRASE_OK: decided("approve")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        agent = stand.agent
        with pytest.raises(StopResponse):
            await heard(agent, PHRASE_OK, agent.heard_at - 1)
        assert decider.seen == []
        assert spoken(session)[-1] == unclear_text(VOCABULARY)
        await agent.stop_delegation()

    asyncio.run(main())


def test_decision_approve_more(tmp_path: Path) -> None:
    text = "Подтверждаю, и скажи потом, сколько времени"

    async def main() -> None:
        decider = FakeDecider({text: decided("approve_more")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        await activate(stand, session, waiting("a"))
        turn_ctx, message = await turn(stand.agent, text)
        assert stand.backend.approved == [("run_a", "once", APPROVAL)]
        assert "Подтверждено" not in spoken(session)
        assert message.text_content == text
        assert ("list_tasks", {"id": "a", "event": "approved", "by": "voice"}) in (
            facts(turn_ctx)
        )
        await stand.agent.stop_delegation()

    asyncio.run(main())


def test_decision_stale_after_new_request(tmp_path: Path) -> None:
    async def main() -> None:
        decider = FakeDecider({"Повтори запрос": decided("repeat")})
        stand = Stand(tmp_path, decider=decider)
        session = FakeSession()
        task = waiting("a")
        await activate(stand, session, task)
        second = {**APPROVAL, "request_id": "p2"}

        async def replace_request() -> None:
            stand.agent.on_task_change(stand.put(replace(task, approval=second)))

        decider.during = replace_request
        _, message = await turn(stand.agent, "Повтори запрос")
        assert message.text_content == "Повтори запрос"
        await until(lambda: len(session.calls) == 2)
        await until(lambda: stand.agent.active is not None)
        assert stand.agent.active is not None
        assert stand.agent.active.approval == second
        await settle()
        assert len(session.calls) == 2
        await stand.agent.stop_delegation()

    asyncio.run(main())
