import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    Awaitable,
    Callable,
    Coroutine,
)
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from livekit import rtc
from livekit.agents import (
    FlushSentinel,
    RunContext,
    StopResponse,
    ToolError,
    function_tool,
    llm,
    stt,
)
from livekit.agents.voice import Agent, AgentSession, ModelSettings, SpeechHandle
from livekit.agents.voice.agent_activity import ActivityClosedError
from livekit.agents.voice.events import UserStateChangedEvent

from voca.clientio.cards import CardState
from voca.conversation.approval import (
    Choice,
    Phrase,
    Vocabulary,
    approval_prompt,
    echoes,
    parse,
    unclear_text,
)
from voca.conversation.compaction import (
    SUMMARY_KEY,
    Limits,
    Plan,
    Trigger,
    build_prompt,
    due,
    estimate,
    plan,
    summary_budget,
    wrap,
)
from voca.conversation.context import Item, approx_tokens, split_turns, trim_chat_ctx
from voca.conversation.descriptions import describe
from voca.conversation.glue import Entry, glue
from voca.conversation.instructions import InstructionLoader, InstructionSet
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.conversation.verdict import (
    Decider,
    Decision,
    DecisionFailure,
    Situation,
    Sorted,
    Sorter,
    SorterPrompt,
    drop,
    sorter_state,
)
from voca.delegation.events import Event, EventKind
from voca.delegation.model import CANCELLED, GATEWAY_RESTART, TIMEOUT, Task, TaskStatus
from voca.delegation.ports import (
    ApprovalNotActive,
    DelegationError,
    NotSteerable,
    TaskNotFound,
    TaskNotOpen,
    Unavailable,
)
from voca.delegation.service import DelegationService
from voca.speech.markup import scan
from voca.telemetry.jsonl import JsonlLog

REFUSALS = {"stt": "не расслышал", "llm": "не могу ответить сейчас"}

OnCompacted = Callable[[str, float, str, dict[str, object]], None]
BLOCK_STRIKES = 2
BLOCK_RETRY_S = 300
DELEGATE_UNAVAILABLE = "Делегирование сейчас недоступно"
SPEECH_LIMIT = 300
UNHEARD_RETRY_S = 2.0
RECENT_LIMIT = 5
TOOL_RESULT_LIMIT = SPEECH_LIMIT
FAILURE_REASONS = {
    TIMEOUT: "не дождался результата",
    GATEWAY_RESTART: "Hermes перезапускался",
    CANCELLED: "отменено пользователем",
}
FAILURE_DEFAULT = "Hermes вернул ошибку"
TASK_UNKNOWN = "Нет такого поручения"
TASK_AMBIGUOUS = "Неясно, какое поручение: назовите id из list_tasks"
TASK_CLOSED = "Поручение уже завершено"
NOT_STEERABLE = "Поручение сейчас не принимает уточнений"
NO_REQUEST = "Сейчас нет запроса подтверждения"
ACTION_FAILED = "Не удалось связаться с Hermes"
APPROVAL_LIMIT = 60
APPROVAL_REPLIES = {"once": "Подтверждено", "deny": "Отклонено"}
APPROVAL_GONE = "Запрос подтверждения уже не активен"
APPROVAL_FAILED = "Не удалось передать подтверждение"
ANNOUNCE_ATTEMPTS = 2


@dataclass
class Pending:
    plan: Plan
    task: asyncio.Task[Summary | Failure]
    trigger: str


@dataclass
class Announcement:
    key: str
    approval: dict[str, Any]
    attempts: int = 0


def refusal(component: str, *, tts_down: bool) -> str | None:
    if tts_down:
        return None
    return REFUSALS.get(component)


def clip(text: str, limit: int = SPEECH_LIMIT) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit + 1]
    found = re.match(r".*[.!?…](?= )", head)
    if found is not None:
        return found.group(0).rstrip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else text[:limit]).rstrip()


def delivery_text(task: Task) -> str:
    if task.status == TaskStatus.SUCCEEDED:
        result = clip(task.result or "")
        return f"Готово по поручению: {result}" if result else "Готово по поручению."
    reason = FAILURE_REASONS.get(task.error or "", FAILURE_DEFAULT)
    return f"Поручение не выполнено: {reason}."


def result_outcome(task: Task) -> str:
    if task.status == TaskStatus.SUCCEEDED:
        outcome = {"status": task.status.value, "result": task.result or ""}
    else:
        reason = FAILURE_REASONS.get(task.error or "", FAILURE_DEFAULT)
        outcome = {"status": task.status.value, "error": reason}
        if task.error:
            outcome["detail"] = task.error
    return json.dumps(outcome, ensure_ascii=False)


def announcement_text(task: Task, vocabulary: Vocabulary) -> str:
    head = clip(task.text, APPROVAL_LIMIT)
    description = describe((task.approval or {}).get("description"))
    return f"Hermes просит подтверждения по поручению «{head}»: {description}. {approval_prompt(vocabulary)}"


def submitted_text(task: str) -> str:
    return f"Поручил Hermes: «{clip(task)}». Сообщу результат."


def short_id(key: str) -> str:
    return key


