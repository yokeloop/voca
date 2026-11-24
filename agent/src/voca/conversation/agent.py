import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    Awaitable,
    Callable,
    Coroutine,
)
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
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
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)

from voca.clientio.cards import CardState
from voca.conversation.approval_judge import (
    Judgement,
    Place,
    PlaceKind,
    Request,
    Rules,
    essence_spoken,
    judge,
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
from voca.conversation.memory import compose, read_memory
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.conversation.verdict import (
    DecisionFailure,
    Sorted,
    Sorter,
    SorterPrompt,
    drop,
    sorter_state,
)
from voca.delegation.danger import Danger
from voca.delegation.events import DENIED, Event, EventKind, EventState, open_request
from voca.delegation.model import (
    CANCELLED,
    GATEWAY_RESTART,
    TIMEOUT,
    Choice,
    Task,
    TaskStatus,
)
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
APPROVAL_GONE = "Запрос подтверждения уже не активен"
APPROVAL_FAILED = "Не удалось передать подтверждение"
REQUEST_UNKNOWN = "Нет такого запроса"
REQUEST_AMBIGUOUS = "Неясно, какой запрос: назовите id из list_notifications"
APPROVAL_KEY = "approval:"
REMIND_KEY = "remind:"
BUTTON_KEY = "button:"
APPROVAL_TIMEOUT_S = 300.0
APPROVAL_REMINDER_S = 60.0
FINAL_WAIT_S = 3.0
EVENT_SILENCE_S = 1.5
BUSY = ("thinking", "speaking")


@dataclass
class Pending:
    plan: Plan
    task: asyncio.Task[Summary | Failure]
    trigger: str


@dataclass
class Reading:
    ids: list[str]
    handle: SpeechHandle


@dataclass
class Notes:
    text: str | None
    changed: bool


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


def result_outcome(task: Task, denied: bool = False) -> str:
    if task.status == TaskStatus.SUCCEEDED:
        outcome = {"status": task.status.value, "result": task.result or ""}
    else:
        reason = FAILURE_REASONS.get(task.error or "", FAILURE_DEFAULT)
        outcome = {"status": task.status.value, "error": reason}
        if task.error:
            outcome["detail"] = task.error
    if denied:
        outcome["approval"] = "denied"
    return json.dumps(outcome, ensure_ascii=False)


def submitted_text(task: str) -> str:
    return f"Поручил Hermes: «{clip(task)}». Сообщу результат."


def short_id(key: str) -> str:
    return key


def task_view(task: Task, denied: bool = False) -> dict[str, Any]:
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
        }
    if denied and task.status != TaskStatus.WAITING_APPROVAL:
        view["approval"] = "denied"
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


EVENT_PROMPT = (
    "Пока ты молчал, от Hermes пришли события по поручениям пользователя, они ниже в JSON. "
    "Скажи о них сам, своими словами, одной–двумя короткими фразами: кратко перескажи суть "
    "результата или причину сбоя, не читай результат целиком, не называй поля и id. "
    "Несколько событий упомяни одной фразой. Если до этого разговор шёл о другом, начни с «Кстати». "
    "Инструменты не вызывай."
)


