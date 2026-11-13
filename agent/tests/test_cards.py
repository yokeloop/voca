import asyncio
import json
from collections.abc import Callable
from typing import Any

from livekit.agents import APIConnectionError
from livekit.agents.llm import LLMError
from livekit.agents.metrics import LLMMetrics
from livekit.agents.stt import STTError
from livekit.agents.tts import TTSError
from livekit.agents.voice import ErrorEvent, MetricsCollectedEvent

from voca.clientio.cards import CardState, component_of
from voca.conversation.instructions import InstructionSet

INSTRUCTIONS = InstructionSet("text", "a" * 64, "fresh", None)


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


class FakeParticipant:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, text: str, *, topic: str) -> None:
        self.sent.append((text, topic))


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.local_participant = FakeParticipant()

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers[event] = handler


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
    async def main() -> list[tuple[str, str]]:
        room = FakeRoom()
        cards = CardState(INSTRUCTIONS)
        cards.bind(room)  # type: ignore[arg-type]
        cards.on_error(error_event(llm_error(False)))
        room.handlers["participant_connected"](object())
        assert cards.sending is not None
        await cards.sending
        cards.set_instructions(INSTRUCTIONS)
        await cards.sending
        return room.local_participant.sent

    sent = asyncio.run(main())
    assert len(sent) == 2
    assert all(topic == "voca.cards" for _, topic in sent)
    assert json.loads(sent[0][0])["components"]["llm"] == "down"
    assert json.loads(sent[1][0])["components"]["llm"] == "down"