def task_view(task: Task, active: Announcement | None) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": short_id(task.tool_call_id),
        "text": clip(task.text, APPROVAL_LIMIT),
        "status": task.status.value,
    }
    if task.status == TaskStatus.SUCCEEDED:
        view["result"] = clip(task.result or "", TOOL_RESULT_LIMIT)
    elif task.status == TaskStatus.FAILED:
        view["error"] = FAILURE_REASONS.get(task.error or "", FAILURE_DEFAULT)
    elif task.status == TaskStatus.WAITING_APPROVAL and task.approval is not None:
        view["approval"] = {
            "request": describe(task.approval.get("description")),
            "command": task.approval.get("command"),
            "active": active is not None
            and active.key == task.tool_call_id
            and active.approval == task.approval,
        }
    return view


def heard(handle: SpeechHandle) -> bool:
    return not handle.interrupted and any(
        item.type == "message" and item.role == "assistant" and item.text_content
        for item in handle.chat_items
    )


def notification_view(event: Event, task: Task, now: float) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": str(event.id),
        "task": short_id(event.tool_call_id),
        "kind": event.kind.value,
        "state": event.state.value,
        "age_s": int(now - event.created_at),
        "text": clip(task.text, APPROVAL_LIMIT),
    }
    if event.kind == EventKind.APPROVAL:
        view["danger"] = None if event.danger is None else event.danger.value
        if task.approval is not None:
            view["request"] = describe(task.approval.get("description"))
            view["command"] = task.approval.get("command")
    elif event.kind == EventKind.RESULT:
        view["result"] = clip(task.result or "", TOOL_RESULT_LIMIT)
    else:
        view["error"] = FAILURE_REASONS.get(task.error or "", FAILURE_DEFAULT)
    return view


def records(ctx: llm.ChatContext) -> list[Item]:
    items: list[Item] = []
    for item in ctx.items:
        if item.type == "message":
            items.append(
                Item(
                    item.id,
                    item.type,
                    item.role,
                    None,
                    approx_tokens(item.text_content or ""),
                    text=item.text_content or "",
                    summary=bool(item.extra.get(SUMMARY_KEY)),
                )
            )
        elif item.type == "function_call":
            items.append(
                Item(
                    item.id,
                    item.type,
                    None,
                    item.call_id,
                    approx_tokens(item.name + item.arguments),
                    text=f"{item.name}({item.arguments})",
                )
            )
        elif item.type == "function_call_output":
            items.append(
                Item(
                    item.id,
                    item.type,
                    None,
                    item.call_id,
                    approx_tokens(item.output),
                    text=item.output,
                )
            )
        else:
            items.append(Item(item.id, item.type, None, None, 0))
    return items


def entries(ctx: llm.ChatContext) -> list[Entry]:
    found: list[Entry] = []
    for item in ctx.items:
        if item.type == "message":
            found.append(
                Entry(
                    item.id,
                    item.type,
                    role=item.role,
                    text=item.text_content or "",
                    interrupted=item.interrupted,
                    started_at=item.metrics.get("started_speaking_at"),
                    stopped_at=item.metrics.get("stopped_speaking_at"),
                )
            )
        elif item.type == "function_call":
            found.append(
                Entry(item.id, item.type, call_id=item.call_id, name=item.name)
            )
        elif item.type == "function_call_output":
            found.append(
                Entry(
                    item.id,
                    item.type,
                    call_id=item.call_id,
                    name=item.name,
                    output=item.output,
                )
            )
        else:
            found.append(Entry(item.id, item.type))
    return found


def talking(session: AgentSession[Any]) -> bool:
    speech = session.current_speech
    return speech is not None and not speech.done() and not speech.interrupted


def last_message_at(ctx: llm.ChatContext) -> float | None:
    times = [
        item.created_at
        for item in ctx.items
        if item.type in ("function_call", "function_call_output")
        or (
            item.type == "message"
            and item.role in ("user", "assistant")
            and not item.extra.get(SUMMARY_KEY)
        )
    ]
    return max(times, default=None)