REQUEST_PROMPT = (
    "От Hermes пришли запросы на разрешение действий по поручениям пользователя, они ниже в JSON. "
    "Упомяни их одной короткой фразой: сколько запросов ждут ответа, не называя действие, команду и id, "
    "и спроси, зачитать ли. Запрос зачитывается только инструментом open_approval, когда пользователь попросит."
)
REMIND_PROMPT = (
    "Запросы Hermes ниже ждут ответа пользователя и меньше чем через минуту истекут: Hermes сам их отклонит. "
    "Напомни о них одной короткой фразой и назови действие."
)
BUTTON_PROMPT = (
    "Пользователь только что ответил на запрос Hermes кнопкой на экране, ниже в JSON — что он выбрал. "
    "Скажи об этом одной короткой фразой, своими словами."
)
TOOLS_OFF = "Инструменты не вызывай."
ASK_NOTE = "Пользователь спрашивает о запросе Hermes. Ответь по полному тексту запроса ниже в JSON. Разрешить или отклонить запрос ты не можешь: это делает агент по ответу пользователя."
CLARIFY_WHICH_NOTE = "Непонятно, к какому запросу Hermes относится ответ пользователя. Задай один короткий вопрос, назвав действия запросов ниже в JSON. Не говори, что запрос разрешён или отклонён."
CLARIFY_SURE_NOTE = "Неясно, разрешает пользователь запрос Hermes ниже в JSON или отклоняет. Задай один короткий вопрос, назвав действие. Не говори, что запрос разрешён или отклонён."
CONFIRM_NOTE = "Запрос Hermes ниже опасный, короткого «да» мало. Переспроси одной фразой, назвав действие и предмет, например «Точно удалить каталог целиком?». Не говори, что запрос разрешён."
LEFT_NOTE = "Ответ пользователя на запрос Hermes так и не понятен. Скажи одной фразой, что запрос остаётся в очереди и его можно разрешить или отклонить позже голосом или кнопкой на экране. Больше не переспрашивай."
PENDING_NOTE = "Запрос Hermes ниже всё ещё ждёт ответа пользователя. Ответь на реплику как обычно и в конце одной фразой скажи, что запрос ждёт ответа."
UNAVAILABLE_NOTE = "Модель решений сейчас недоступна: голосом запрос Hermes не разрешить и не отклонить. Если пользователь отвечал на запрос ниже, скажи, что ответ не разобран, и предложи кнопки на экране; иначе ответь как обычно."
DENIED_NOTE = "Запрос отклонён, поручение не продолжится. Чтобы выполнить его снова, нужен новый вызов delegate."
NOTES: dict[str, str] = {
    "confirm": CONFIRM_NOTE,
    "clarify_which": CLARIFY_WHICH_NOTE,
    "clarify_sure": CLARIFY_SURE_NOTE,
    "asks": ASK_NOTE,
    "left": LEFT_NOTE,
    "unavailable": UNAVAILABLE_NOTE,
    "pass": PENDING_NOTE,
}
PLACES: dict[str, PlaceKind] = {
    "confirm": "confirm",
    "clarify_which": "clarify",
    "clarify_sure": "clarify",
    "asks": "asks",
}
CLARIFYING = ("clarify_which", "clarify_sure", "confirm")


def events_prompt(
    views: list[dict[str, Any]],
    requests: list[dict[str, Any]] | None = None,
    reminders: list[dict[str, Any]] | None = None,
    buttons: list[dict[str, Any]] | None = None,
) -> str:
    parts = []
    if buttons:
        parts.append(
            f"{BUTTON_PROMPT}\n{json.dumps({'pressed': buttons}, ensure_ascii=False)}"
        )
    if requests:
        parts.append(
            f"{REQUEST_PROMPT}\n{json.dumps({'requests': requests}, ensure_ascii=False)}"
        )
    if reminders:
        parts.append(
            f"{REMIND_PROMPT}\n{json.dumps({'expiring': reminders}, ensure_ascii=False)}"
        )
    if views:
        parts.append(
            f"{EVENT_PROMPT}\n{json.dumps({'events': views}, ensure_ascii=False)}"
        )
    else:
        parts.append(TOOLS_OFF)
    return "\n".join(parts)


def note(text: str, views: list[dict[str, Any]]) -> str:
    return f"{text}\n{json.dumps({'requests': views}, ensure_ascii=False)}"


def request_view(event: Event, task: Task) -> Request:
    approval = task.approval or {}
    return Request(
        str(event.id),
        event.danger or Danger.HIGH,
        event.state == EventState.ESSENCE,
        describe(approval.get("description")),
        approval.get("command"),
    )


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


def talking(session: AgentSession[Any], voiced: SpeechHandle | None) -> bool:
    speech = session.current_speech
    return (
        speech is not None
        and speech is voiced
        and not speech.done()
        and not speech.interrupted
    )


