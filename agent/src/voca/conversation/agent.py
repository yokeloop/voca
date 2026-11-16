import asyncio
import logging
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from livekit.agents import FlushSentinel, RunContext, ToolError, function_tool, llm
from livekit.agents.voice import Agent, AgentSession, ModelSettings

from voca.clientio.cards import CardState
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
from voca.conversation.instructions import InstructionLoader, InstructionSet
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.delegation.model import GATEWAY_RESTART, TIMEOUT, Task, TaskStatus
from voca.delegation.ports import Unavailable
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
FAILURE_REASONS = {
    TIMEOUT: "не дождался результата",
    GATEWAY_RESTART: "Hermes перезапускался",
}


@dataclass
class Pending:
    plan: Plan
    task: asyncio.Task[Summary | Failure]
    trigger: str


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
    reason = FAILURE_REASONS.get(task.error or "", "Hermes вернул ошибку")
    return f"Поручение не выполнено: {reason}."


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

    def on_task_change(self, task: Task) -> None:
        key = task.tool_call_id
        self.cards.publish_task(task)
        extra = {"interrupted": self.playout.pop(key)} if key in self.playout else {}
        self.log.write(
            "task",
            tool_call_id=key,
            status=task.status.value,
            run_id=task.run_id,
            error=task.error,
            delivered=task.delivered_at is not None,
            **extra,
        )
        self.enqueue(task)

    async def enqueue_undelivered(self) -> None:
        if self.delegation is None:
            return
        for task in await self.delegation.repo.list_undelivered():
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
            self.deliverer = asyncio.create_task(self._deliver_loop(session, sent))

    async def _deliver_loop(
        self, session: AgentSession[Any], sent: Callable[[], Awaitable[None]]
    ) -> None:
        while True:
            key = await self.queue.get()
            delivered = True
            try:
                while True:
                    await self.present.wait()
                    await sent()
                    if self.present.is_set():
                        break
                delivered = await self._deliver(session, key)
            except Exception:
                logging.getLogger("voca.tasks").exception("delivery failed: %s", key)
            finally:
                self.queued.discard(key)
            if not delivered:
                return

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
        await self.delegation.mark_delivered(key)
        return True

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
        changed = False
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
