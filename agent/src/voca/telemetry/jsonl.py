import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livekit.agents.llm import ChatMessage
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import (
    AgentFalseInterruptionEvent,
    AgentSession,
    AgentStateChangedEvent,
    CloseEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    MetricsCollectedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)

from voca.clientio.cards import component_of
from voca.speech.prepare import Prepared

MARKDOWN = re.compile(
    r"\*\*|__|`|^#{1,6}\s|^\s*[-*]\s|^\s*\d+\.\s|^\s*\||\$|\\\(|\\\[|\\frac",
    re.MULTILINE,
)
METRIC_FIELDS = (
    "ttft",
    "prompt_tokens",
    "prompt_cached_tokens",
    "ttfb",
    "characters_count",
    "end_of_utterance_delay",
    "transcription_delay",
    "duration",
    "speech_id",
)


class JsonlLog:
    def __init__(self, path: Path, session: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8", buffering=1)
        self.session = session
        self.device = ""
        self.turn = 0

    def write(self, kind: str, **fields: object) -> None:
        row = {
            "ts": time.time(),
            "session": self.session,
            "turn": self.turn,
            "device": self.device,
            "kind": kind,
            **fields,
        }
        self.file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self.file.close()


def markdown_markers(text: str) -> int:
    return len(MARKDOWN.findall(text))


@dataclass
class Fragments:
    first_text: str | None = None
    first_prepared: str | None = None
    first_prepare_ms: float | None = None
    leftover_digits: int = 0

    def add(self, text: str, prepared: Prepared, prepare_ms: float) -> None:
        if self.first_text is None:
            self.first_text = text
            self.first_prepared = prepared.text
            self.first_prepare_ms = prepare_ms
        self.leftover_digits += prepared.leftover_digits


def ms(seconds: float | None) -> float | None:
    return None if seconds is None else seconds * 1000


def turn_row(
    *,
    user: ChatMessage | None,
    assistant: ChatMessage,
    llm: LLMMetrics | None,
    fragments: Fragments,
    cold: bool,
) -> dict[str, object]:
    user_metrics = user.metrics if user is not None else {}
    reply = assistant.metrics
    transcription = ms(user_metrics.get("transcription_delay"))
    e2e = ms(reply.get("e2e_latency"))
    text = assistant.text_content or ""
    return {
        "user_text": user.text_content if user is not None else None,
        "end_of_turn_delay_ms": ms(user_metrics.get("end_of_turn_delay")),
        "transcription_delay_ms": transcription,
        "stt_ms": transcription,
        "e2e_latency_ms": e2e,
        "first_audio_ms": e2e,
        "llm_ttft_ms": ms(reply.get("llm_node_ttft")),
        "first_sentence_ms": ms(reply.get("llm_node_ttfs")),
        "tts_ttfb_ms": ms(reply.get("tts_node_ttfb")),
        "tokens_in": llm.prompt_tokens if llm is not None else None,
        "ctx_size": llm.prompt_tokens if llm is not None else None,
        "cached_tokens": llm.prompt_cached_tokens if llm is not None else None,
        "cache_hit": llm.prompt_cached_tokens > 0 if llm is not None else None,
        "first_fragment": fragments.first_prepared,
        "first_fragment_chars": (
            len(fragments.first_text) if fragments.first_text is not None else None
        ),
        "prepare_ms": fragments.first_prepare_ms,
        "leftover_digits": fragments.leftover_digits,
        "text": text,
        "interrupted": assistant.interrupted,
        "started_speaking_at": reply.get("started_speaking_at"),
        "stopped_speaking_at": reply.get("stopped_speaking_at"),
        "markdown_removed": markdown_markers(text),
        "cold": cold,
    }


class TurnTracker:
    def __init__(self, log: JsonlLog) -> None:
        self.log = log
        self.fragments = Fragments()
        self.llm: LLMMetrics | None = None
        self.user: ChatMessage | None = None
        self.cold = True

    def attach(self, session: AgentSession[Any]) -> None:
        session.on("user_input_transcribed", self.on_transcribed)
        session.on("conversation_item_added", self.on_item)
        session.on("agent_state_changed", self.on_state)
        session.on("user_state_changed", self.on_user_state)
        session.on("agent_false_interruption", self.on_false_interruption)
        session.on("metrics_collected", self.on_metrics)
        session.on("error", self.on_error)
        session.on("close", self.on_close)

    def on_prepared(self, text: str, prepared: Prepared, prepare_ms: float) -> None:
        self.fragments.add(text, prepared, prepare_ms)

    def on_transcribed(self, ev: UserInputTranscribedEvent) -> None:
        if ev.is_final:
            self.log.write("user_input_transcribed", text=ev.transcript)

    def on_item(self, ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, ChatMessage):
            return
        if item.role == "user":
            self.log.turn += 1
            self.user = item
        elif item.role == "assistant":
            self.log.write(
                "turn",
                **turn_row(
                    user=self.user,
                    assistant=item,
                    llm=self.llm,
                    fragments=self.fragments,
                    cold=self.cold,
                ),
            )
            self.cold = False
            if item.interrupted:
                self.log.write("interruption")
            self.fragments = Fragments()
            self.llm = None

    def on_state(self, ev: AgentStateChangedEvent) -> None:
        self.log.write("agent_state", old=ev.old_state, new=ev.new_state)

    def on_user_state(self, ev: UserStateChangedEvent) -> None:
        self.log.write("user_state", old=ev.old_state, new=ev.new_state)

    def on_false_interruption(self, ev: AgentFalseInterruptionEvent) -> None:
        self.log.write("false_interruption", resumed=ev.resumed)

    def on_metrics(self, ev: MetricsCollectedEvent) -> None:
        metrics = ev.metrics
        if isinstance(metrics, LLMMetrics):
            self.llm = metrics
        if isinstance(metrics, STTMetrics | LLMMetrics | TTSMetrics | EOUMetrics):
            fields = {
                name: getattr(metrics, name)
                for name in METRIC_FIELDS
                if name in type(metrics).model_fields
            }
            self.log.write("metrics", type=metrics.type, **fields)

    def on_error(self, ev: ErrorEvent) -> None:
        self.log.write(
            "error",
            component=component_of(ev.error),
            recoverable=ev.error.recoverable,
            message=str(ev.error.error),
        )

    def on_close(self, ev: CloseEvent) -> None:
        self.log.write("close", reason=str(ev.reason))