async def _idle(session: AgentSession[Any]) -> bool:
    while True:
        try:
            await session.wait_for_idle()
        except ActivityClosedError:
            return False
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            continue
        return True


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
        sorter: Sorter | None = None,
        sorter_prompt: SorterPrompt | None = None,
        glue_window_s: float = 3.0,
        action_silence_s: float = 0.5,
        event_silence_s: float = EVENT_SILENCE_S,
        rules: Rules | None = None,
        approval_timeout_s: float = APPROVAL_TIMEOUT_S,
        approval_reminder_s: float = APPROVAL_REMINDER_S,
        memory_path: Path | None = None,
    ) -> None:
        assert sorter is None or sorter_prompt is not None
        memory = read_memory(memory_path) if memory_path is not None else None
        text = (
            instructions.text
            if memory is None
            else compose(instructions.text, memory.text)
        )
        super().__init__(instructions=text, chat_ctx=chat_ctx)
        self.loader = loader
        self.current = instructions
        self.log = log
        self.memory_path = memory_path
        if memory is not None:
            self.log.write(
                "memory_loaded", chars=len(memory.text), sha256=memory.sha256
            )
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
        self.retry: set[str] = set()
        self.present = asyncio.Event()
        self.playout: dict[str, bool] = {}
        self.stopped = False
        self.wake = asyncio.Event()
        self.speaker: AgentSession[Any] | None = None
        self.answered: dict[str, str] = {}
        self.waiting: dict[str, str] = {}
        self.noted: set[str] = set()
        self.timers: set[int] = set()
        self.pressed: dict[str, dict[str, Any]] = {}
        self.reading: Reading | None = None
        self.about: dict[str, Place] = {}
        self.asked: dict[str, Place] = {}
        self.announced: set[str] = set()
        self.clarified = 0
        self.rules = rules or Rules()
        self.approval_timeout_s = approval_timeout_s
        self.approval_reminder_s = approval_reminder_s
        self.sorter = sorter
        self.sorter_prompt = sorter_prompt
        self.passed: list[tuple[str, Sorted | DecisionFailure, tuple[str, ...]]] = []
        self.voiced: SpeechHandle | None = None
        self.glue_window_s = glue_window_s
        self.action_silence_s = action_silence_s
        self.event_silence_s = event_silence_s
        self.user_speaking = False
        self.quiet_at = float("-inf")
        self.spoke_at = float("-inf")
        self.final_at = float("-inf")
        self.user_changed = asyncio.Event()
        self.activity = 0
        self.agent_state: str = "initializing"
        self.replied = False
        self.stalled: list[str] = []

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

    def _put(self, key: str) -> None:
        if key in self.queued or key in self.in_tool:
            return
        if self.queue.empty():
            self.replied = self.agent_state in BUSY
        self.queued.add(key)
        self.queue.put_nowait(key)
        self.wake.set()

    def enqueue(self, task: Task) -> None:
        if task.is_open or task.delivered_at is not None:
            return
        self._put(task.tool_call_id)

    def _expiry(self, task: Task) -> None:
        key = task.tool_call_id
        previous, current = self.waiting.get(key), open_request(task)
        if previous is not None and previous != current:
            del self.waiting[key]
            if (
                self.answered.pop(key, None) != previous
                and task.status == TaskStatus.RUNNING
            ):
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
        if current is not None:
            self.waiting[key] = current

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
        self._expiry(task)
        if task.status == TaskStatus.WAITING_APPROVAL:
            self._spawn(self._track_requests())

    async def _track_requests(self) -> None:
        assert self.delegation is not None
        for event in await self.delegation.repo.list_events(open_only=True):
            if event.kind != EventKind.APPROVAL or event.id is None:
                continue
            if event.state == EventState.ARRIVED:
                self._put(f"{APPROVAL_KEY}{event.id}")
            if self.approval_reminder_s > 0 and event.id not in self.timers:
                self.timers.add(event.id)
                self._spawn(self._remind_at(event))

    async def _remind_at(self, event: Event) -> None:
        assert self.delegation is not None
        due = event.created_at + self.approval_timeout_s - self.approval_reminder_s
        await self.delegation.sleep(max(0.0, due - self.delegation.clock()))
        self._put(f"{REMIND_KEY}{event.id}")

    async def enqueue_undelivered(self) -> None:
        if self.delegation is None:
            return
        undelivered = await self.delegation.repo.list_undelivered()
        for task in await self.delegation.repo.list_open():
            current = open_request(task)
            if current is not None:
                self.waiting[task.tool_call_id] = current
        await self._track_requests()
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
            self.deliverer.add_done_callback(self._delivery_done)

    def _delivery_done(self, task: asyncio.Task[None]) -> None:
        if self.stopped:
            return
        if task.cancelled():
            logging.getLogger("voca.tasks").error("delivery loop stopped: cancelled")
            return
        if task.exception() is not None:
            logging.getLogger("voca.tasks").error(
                "delivery loop stopped", exc_info=task.exception()
            )

    async def _ready(self, sent: Callable[[], Awaitable[None]]) -> None:
        while True:
            await self.present.wait()
            await sent()
            if self.present.is_set():
                return

    async def _deliver_loop(
        self, session: AgentSession[Any], sent: Callable[[], Awaitable[None]]
    ) -> None:
        while True:
            await self._ready(sent)
            if self.queue.empty():
                self.wake.clear()
                await self.wake.wait()
                continue
            key = self.queue.get_nowait()
            ok = True
            try:
                ok = await self._speak_events(session, key)
            except Exception:
                logging.getLogger("voca.tasks").exception("delivery failed: %s", key)
            if not ok:
                return

    async def _moment(self, session: AgentSession[Any]) -> bool:
        assert self.delegation is not None
        while True:
            if not await _idle(session):
                return False
            if self.replied:
                return True
            mark = self.activity
            await self.delegation.sleep(self.event_silence_s)
            if self.activity == mark:
                return True

    async def _arrived(self, ids: set[str]) -> tuple[set[str], set[str]]:
        assert self.delegation is not None
        if self.reading is not None and self.reading.handle.done():
            await self._settle_reading(self.reading)
        busy = ids & set(self.reading.ids) if self.reading is not None else set()
        fresh = {
            str(event.id)
            for event in await self.delegation.repo.list_events(open_only=True)
            if str(event.id) in ids - busy and event.state == EventState.ARRIVED
        }
        return fresh, busy

    async def _open_task(self, event: Event | None) -> Task | None:
        assert self.delegation is not None
        if event is None or event.kind != EventKind.APPROVAL:
            return None
        task = await self.delegation.repo.get(event.tool_call_id)
        if (
            task is None
            or open_request(task) != event.request_id
            or self.answered.get(task.tool_call_id) == event.request_id
        ):
            return None
        return task

    async def _speak_events(self, session: AgentSession[Any], first: str) -> bool:
        assert self.delegation is not None
        taken = [first]
        try:
            if not await self._moment(session):
                return False
            while not self.queue.empty():
                taken.append(self.queue.get_nowait())
            tasks: list[Task] = []
            for key in taken:
                if key.startswith((APPROVAL_KEY, REMIND_KEY, BUTTON_KEY)):
                    continue
                task = await self.delegation.repo.get(key)
                if task is None or task.is_open or task.delivered_at is not None:
                    continue
                tasks.append(task)
            opened = await self.delegation.repo.list_events(open_only=True)
            requests = {
                str(event.id): event
                for event in opened
                if event.kind == EventKind.APPROVAL
            }
            events = {
                event.tool_call_id: event
                for event in opened
                if event.kind in (EventKind.RESULT, EventKind.FAILURE)
            }
            now = self.delegation.clock()
            asked: list[dict[str, Any]] = []
            expiring: list[dict[str, Any]] = []
            pressed: list[dict[str, Any]] = []
            spoken: list[str] = []
            for key in taken:
                if key.startswith(BUTTON_KEY):
                    view = self.pressed.pop(key, None)
                    if view is not None:
                        pressed.append(view)
                    continue
                if key.startswith(APPROVAL_KEY):
                    event = requests.get(key.removeprefix(APPROVAL_KEY))
                    if event is None or event.state != EventState.ARRIVED:
                        continue
                    found = asked
                elif key.startswith(REMIND_KEY):
                    event = requests.get(key.removeprefix(REMIND_KEY))
                    if (
                        event is None
                        or now >= event.created_at + self.approval_timeout_s
                    ):
                        continue
                    found = expiring
                else:
                    continue
                task = await self._open_task(event)
                if task is None:
                    continue
                found.append(notification_view(event, task, now))
                spoken.append(key)
            if asked:
                fresh, busy = await self._arrived({view["id"] for view in asked})
                self.retry.update(f"{APPROVAL_KEY}{id}" for id in busy)
                asked = [view for view in asked if view["id"] in fresh]
                spoken = [
                    key
                    for key in spoken
                    if not key.startswith(APPROVAL_KEY)
                    or key.removeprefix(APPROVAL_KEY) in fresh
                ]
            if not (tasks or asked or expiring or pressed):
                return True
            keys = [task.tool_call_id for task in tasks]
            spoken.extend(keys)
            views = [
                notification_view(events[task.tool_call_id], task, now)
                if task.tool_call_id in events
                else task_view(task)
                for task in tasks
            ]
            for view in asked:
                if view["id"] in self.noted:
                    continue
                await self._fact(
                    "list_notifications",
                    {},
                    {
                        "id": view["id"],
                        "event": "approval_mentioned",
                        "request": view.get("request"),
                        "command": view.get("command"),
                        "danger": view["danger"],
                    },
                )
                self.noted.add(view["id"])
            for task in tasks:
                await self._remember(task, [])
            try:
                handle = session.generate_reply(
                    instructions=events_prompt(
                        views, requests=asked, reminders=expiring, buttons=pressed
                    ),
                    tool_choice="none",
                    allow_interruptions=True,
                )
            except RuntimeError:
                return False
            await handle.wait_for_playout()
            self.announced.update(
                item.id
                for item in handle.chat_items
                if item.type == "message" and item.role == "assistant"
            )
            if handle.exception() is not None:
                logging.getLogger("voca.tasks").warning(
                    "event reply failed: %s", spoken, exc_info=handle.exception()
                )
                for task in tasks:
                    self._task_row(task, interrupted=handle.interrupted)
                self.stalled.extend(spoken)
                return True
            if heard(handle):
                for key in keys:
                    self.playout[key] = False
                    await self.delegation.mark_delivered(key)
                for view in asked:
                    if not await self.delegation.repo.advance_event(
                        int(view["id"]), EventState.MENTIONED, now
                    ):
                        continue
                    self.log.write(
                        "approval_event",
                        event_id=view["id"],
                        tool_call_id=view["task"],
                        state=EventState.MENTIONED.value,
                    )
                return True
            for task in tasks:
                self._task_row(task, interrupted=handle.interrupted)
            if not await _idle(session):
                return False
            if handle.interrupted:
                self.replied = False
            else:
                await self.delegation.sleep(UNHEARD_RETRY_S)
                self.replied = True
            self.retry.update(spoken)
            return True
        finally:
            self._settle(taken)

    def _settle(self, taken: list[str]) -> None:
        for key in taken:
            if key in self.retry:
                self.retry.discard(key)
                self.queue.put_nowait(key)
            elif key in self.stalled:
                continue
            else:
                self.queued.discard(key)

    async def _denied(self) -> set[str]:
        if self.delegation is None:
            return set()
        return {
            event.tool_call_id
            for event in await self.delegation.repo.list_events(open_only=False)
            if event.kind == EventKind.APPROVAL and event.closed_reason == DENIED
        }

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
                    output=result_outcome(
                        task, task.tool_call_id in await self._denied()
                    ),
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
        self.answered[key] = request_id
        event = "approved" if choice == "once" else "denied"
        output: dict[str, Any] = {"id": short_id(key), "event": event, "by": "button"}
        if choice == "deny":
            output["note"] = DENIED_NOTE
        await self._fact("list_tasks", {}, output)
        self._task_row(task, approval=choice, by="button")
        self.pressed[f"{BUTTON_KEY}{key}"] = {
            "task": short_id(key),
            "event": event,
            "request": describe(task.approval.get("description")),
            "command": task.approval.get("command"),
        }
        self._put(f"{BUTTON_KEY}{key}")
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
        view = task_view(final)
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

    async def refresh_instructions(self) -> bool:
        text = self.current.text
        if self.memory_path is not None:
            memory = read_memory(self.memory_path)
            self.log.write(
                "memory_loaded", chars=len(memory.text), sha256=memory.sha256
            )
            text = compose(text, memory.text)
        if text == self.instructions:
            return False
        await self.update_instructions(text)
        return True

    def cancel_compaction(self) -> None:
        if self.pending is not None:
            self.pending.task.cancel()
        self.pending = None

    def on_agent_state(self, ev: AgentStateChangedEvent) -> None:
        self.activity += 1
        self.agent_state = ev.new_state
        if ev.new_state in BUSY:
            self.replied = True
        if ev.new_state == "thinking":
            self._unstall()
        if ev.new_state == "speaking":
            self.voiced = self.session.current_speech

    def on_user_state(self, ev: UserStateChangedEvent) -> None:
        self.activity += 1
        if ev.new_state == "speaking":
            self.user_speaking = True
            self.spoke_at = ev.created_at
            self._unstall()
        elif ev.old_state == "speaking":
            self.user_speaking = False
            self.quiet_at = ev.created_at
        self.user_changed.set()
        self.user_changed = asyncio.Event()

    def on_user_transcribed(self, ev: UserInputTranscribedEvent) -> None:
        if not ev.is_final:
            return
        self.activity += 1
        self.final_at = ev.created_at
        self.user_changed.set()
        self.user_changed = asyncio.Event()

    def _unstall(self) -> None:
        for key in self.stalled:
            self.queue.put_nowait(key)
        self.stalled.clear()
        self.wake.set()

    async def _hold(self, handle: SpeechHandle) -> bool:
        while not handle.interrupted:
            now = self.clock()
            timeout: float | None = None
            if self.user_speaking:
                pass
            elif self.final_at < self.spoke_at and now < self.quiet_at + FINAL_WAIT_S:
                timeout = self.quiet_at + FINAL_WAIT_S - now
            else:
                since = max(self.quiet_at, self.final_at)
                timeout = self.action_silence_s - (now - since)
                if timeout <= 0:
                    return True
            fut = asyncio.ensure_future(
                asyncio.wait_for(self.user_changed.wait(), timeout=timeout)
            )
            try:
                await handle.wait_if_not_interrupted([fut])
            except TimeoutError:
                pass
            finally:
                if not fut.done():
                    fut.cancel()
        return False

    async def _sort(
        self,
        text: str,
        branch: str,
        *,
        speaking: bool,
        requests: list[tuple[Event, Task]],
    ) -> tuple[bool, Sorted | DecisionFailure]:
        assert self.sorter is not None and self.sorter_prompt is not None
        agent_last = ""
        for item in reversed(self.chat_ctx.items):
            if item.type == "message" and item.role == "assistant":
                agent_last = item.text_content or ""
                break
        described = [
            (
                describe((task.approval or {}).get("description")),
                (task.approval or {}).get("command"),
            )
            for _, task in requests
        ]
        result = await self.sorter.sort(
            sorter_state(agent_last, described, text), len(requests)
        )
        dropped = drop(text, result, self.sorter_prompt, speaking=speaking)
        if isinstance(result, Sorted):
            answers: dict[str, object] = {}
            if result.relation is not None:
                answers["relation"] = result.relation
            if result.answer is not None:
                answers["answer"] = result.answer
            if len(requests) >= 2:
                answers["target"] = result.target
            self.log.write(
                "sorter",
                branch=branch,
                text=text,
                speaking=speaking,
                dropped=dropped,
                ms=result.ms,
                choice=result.kind,
                confidence=result.confidence,
                **answers,
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
        return dropped, result

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
            pairs = await self._requests()
            dropped, result = await self._sort(
                text, "stt", speaking=True, requests=pairs
            )
            if dropped:
                self.final_at = self.clock()
                self.user_changed.set()
                self.user_changed = asyncio.Event()
                continue
            self.passed.append((text, result, tuple(str(e.id) for e, _ in pairs)))
            yield event

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> AsyncGenerator[stt.SpeechEvent | str]:
        async for event in self._screen(
            Agent.default.stt_node(self, audio, model_settings),
            lambda: talking(self.session, self.voiced),
        ):
            yield event

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        glued = glue(
            entries(self.chat_ctx),
            started_at=new_message.metrics.get("started_speaking_at"),
            window_s=self.glue_window_s,
        )
        pairs = await self._requests() if self.delegation is not None else []
        ids = tuple(str(e.id) for e, _ in pairs)
        cached = None
        if self.sorter is not None:
            text = new_message.text_content or ""
            passed, self.passed = self.passed, []
            cached = next((p for p in passed if p[0] == text), None)
            if (
                glued is None
                and cached is None
                and text != " ".join(p[0] for p in passed)
            ):
                dropped, result = await self._sort(
                    text, "hook", speaking=False, requests=pairs
                )
                if dropped:
                    raise StopResponse()
                cached = (text, result, ids)
        changed = False
        notes = Notes(None, False)
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
        if self.reading is not None and self.reading.handle.done():
            await self._settle_reading(self.reading)
        pairs = await self._requests()
        if pairs:
            notes = await self._judge(new_message, pairs, cached)
            changed = changed or notes.changed
        reload = False
        loaded = self.loader.load()
        if (loaded.sha256, loaded.source, loaded.error) != (
            self.current.sha256,
            self.current.source,
            self.current.error,
        ):
            if loaded.sha256 != self.current.sha256:
                reload = True
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
            reload = True
        if reload and await self.refresh_instructions():
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
        if notes.text:
            turn_ctx.add_message(role="system", content=[notes.text])

    async def _requests(self) -> list[tuple[Event, Task]]:
        if self.delegation is None:
            return []
        found: list[tuple[Event, Task]] = []
        for event in await self.delegation.repo.list_events(open_only=True):
            task = await self._open_task(event)
            if task is not None:
                found.append((event, task))
        return found

    async def _read(self, reading: Reading) -> None:
        await reading.handle.wait_for_playout()
        await self._settle_reading(reading)

    async def _settle_reading(self, reading: Reading) -> None:
        if self.reading is not reading:
            return
        self.reading = None
        assert self.delegation is not None
        items = reading.handle.chat_items
        start = 0
        for index, item in enumerate(items):
            if item.type == "function_call_output" and item.name == "open_approval":
                start = index + 1
        messages = [
            item
            for item in items[start:]
            if item.type == "message" and item.role == "assistant"
        ]
        played = " ".join(item.text_content or "" for item in messages).strip()
        interrupted = reading.handle.interrupted
        spoken = essence_spoken(played, interrupted)
        self.log.write(
            "approval_read",
            event_ids=reading.ids,
            played=played,
            interrupted=interrupted,
            essence=spoken,
        )
        if not spoken:
            return
        now = self.delegation.clock()
        changed: list[str] = []
        for id in reading.ids:
            await self.delegation.repo.advance_event(int(id), EventState.MENTIONED, now)
            if await self.delegation.repo.advance_event(
                int(id), EventState.ESSENCE, now
            ):
                changed.append(id)
        keys = {
            str(event.id): event.tool_call_id
            for event in await self.delegation.repo.list_events(open_only=True)
        }
        for id in changed:
            self.log.write(
                "approval_event",
                event_id=id,
                tool_call_id=keys.get(id),
                state=EventState.ESSENCE.value,
            )
        if messages:
            self.about[messages[-1].id] = Place(tuple(reading.ids), "essence")

    async def _quiet(self) -> bool:
        mark = self.spoke_at
        while True:
            if self.user_speaking or self.spoke_at > mark:
                return False
            since = max(self.quiet_at, self.final_at)
            timeout = self.action_silence_s - (self.clock() - since)
            if timeout <= 0:
                return True
            try:
                await asyncio.wait_for(self.user_changed.wait(), timeout=timeout)
            except TimeoutError:
                pass

    def _place(self) -> Place | None:
        messages = [
            item
            for item in self.chat_ctx.items
            if item.type == "message" and item.role in ("user", "assistant")
        ]
        if (
            not messages
            or messages[-1].role != "assistant"
            or messages[-1].id in self.announced
        ):
            return None
        if messages[-1].id in self.about:
            return self.about[messages[-1].id]
        for item in reversed(messages):
            if item.role == "user":
                return self.asked.get(item.id)
        return None

    async def _judge(
        self,
        message: llm.ChatMessage,
        pairs: list[tuple[Event, Task]],
        cached: tuple[str, Sorted | DecisionFailure, tuple[str, ...]] | None,
    ) -> Notes:
        assert self.delegation is not None
        text = message.text_content or ""
        ids = tuple(str(e.id) for e, _ in pairs)
        result: Sorted | DecisionFailure | None = None
        if self.sorter is None:
            result = None
        elif cached is not None and cached[0] == text and cached[2] == ids:
            result = cached[1]
        else:
            result = (await self._sort(text, "judge", speaking=False, requests=pairs))[
                1
            ]
        verdict = judge(
            text,
            [request_view(e, t) for e, t in pairs],
            result,
            self._place(),
            self.rules,
            self.clarified,
        )
        self._judged(text, ids, verdict, verdict.outcome)
        self.clarified = self.clarified + 1 if verdict.outcome in CLARIFYING else 0
        now = self.delegation.clock()
        views = [
            notification_view(e, t, now) for e, t in pairs if str(e.id) in verdict.ids
        ]
        if verdict.outcome in ("approve", "deny"):
            event, task = next(p for p in pairs if str(p[0].id) == verdict.ids[0])
            if not await self._quiet():
                self._judged(text, ids, verdict, "resumed")
                return Notes(None, False)
            await self._decide(
                event, task, "once" if verdict.outcome == "approve" else "deny"
            )
            return Notes(None, True)
        if verdict.outcome in PLACES:
            self.asked[message.id] = Place(verdict.ids, PLACES[verdict.outcome])
        if verdict.outcome == "pass" and not verdict.ids:
            return Notes(None, False)
        return Notes(note(NOTES[verdict.outcome], views), False)

    def _judged(
        self, text: str, ids: tuple[str, ...], verdict: Judgement, outcome: str
    ) -> None:
        self.log.write(
            "approval_judge",
            text=text,
            requests=list(ids),
            outcome=outcome,
            event_ids=list(verdict.ids),
            binding=verdict.binding,
            confidence=verdict.confidence,
            threshold=verdict.threshold,
            clarified=self.clarified,
        )

    async def _decide(self, event: Event, task: Task, choice: Choice) -> None:
        assert self.delegation is not None
        key = task.tool_call_id
        approval = task.approval or {}
        try:
            await self.delegation.approve(key, choice, expected=task.approval)
        except (ApprovalNotActive, TaskNotFound):
            await self._approval_failed(key, APPROVAL_GONE)
            return
        except DelegationError:
            await self._approval_failed(key, APPROVAL_FAILED)
            return
        self.answered[key] = event.request_id
        output: dict[str, Any] = {
            "id": short_id(key),
            "event": "approved" if choice == "once" else "denied",
            "by": "voice",
            "request": describe(approval.get("description")),
        }
        if choice == "deny":
            output["note"] = DENIED_NOTE
        await self._fact("list_tasks", {}, output)
        self._task_row(task, approval=choice, by="voice")

    async def _approval_failed(self, key: str, error: str) -> None:
        await self._fact(
            "list_tasks",
            {},
            {
                "id": short_id(key),
                "event": "approval_failed",
                "by": "voice",
                "error": error,
            },
            is_error=True,
        )

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
        denied = await self._denied()
        tasks = [task_view(task, task.tool_call_id in denied) for task in found]
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
        return json.dumps(task_view(final), ensure_ascii=False)

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
    async def open_approval(
        self, context: RunContext[Any], id: str | None = None
    ) -> str:
        """Зачитать запрос Hermes на разрешение действия: возвращает полный текст запроса. id — из list_notifications; можно не указывать, если запрос один. Первой фразой назови действие и предмет, потом подробности, и спроси, разрешить ли. Разрешить или отклонить запрос ты не можешь."""
        if self.delegation is None:
            raise ToolError(DELEGATE_UNAVAILABLE)
        pairs = await self._requests()
        if not pairs:
            raise ToolError(NO_REQUEST)
        if id:
            pairs = [pair for pair in pairs if str(pair[0].id) == id]
            if not pairs:
                raise ToolError(REQUEST_UNKNOWN)
        elif len(pairs) > 1:
            raise ToolError(REQUEST_AMBIGUOUS)
        event, task = pairs[0]
        picked = str(event.id)
        if self.reading is not None and self.reading.handle is context.speech_handle:
            self.reading.ids.append(picked)
        else:
            if self.reading is not None:
                await self._settle_reading(self.reading)
            self.reading = Reading([picked], context.speech_handle)
            self._spawn(self._read(self.reading))
        return json.dumps(
            notification_view(event, task, self.delegation.clock()), ensure_ascii=False
        )

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
