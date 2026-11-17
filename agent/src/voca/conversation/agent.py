import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from livekit.agents import (
    FlushSentinel,
    RunContext,
    StopResponse,
    ToolError,
    function_tool,
    llm,
)
from livekit.agents.voice import Agent, AgentSession, ModelSettings, SpeechHandle

from voca.clientio.cards import CardState
from voca.conversation.approval import Choice, NearMiss, parse
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
from voca.conversation.instructions import InstructionLoader, InstructionSet
from voca.conversation.summarizer import Failure, Summarizer, Summary
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
DELEGATE_ACCEPTED = "Поручение принято, сообщу, когда будет результат"
DELEGATE_UNAVAILABLE = "Делегирование сейчас недоступно"
SPEECH_LIMIT = 300
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
APPROVAL_PROMPT = "Подтвердите на экране или скажите „Подтверждаю“ или „Отклоняю“."
APPROVAL_REPLIES = {"once": "Подтверждено", "deny": "Отклонено"}
APPROVAL_GONE = "Запрос подтверждения уже не активен"
APPROVAL_FAILED = "Не удалось передать подтверждение"
APPROVAL_UNCLEAR = "Не разобрал подтверждение. Скажите „Подтверждаю“ или „Отклоняю“, или ответьте на экране"
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


def announcement_text(task: Task) -> str:
    head = clip(task.text, APPROVAL_LIMIT)
    description = describe((task.approval or {}).get("description"))
    return f"Hermes просит подтверждения по поручению «{head}»: {description}. {APPROVAL_PROMPT}"


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
    ) -> None:
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
        self.background: set[asyncio.Task[Any]] = set()
        self.deliverer: asyncio.Task[None] | None = None
        self.in_tool: set[str] = set()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.queued: set[str] = set()
        self.present = asyncio.Event()
        self.playout: dict[str, bool] = {}
        self.stopped = False
        self.announcements: deque[Announcement] = deque()
        self.speaking: Announcement | None = None
        self.active: Announcement | None = None
        self.wake = asyncio.Event()
        self.speaker: AgentSession[Any] | None = None
        self.heard_at = 0.0
        self.answered: dict[str, dict[str, Any]] = {}

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
        if not waiting:
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
            try:
                handle = session.say(
                    announcement_text(task),
                    allow_interruptions=True,
                    add_to_chat_ctx=False,
                )
            except RuntimeError:
                return False
            await handle.wait_for_playout()
        finally:
            self.speaking = None
        self.heard_at = time.time()
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
        self.playout[key] = handle.interrupted
        await self._remember(task, handle.chat_items)
        await self.delegation.mark_delivered(key)
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
            if choice == "once":
                await self._fact(
                    "list_tasks", {}, {"id": id, "event": "approved", "by": "voice"}
                )
            else:
                await self._fact(
                    "deny_approval",
                    {"id": id},
                    {"id": id, "event": "denied", "by": "voice"},
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
                APPROVAL_UNCLEAR, allow_interruptions=True, add_to_chat_ctx=True
            )
        except RuntimeError:
            return
        self._spawn(self._heard(handle))

    async def _heard(self, handle: SpeechHandle) -> None:
        await handle.wait_for_playout()
        self.heard_at = time.time()

    def _settled(self, key: str, approval: dict[str, Any]) -> None:
        self.answered[key] = approval
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

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        parsed = parse(new_message.text_content)
        changed = False
        if parsed is not None and self.active is not None:
            active = self.active
            started = new_message.metrics.get("started_speaking_at")
            if isinstance(parsed, NearMiss) or (
                started is not None and started < self.heard_at
            ):
                await self._unclear(active)
                raise StopResponse()
            await self._answer(active, parsed.choice, speak=not parsed.tail)
            if not parsed.tail:
                raise StopResponse()
            new_message.content = [parsed.tail]
            changed = True
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
    async def delegate(self, context: RunContext[Any], task: str) -> str:
        """Поручить задачу основному агенту Hermes: работа с проектами, файлами, поиском, заметками и всё, что требует времени. task — полная формулировка поручения по-русски."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        key = context.function_call.call_id
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
        return DELEGATE_ACCEPTED

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
    async def deny_approval(self, id: str | None = None) -> str:
        """Отклонить запрос подтверждения Hermes. Подтвердить запрос может только пользователь."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        if id is None and self.active is not None:
            found = await self.delegation.repo.get(self.active.key)
            if found is None:
                raise ToolError(APPROVAL_GONE)
            task = found
        else:
            task = await self._pick_task(
                id,
                lambda task: (
                    task.status == TaskStatus.WAITING_APPROVAL
                    and task.approval is not None
                ),
                NO_REQUEST,
            )
        key = task.tool_call_id
        try:
            await self.delegation.approve(key, "deny", expected=task.approval)
        except (ApprovalNotActive, TaskNotFound):
            raise ToolError(APPROVAL_GONE) from None
        except DelegationError:
            raise ToolError(APPROVAL_FAILED) from None
        self._settled(key, task.approval or {})
        self._task_row(task, approval="deny", by="model")
        return json.dumps({"id": short_id(key), "event": "denied"}, ensure_ascii=False)

    @function_tool
    async def repeat_approval(self) -> None:
        """Повторить вслух активный запрос подтверждения Hermes."""
        if self.active is None:
            raise ToolError(NO_REQUEST)
        item = self.active
        item.attempts = 0
        self.active = None
        self.announcements.appendleft(item)
        self.wake.set()

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