class VocaAgent(Agent):
    def __init__(
        self,
        *,
        loader: InstructionLoader,
        instructions: InstructionSet,
        log: JsonlLog,
        cards: CardState,
        ctx_high: int,
        ctx_low: int,
        chat_ctx: llm.ChatContext | None = None,
        limits: Limits | None = None,
        summarizer: Summarizer | None = None,
        clock: Callable[[], float] = time.time,
        compacted_at: float | None = None,
        delegation: DelegationService | None = None,
        vocabulary: Vocabulary,
        decider: Decider | None = None,
        sorter: Sorter | None = None,
        sorter_prompt: SorterPrompt | None = None,
        glue_window_s: float = 3.0,
        action_silence_s: float = 0.5,
    ) -> None:
        assert sorter is None or sorter_prompt is not None
        super().__init__(instructions=instructions.text, chat_ctx=chat_ctx)
        self.loader = loader
        self.current = instructions
        self.log = log
        self.cards = cards
        self.ctx_high = ctx_high
        self.ctx_low = ctx_low
        self.limits = limits
        self.summarizer = summarizer
        self.pending: Pending | None = None
        self.on_compacted: OnCompacted | None = None
        self.clock = clock
        self.compacted_at = compacted_at if compacted_at is not None else self.clock()
        self.strikes = 0
        self.blocked_at: float | None = None
        self.delegation = delegation
        self.vocabulary = vocabulary
        self.decider = decider
        self.background: set[asyncio.Task[Any]] = set()
        self.deliverer: asyncio.Task[None] | None = None
        self.in_tool: set[str] = set()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.queued: set[str] = set()
        self.retry: set[str] = set()
        self.present = asyncio.Event()
        self.playout: dict[str, bool] = {}
        self.stopped = False
        self.announcements: deque[Announcement] = deque()
        self.speaking: Announcement | None = None
        self.active: Announcement | None = None
        self.wake = asyncio.Event()
        self.speaker: AgentSession[Any] | None = None
        self.heard_at = 0.0
        self.barged: Announcement | None = None
        self.announced: dict[str, str] = {}
        self.answered: dict[str, dict[str, Any]] = {}
        self.sorter = sorter
        self.sorter_prompt = sorter_prompt
        self.passed: list[str] = []
        self.glue_window_s = glue_window_s
        self.action_silence_s = action_silence_s
        self.user_speaking = False
        self.quiet_at = float("-inf")
        self.user_changed = asyncio.Event()

    def set_present(self, present: bool) -> None:
        if present:
            self.present.set()
        else:
            self.present.clear()

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self.stopped:
            coro.close()
            return
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self.background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger("voca.tasks").error(
                "delegation task failed", exc_info=task.exception()
            )

    def enqueue(self, task: Task) -> None:
        key = task.tool_call_id
        if (
            task.is_open
            or task.delivered_at is not None
            or key in self.queued
            or key in self.in_tool
        ):
            return
        self.queued.add(key)
        self.queue.put_nowait(key)
        self.wake.set()

    def announce(self, task: Task) -> None:
        if task.status != TaskStatus.WAITING_APPROVAL or task.approval is None:
            return
        key = task.tool_call_id
        if self.answered.get(key) == task.approval:
            return
        for item in (self.active, self.speaking, *self.announcements):
            if item is not None and item.key == key and item.approval == task.approval:
                return
        self.announcements = deque(
            item for item in self.announcements if item.key != key
        )
        self.announcements.append(Announcement(key, task.approval))
        self.wake.set()

    def _release(self, task: Task) -> None:
        key = task.tool_call_id
        waiting = task.status == TaskStatus.WAITING_APPROVAL
        answered = key in self.answered
        if not waiting or task.approval != self.answered.get(key):
            self.answered.pop(key, None)
        if (
            self.active is not None
            and self.active.key == key
            and (not waiting or task.approval != self.active.approval)
        ):
            if task.status == TaskStatus.RUNNING and not answered:
                self._spawn(
                    self._fact(
                        "list_tasks",
                        {},
                        {
                            "id": short_id(key),
                            "event": "approval_expired",
                            "by": "hermes",
                        },
                    )
                )
            self.active = None
            self.wake.set()
        if (
            self.barged is not None
            and self.barged.key == key
            and (not waiting or task.approval != self.barged.approval)
        ):
            self.barged = None
        if not waiting:
            self.announced.pop(key, None)
            self.announcements = deque(
                item for item in self.announcements if item.key != key
            )

    def _task_row(self, task: Task, **extra: object) -> None:
        self.log.write(
            "task",
            tool_call_id=task.tool_call_id,
            status=task.status.value,
            run_id=task.run_id,
            error=task.error,
            delivered=task.delivered_at is not None,
            **extra,
        )

    def on_task_change(self, task: Task) -> None:
        key = task.tool_call_id
        self.cards.publish_task(task)
        extra = {"interrupted": self.playout.pop(key)} if key in self.playout else {}
        self._task_row(task, **extra)
        self.enqueue(task)
        self._release(task)
        self.announce(task)

    async def enqueue_undelivered(self) -> None:
        if self.delegation is None:
            return
        undelivered = await self.delegation.repo.list_undelivered()
        waiting = await self.delegation.repo.list_open()
        for task in waiting:
            self.announce(task)
        for task in undelivered:
            self.enqueue(task)

    def start_recover(self) -> None:
        if self.delegation is not None:
            self._spawn(self._recover())

    async def _recover(self) -> None:
        assert self.delegation is not None
        for task in await self.delegation.recover():
            self.enqueue(task)

    def start_delivery(
        self, session: AgentSession[Any], sent: Callable[[], Awaitable[None]]
    ) -> None:
        if self.delegation is not None and self.deliverer is None and not self.stopped:
            self.speaker = session
            self.deliverer = asyncio.create_task(self._deliver_loop(session, sent))

    async def _ready(self, sent: Callable[[], Awaitable[None]]) -> None:
        while True:
            await self.present.wait()
            await sent()
            if self.present.is_set():
                return

    def _pick(self) -> Announcement | str | None:
        if self.active is None and self.announcements:
            return self.announcements.popleft()
        if not self.queue.empty():
            return self.queue.get_nowait()
        return None

    async def _deliver_loop(
        self, session: AgentSession[Any], sent: Callable[[], Awaitable[None]]
    ) -> None:
        while True:
            await self._ready(sent)
            item = self._pick()
            if item is None:
                self.wake.clear()
                await self.wake.wait()
                continue
            ok = True
            try:
                if isinstance(item, Announcement):
                    ok = await self._announce(session, item)
                else:
                    ok = await self._deliver(session, item)
            except Exception:
                key = item.key if isinstance(item, Announcement) else item
                logging.getLogger("voca.tasks").exception("delivery failed: %s", key)
            finally:
                if isinstance(item, str):
                    if item in self.retry:
                        self.retry.discard(item)
                        self.queue.put_nowait(item)
                    else:
                        self.queued.discard(item)
            if not ok:
                return

    async def _announce(self, session: AgentSession[Any], item: Announcement) -> bool:
        assert self.delegation is not None
        task = await self.delegation.repo.get(item.key)
        if (
            task is None
            or task.status != TaskStatus.WAITING_APPROVAL
            or task.approval != item.approval
        ):
            return True
        self.speaking = item
        try:
            text = announcement_text(task, self.vocabulary)
            try:
                handle = session.say(
                    text,
                    allow_interruptions=True,
                    add_to_chat_ctx=False,
                )
            except RuntimeError:
                return False
            self.announced[item.key] = text
            await handle.wait_for_playout()
        finally:
            self.speaking = None
        self.heard_at = time.time()
        self.barged = item if handle.interrupted else None
        item.attempts += 1
        self._task_row(task, announced=True, interrupted=handle.interrupted)
        approval = task.approval or {}
        await self._fact(
            "list_tasks",
            {},
            {
                "id": short_id(item.key),
                "event": "approval_requested",
                "text": clip(task.text, APPROVAL_LIMIT),
                "request": describe(approval.get("description")),
                "command": approval.get("command"),
                "interrupted": handle.interrupted,
            },
        )
        if not handle.interrupted or item.attempts >= ANNOUNCE_ATTEMPTS:
            current = await self.delegation.repo.get(item.key)
            if (
                current is not None
                and current.status == TaskStatus.WAITING_APPROVAL
                and current.approval == item.approval
                and self.active is None
                and self.answered.get(item.key) != item.approval
            ):
                self.active = item
        else:
            self.announcements.appendleft(item)
            self.wake.set()
        return True

    async def _deliver(self, session: AgentSession[Any], key: str) -> bool:
        assert self.delegation is not None
        task = await self.delegation.repo.get(key)
        if task is None or task.is_open or task.delivered_at is not None:
            return True
        try:
            handle = session.say(
                delivery_text(task), allow_interruptions=True, add_to_chat_ctx=True
            )
        except RuntimeError:
            return False
        await handle.wait_for_playout()
        await self._remember(task, handle.chat_items)
        if heard(handle):
            self.playout[key] = False
            await self.delegation.mark_delivered(key)
            return True
        self._task_row(task, interrupted=handle.interrupted)
        try:
            await session.wait_for_idle()
        except ActivityClosedError:
            return False
        if not handle.interrupted:
            await self.delegation.sleep(UNHEARD_RETRY_S)
        self.retry.add(key)
        return True

    async def _remember(self, task: Task, spoken: list[llm.ChatItem]) -> None:
        call_id = f"delivery_{task.tool_call_id}"
        ctx = self.chat_ctx.copy()
        if ctx.get_by_id(call_id) is not None:
            return
        at = spoken[0].created_at if spoken else time.time()
        ctx.insert(
            [
                llm.FunctionCall(
                    id=call_id,
                    call_id=call_id,
                    name="delegate",
                    arguments=json.dumps({"task": task.text}, ensure_ascii=False),
                    created_at=at - 2e-6,
                ),
                llm.FunctionCallOutput(
                    id=f"{call_id}_output",
                    call_id=call_id,
                    name="delegate",
                    output=result_outcome(task),
                    is_error=task.status != TaskStatus.SUCCEEDED,
                    created_at=at - 1e-6,
                ),
            ]
        )
        await self.update_chat_ctx(ctx)

    async def _fact(
        self,
        name: str,
        arguments: dict[str, Any],
        output: dict[str, Any],
        *,
        is_error: bool = False,
    ) -> None:
        call_id = f"voca_{uuid.uuid4().hex[:16]}"
        at = time.time()
        ctx = self.chat_ctx.copy()
        ctx.insert(
            [
                llm.FunctionCall(
                    id=call_id,
                    call_id=call_id,
                    name=name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                    created_at=at - 2e-6,
                ),
                llm.FunctionCallOutput(
                    id=f"{call_id}_output",
                    call_id=call_id,
                    name=name,
                    output=json.dumps(output, ensure_ascii=False),
                    is_error=is_error,
                    created_at=at - 1e-6,
                ),
            ]
        )
        await self.update_chat_ctx(ctx)

    async def _answer(
        self, active: Announcement, choice: Choice, *, speak: bool
    ) -> None:
        assert self.delegation is not None and self.speaker is not None
        task = await self.delegation.repo.get(active.key)
        approved = False
        id = short_id(active.key)
        try:
            await self.delegation.approve(active.key, choice, expected=active.approval)
            text = APPROVAL_REPLIES[choice]
            release = approved = True
        except (ApprovalNotActive, TaskNotFound):
            text = APPROVAL_GONE
            release = True
        except DelegationError:
            text = APPROVAL_FAILED
            release = False
        if approved:
            event = "approved" if choice == "once" else "denied"
            await self._fact(
                "list_tasks", {}, {"id": id, "event": event, "by": "voice"}
            )
        else:
            await self._fact(
                "list_tasks",
                {},
                {"id": id, "event": "approval_failed", "by": "voice", "error": text},
                is_error=True,
            )
        if speak:
            try:
                self.speaker.say(text, allow_interruptions=True, add_to_chat_ctx=True)
            except RuntimeError:
                pass
        if approved:
            self._settled(active.key, active.approval)
        elif release and self.active is active:
            self.active = None
            self.wake.set()
        if approved and task is not None:
            self._task_row(task, approval=choice, by="voice")

    async def _unclear(self, active: Announcement) -> None:
        assert self.speaker is not None
        await self._fact(
            "list_tasks",
            {},
            {
                "id": short_id(active.key),
                "event": "approval_not_recognized",
                "by": "voice",
            },
        )
        try:
            handle = self.speaker.say(
                unclear_text(self.vocabulary),
                allow_interruptions=True,
                add_to_chat_ctx=True,
            )
        except RuntimeError:
            return
        self._spawn(self._heard(handle))

    async def _heard(self, handle: SpeechHandle) -> None:
        await handle.wait_for_playout()
        self.heard_at = time.time()

    def _settled(self, key: str, approval: dict[str, Any]) -> None:
        self.answered[key] = approval
        self.announced.pop(key, None)
        if self.barged is not None and self.barged.key == key:
            self.barged = None
        self.announcements = deque(
            item for item in self.announcements if item.key != key
        )
        if self.active is not None and self.active.key == key:
            self.active = None
        self.wake.set()

    async def approve_from_page(
        self, key: str, request_id: str, choice: Choice
    ) -> dict[str, Any]:
        if self.delegation is None:
            return {"ok": False, "error": DELEGATE_UNAVAILABLE}
        task = await self.delegation.repo.get(key)
        if (
            task is None
            or task.status != TaskStatus.WAITING_APPROVAL
            or task.approval is None
            or task.approval.get("request_id") != request_id
        ):
            return {"ok": False, "error": APPROVAL_GONE}
        try:
            await self.delegation.approve(key, choice, expected=task.approval)
        except (ApprovalNotActive, TaskNotFound):
            return {"ok": False, "error": APPROVAL_GONE}
        except DelegationError:
            return {"ok": False, "error": APPROVAL_FAILED}
        self._settled(key, task.approval)
        event = "approved" if choice == "once" else "denied"
        await self._fact(
            "list_tasks", {}, {"id": short_id(key), "event": event, "by": "button"}
        )
        self._task_row(task, approval=choice, by="button")
        return {"ok": True}

    async def cancel_from_page(self, key: str) -> dict[str, Any]:
        if self.delegation is None:
            return {"ok": False, "error": DELEGATE_UNAVAILABLE}
        task = await self.delegation.repo.get(key)
        if task is None:
            return {"ok": False, "error": TASK_UNKNOWN}
        if not task.is_open:
            return {"ok": False, "error": TASK_CLOSED}
        self.in_tool.add(key)
        self._spawn(self._cancel_by_button(key))
        return {"ok": True}

    async def _cancel_by_button(self, key: str) -> None:
        assert self.delegation is not None
        try:
            final = await self.delegation.cancel(key)
        except DelegationError as error:
            logging.getLogger("voca.tasks").warning("cancel failed: %s: %r", key, error)
            return
        finally:
            self.in_tool.discard(key)
        view = task_view(final, self.active)
        output = {"id": view["id"], "event": "cancelled", "by": "button"}
        output.update(
            {
                field: view[field]
                for field in ("status", "error", "result")
                if field in view
            }
        )
        await self._fact("list_tasks", {}, output)

    async def _pick_task(
        self,
        id: str | None,
        allowed: Callable[[Task], bool],
        unfit: str = TASK_UNKNOWN,
    ) -> Task:
        assert self.delegation is not None
        found = await self.delegation.repo.list_open()
        candidates = [task for task in found if allowed(task)]
        if (
            id
            and not any(task.tool_call_id.startswith(id) for task in candidates)
            and any(task.tool_call_id.startswith(id) for task in found)
        ):
            raise ToolError(unfit)
        if id:
            exact = [task for task in candidates if task.tool_call_id == id]
            if exact:
                return exact[0]
            candidates = [
                task for task in candidates if task.tool_call_id.startswith(id)
            ]
            if not candidates:
                closed = await self.delegation.repo.get(id)
                if closed is not None and not closed.is_open:
                    raise ToolError(TASK_CLOSED)
        if not candidates:
            raise ToolError(TASK_UNKNOWN)
        if len(candidates) > 1:
            raise ToolError(TASK_AMBIGUOUS)
        return candidates[0]

    async def stop_delegation(self) -> None:
        self.stopped = True
        tasks = [*self.background]
        if self.deliverer is not None:
            tasks.append(self.deliverer)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def maybe_compact(self) -> None:
        if (
            self.limits is None
            or self.summarizer is None
            or self.limits.threshold == 0
            or self.pending is not None
        ):
            return
        items = records(self.chat_ctx.copy())
        total = estimate(items)
        if total < self.limits.threshold:
            return
        if self.strikes >= BLOCK_STRIKES:
            now = self.clock()
            if self.blocked_at is None:
                self.blocked_at = now
                self.log.write(
                    "compaction_blocked",
                    strikes=self.strikes,
                    estimate=total,
                    threshold=self.limits.threshold,
                    retry_in_s=BLOCK_RETRY_S,
                )
                return
            if now - self.blocked_at < BLOCK_RETRY_S:
                return
            self.strikes = 1
            self.blocked_at = None
        self.start_compaction("threshold", items)

    def start_compaction(self, trigger: str, items: list[Item]) -> bool:
        assert self.limits is not None and self.summarizer is not None
        found = plan(items, self.limits)
        if found is None:
            return False
        if not self.summarizer.ready:
            self.log.write(
                "compaction_failed",
                trigger=trigger,
                reason="no_key",
                estimate_before=found.estimate_before,
                ms=0,
            )
            return False
        prompt = build_prompt(
            found.middle,
            found.previous_body,
            today=date.today(),
            budget=summary_budget(found.folded_tokens),
        )
        self.pending = Pending(
            found, asyncio.create_task(self.summarizer.summarize(prompt)), trigger
        )
        return True

    def check_due(self) -> Trigger | None:
        if self.limits is None or self.summarizer is None or self.pending is not None:
            return None
        ctx = self.chat_ctx.copy()
        items = records(ctx)
        last = last_message_at(ctx)
        trigger = due(
            datetime.fromtimestamp(self.clock()),
            None if last is None else datetime.fromtimestamp(last),
            datetime.fromtimestamp(self.compacted_at),
            self.limits.idle_hours,
            self.limits.daily_at,
            estimate(items),
            self.limits.tail_budget,
        )
        if trigger is not None and self.start_compaction(trigger, items):
            return trigger
        return None

    async def accept_compaction(self) -> bool:
        pending = self.pending
        if pending is None or not pending.task.done():
            return False
        self.pending = None
        error = pending.task.exception()
        result = pending.task.result() if error is None else Failure("error", 0)
        if isinstance(result, Failure):
            named = {} if error is None else {"error": type(error).__name__}
            self.log.write(
                "compaction_failed",
                trigger=pending.trigger,
                reason=result.reason,
                estimate_before=pending.plan.estimate_before,
                ms=result.ms,
                **named,
            )
            return False
        ctx = self.chat_ctx.copy()
        ctx.items = [
            item
            for item in ctx.items
            if item.id not in pending.plan.folded
            and item.id != pending.plan.previous_id
        ]
        anchor = split_turns(records(ctx))[0][0]
        index = ctx.index_by_id(anchor.id)
        assert index is not None
        at = ctx.items[index].created_at - 1e-6
        message = llm.ChatMessage(
            role="assistant",
            content=[wrap(result.body)],
            extra={SUMMARY_KEY: True},
            created_at=at,
        )
        ctx.items.insert(index, message)
        await self.update_chat_ctx(ctx)
        after = estimate(records(self.chat_ctx.copy()))
        if pending.trigger == "threshold":
            if self.limits is not None and after >= self.limits.threshold:
                self.strikes += 1
            else:
                self.strikes = 0
        data: dict[str, object] = {
            "trigger": pending.trigger,
            "estimate_before": pending.plan.estimate_before,
            "after": after,
            "folded_turns": pending.plan.folded_turns,
        }
        self.log.write("compaction", **data, ms=result.ms, summary_tokens=result.tokens)
        self.compacted_at = self.clock()
        if self.on_compacted is not None:
            self.on_compacted(message.id, at, result.body, data)
        return True

    def cancel_compaction(self) -> None:
        if self.pending is not None:
            self.pending.task.cancel()
        self.pending = None

    async def _on_request(self, active: Announcement, message: llm.ChatMessage) -> bool:
        assert self.delegation is not None
        text = message.text_content or ""
        started = message.metrics.get("started_speaking_at")
        early = started is not None and started < self.heard_at
        barged = self.barged is active
        if (early and not barged) or (
            (early or barged)
            and echoes(text, self.announced.get(active.key, ""), self.vocabulary)
        ):
            await self._unclear(active)
            raise StopResponse()
        task = await self.delegation.repo.get(active.key)
        if (
            task is None
            or task.status != TaskStatus.WAITING_APPROVAL
            or task.approval != active.approval
        ):
            return False
        approval = task.approval or {}
        situation = Situation(
            task=clip(task.text, APPROVAL_LIMIT),
            request=describe(approval.get("description")),
            command=approval.get("command"),
            announcement=self.announced.get(active.key)
            or announcement_text(task, self.vocabulary),
            phrase=text,
        )
        decided: Decision | DecisionFailure = (
            await self.decider.decide(situation)
            if self.decider is not None
            else DecisionFailure("disabled", 0)
        )
        if isinstance(decided, Decision):
            self.log.write(
                "approval_decision",
                tool_call_id=active.key,
                phrase=text,
                verdict=decided.verdict,
                picked=decided.picked,
                confidence=decided.confidence,
                ms=decided.ms,
            )
        else:
            self.log.write(
                "approval_decision",
                tool_call_id=active.key,
                phrase=text,
                fallback=decided.reason,
                ms=decided.ms,
            )
        if self.answered.get(active.key) == active.approval:
            return False
        if self.active is not active and self.barged is not active:
            return False
        if isinstance(decided, DecisionFailure):
            parsed = parse(text, self.vocabulary)
            if not isinstance(parsed, Phrase):
                await self._unclear(active)
                raise StopResponse()
            await self._answer(active, parsed.choice, speak=not parsed.tail)
            if not parsed.tail:
                raise StopResponse()
            message.content = [parsed.tail]
            return True
        if decided.verdict in ("approve", "deny"):
            choice: Choice = "once" if decided.verdict == "approve" else "deny"
            await self._answer(active, choice, speak=True)
            raise StopResponse()
        if decided.verdict in ("approve_more", "deny_more"):
            more: Choice = "once" if decided.verdict == "approve_more" else "deny"
            await self._answer(active, more, speak=False)
            return True
        if decided.verdict == "repeat":
            self._repeat(active)
            raise StopResponse()
        if decided.verdict == "unclear":
            await self._unclear(active)
            raise StopResponse()
        return False

    def _repeat(self, item: Announcement) -> None:
        item.attempts = 0
        if self.active is item:
            self.active = None
        if self.barged is item:
            self.barged = None
        self.announcements = deque(
            other
            for other in self.announcements
            if other.key != item.key or other.approval != item.approval
        )
        self.announcements.appendleft(item)
        self.wake.set()

    def on_user_state(self, ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            self.user_speaking = True
        elif ev.old_state == "speaking":
            self.user_speaking = False
            self.quiet_at = ev.created_at
        self.user_changed.set()
        self.user_changed = asyncio.Event()

    async def _hold(self, handle: SpeechHandle) -> bool:
        while not handle.interrupted:
            left = self.action_silence_s - (self.clock() - self.quiet_at)
            if not self.user_speaking and left <= 0:
                return True
            fut = asyncio.ensure_future(
                asyncio.wait_for(
                    self.user_changed.wait(),
                    timeout=None if self.user_speaking else left,
                )
            )
            try:
                await handle.wait_if_not_interrupted([fut])
            except TimeoutError:
                pass
            finally:
                if not fut.done():
                    fut.cancel()
        return False

    async def _sort(self, text: str, branch: str, *, speaking: bool) -> bool:
        assert self.sorter is not None and self.sorter_prompt is not None
        agent_last = ""
        for item in reversed(self.chat_ctx.items):
            if item.type == "message" and item.role == "assistant":
                agent_last = item.text_content or ""
                break
        requests: list[tuple[str, str | None]] = []
        if self.delegation is not None:
            for task in await self.delegation.repo.list_open():
                if (
                    task.status == TaskStatus.WAITING_APPROVAL
                    and task.approval is not None
                ):
                    requests.append(
                        (
                            describe(task.approval.get("description")),
                            task.approval.get("command"),
                        )
                    )
        result = await self.sorter.sort(sorter_state(agent_last, requests, text))
        dropped = drop(text, result, self.sorter_prompt, speaking=speaking)
        if isinstance(result, Sorted):
            self.log.write(
                "sorter",
                branch=branch,
                text=text,
                speaking=speaking,
                dropped=dropped,
                ms=result.ms,
                choice=result.kind,
                confidence=result.confidence,
            )
        else:
            self.log.write(
                "sorter",
                branch=branch,
                text=text,
                speaking=speaking,
                dropped=dropped,
                ms=result.ms,
                fallback=result.reason,
            )
        return dropped

    async def _screen(
        self,
        events: AsyncIterable[stt.SpeechEvent | str],
        speaking: Callable[[], bool],
    ) -> AsyncGenerator[stt.SpeechEvent | str]:
        async for event in events:
            if (
                self.sorter is None
                or not isinstance(event, stt.SpeechEvent)
                or event.type != stt.SpeechEventType.FINAL_TRANSCRIPT
                or not event.alternatives
            ):
                yield event
                continue
            text = event.alternatives[0].text.strip()
            if not text or not speaking():
                yield event
                continue
            if await self._sort(text, "stt", speaking=True):
                continue
            self.passed.append(text)
            yield event

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> AsyncGenerator[stt.SpeechEvent | str]:
        async for event in self._screen(
            Agent.default.stt_node(self, audio, model_settings),
            lambda: talking(self.session),
        ):
            yield event

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        if self.sorter is not None:
            text = new_message.text_content or ""
            passed, self.passed = self.passed, []
            if (
                text not in passed
                and text != " ".join(passed)
                and await self._sort(text, "hook", speaking=False)
            ):
                raise StopResponse()
        changed = False
        glued = glue(
            entries(self.chat_ctx),
            started_at=new_message.metrics.get("started_speaking_at"),
            window_s=self.glue_window_s,
        )
        if glued is not None:
            ctx = self.chat_ctx.copy()
            ctx.items = [item for item in ctx.items if item.id not in glued.drop]
            await self.update_chat_ctx(ctx)
            new_message.content = [
                f"{glued.fragment} {new_message.text_content or ''}".strip()
            ]
            self.log.write(
                "glue",
                removed=len(glued.drop),
                fragment=glued.fragment,
                gap_s=round(glued.gap_s, 3),
            )
            changed = True
        active = self.active or self.barged
        if active is not None and self.delegation is not None:
            changed = await self._on_request(active, new_message) or changed
        loaded = self.loader.load()
        if (loaded.sha256, loaded.source, loaded.error) != (
            self.current.sha256,
            self.current.source,
            self.current.error,
        ):
            if loaded.sha256 != self.current.sha256:
                await self.update_instructions(loaded.text)
                changed = True
            self.current = loaded
            self.log.write(
                "instructions",
                sha256=loaded.sha256,
                source=loaded.source,
                error=loaded.error,
            )
            self.cards.set_instructions(loaded)
        if await self.accept_compaction():
            changed = True
        ctx = self.chat_ctx.copy()
        trim = trim_chat_ctx(records(ctx), high=self.ctx_high, low=self.ctx_low)
        if trim is not None:
            ctx.items = [item for item in ctx.items if item.id not in trim.drop]
            await self.update_chat_ctx(ctx)
            self.log.write(
                "context_trimmed",
                removed=len(trim.drop),
                estimate_before=trim.estimate_before,
                after=trim.estimate_after,
            )
            changed = True
        self.check_due()
        if changed:
            turn_ctx.items = self.chat_ctx.copy().items

    def on_component_error(self, component: str) -> None:
        text = refusal(component, tts_down=self.cards.components["tts"] == "down")
        if text is not None:
            self.session.say(text, add_to_chat_ctx=False)

    @function_tool
    async def delegate(self, context: RunContext[Any], task: str) -> llm.ToolResult:
        """Поручить задачу основному агенту Hermes: работа с проектами, файлами, поиском, заметками и всё, что требует времени. task — полная формулировка поручения по-русски."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        key = context.function_call.call_id
        if not await self._hold(context.speech_handle):
            output = {"id": short_id(key), "event": "not_sent"}
            return llm.ToolResult(
                json.dumps(output, ensure_ascii=False), reply_required=False
            )
        self.in_tool.add(key)
        try:
            found = await self.delegation.delegate(key, task)
        except Unavailable:
            await self.delegation.mark_delivered(key)
            raise ToolError(DELEGATE_UNAVAILABLE) from None
        finally:
            self.in_tool.discard(key)
        if found.is_open and found.status != TaskStatus.QUEUED:
            self._spawn(self.delegation.watch(key))
        else:
            self.enqueue(found)
        spoken = False
        if self.speaker is not None:
            try:
                self.speaker.say(
                    submitted_text(task),
                    allow_interruptions=True,
                    add_to_chat_ctx=False,
                )
                spoken = True
            except RuntimeError:
                pass
        if not spoken:
            logging.getLogger("voca.tasks").warning(
                "delegation acknowledgement not spoken: %s", key
            )
        output = {"id": short_id(key), "event": "submitted"}
        return llm.ToolResult(
            json.dumps(output, ensure_ascii=False), reply_required=not spoken
        )

    @function_tool
    async def list_tasks(self) -> str:
        """Показать поручения Hermes: открытые и последние завершённые, статус, ждёт ли подтверждения, краткий результат. Вызывай, когда спрашивают о поручениях."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        found = await self.delegation.repo.list_open()
        closed = [
            task for task in await self.delegation.repo.list_all() if not task.is_open
        ]
        found.extend(closed[:RECENT_LIMIT])
        tasks = [task_view(task, self.active) for task in found]
        return json.dumps({"tasks": tasks}, ensure_ascii=False)

    @function_tool
    async def list_notifications(self) -> str:
        """Показать незакрытые события Hermes по порядку поступления: запросы подтверждения с уровнем опасности, результаты и сбои, которые ещё не прозвучали до конца. Вызывай, когда спрашивают, что пришло от Hermes."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        now = self.delegation.clock()
        notifications = []
        for event in await self.delegation.repo.list_events(open_only=True):
            task = await self.delegation.repo.get(event.tool_call_id)
            if task is None:
                continue
            notifications.append(notification_view(event, task, now))
        return json.dumps({"notifications": notifications}, ensure_ascii=False)

    @function_tool
    async def cancel_task(self, id: str | None = None) -> str:
        """Отменить поручение Hermes. id — из list_tasks; можно не указывать, если поручение одно."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        task = await self._pick_task(id, lambda task: True)
        key = task.tool_call_id
        self.in_tool.add(key)
        try:
            final = await self.delegation.cancel(key)
        except TaskNotOpen:
            raise ToolError(TASK_CLOSED) from None
        except DelegationError:
            raise ToolError(ACTION_FAILED) from None
        finally:
            self.in_tool.discard(key)
        return json.dumps(task_view(final, self.active), ensure_ascii=False)

    @function_tool
    async def steer_task(self, text: str, id: str | None = None) -> str:
        """Уточнить идущее поручение Hermes: text — уточнение по-русски."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        found = await self.delegation.repo.list_open()
        if found and not any(task.status == TaskStatus.RUNNING for task in found):
            raise ToolError(NOT_STEERABLE)
        task = await self._pick_task(
            id, lambda task: task.status == TaskStatus.RUNNING, NOT_STEERABLE
        )
        try:
            await self.delegation.steer(task.tool_call_id, text)
        except NotSteerable:
            raise ToolError(NOT_STEERABLE) from None
        except DelegationError:
            raise ToolError(ACTION_FAILED) from None
        output = {
            "id": short_id(task.tool_call_id),
            "event": "steered",
            "accepted": True,
        }
        return json.dumps(output, ensure_ascii=False)

    @function_tool
    async def repeat_approval(self) -> None:
        """Повторить вслух активный запрос подтверждения Hermes."""
        if self.active is None:
            raise ToolError(NO_REQUEST)
        self._repeat(self.active)

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[llm.ChatChunk | str | FlushSentinel]:
        buffer = ""
        inside = False
        async for item in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(item, str):
                text, buffer, inside = scan(buffer + item, inside)
                if text:
                    yield text
            elif (
                isinstance(item, llm.ChatChunk)
                and item.delta is not None
                and item.delta.content
            ):
                text, buffer, inside = scan(buffer + item.delta.content, inside)
                if text or item.delta.tool_calls or item.usage is not None:
                    yield llm.ChatChunk(
                        id=item.id,
                        delta=llm.ChoiceDelta(
                            role=item.delta.role,
                            content=text,
                            tool_calls=item.delta.tool_calls,
                        ),
                        usage=item.usage,
                    )
            else:
                yield item
        if buffer and not inside:
            yield buffer
