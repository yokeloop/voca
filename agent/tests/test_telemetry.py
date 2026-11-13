import json
from pathlib import Path

import pytest
from livekit.agents import APIConnectionError
from livekit.agents.llm import ChatMessage, LLMError
from livekit.agents.metrics import LLMMetrics
from livekit.agents.voice import ErrorEvent, UserStateChangedEvent

from voca.speech.prepare import Prepared
from voca.telemetry.jsonl import Fragments, JsonlLog, TurnTracker, turn_row


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


@pytest.mark.parametrize("cold", [True, False])
def test_turn_row(cold: bool) -> None:
    user = ChatMessage(
        role="user",
        content=["Сколько времени?"],
        metrics={"end_of_turn_delay": 0.6, "transcription_delay": 0.3},
    )
    assistant = ChatMessage(
        role="assistant",
        content=["**Сейчас** 5 минут."],
        interrupted=True,
        metrics={
            "e2e_latency": 2.1,
            "llm_node_ttft": 0.8,
            "tts_node_ttfb": 0.4,
            "started_speaking_at": 100.0,
            "stopped_speaking_at": 104.5,
        },
    )
    fragments = Fragments()
    fragments.add("Сейчас 5 минут.", Prepared("Сейчас пять минут.", 0), 12.5)
    row = turn_row(
        user=user,
        assistant=assistant,
        llm=llm_metrics(),
        fragments=fragments,
        cold=cold,
    )
    assert row["e2e_latency_ms"] == pytest.approx(2100)
    assert row["first_audio_ms"] == pytest.approx(2100)
    assert row["end_of_turn_delay_ms"] == pytest.approx(600)
    assert row["transcription_delay_ms"] == pytest.approx(300)
    assert row["stt_ms"] == pytest.approx(300)
    assert row["llm_ttft_ms"] == pytest.approx(800)
    assert row["tts_ttfb_ms"] == pytest.approx(400)
    assert row["first_sentence_ms"] is None
    assert row["tokens_in"] == 900
    assert row["ctx_size"] == 900
    assert row["cached_tokens"] == 850
    assert row["cache_hit"] is True
    assert row["interrupted"] is True
    assert row["started_speaking_at"] == 100.0
    assert row["stopped_speaking_at"] == 104.5
    assert row["markdown_removed"] == 2
    assert row["leftover_digits"] == 0
    assert row["first_fragment"] == "Сейчас пять минут."
    assert row["first_fragment_chars"] == len("Сейчас 5 минут.")
    assert row["prepare_ms"] == 12.5
    assert row["user_text"] == "Сколько времени?"
    assert row["text"] == "**Сейчас** 5 минут."
    assert row["cold"] is cold


def test_turn_row_without_metrics() -> None:
    row = turn_row(
        user=None,
        assistant=ChatMessage(role="assistant", content=["Да."]),
        llm=None,
        fragments=Fragments(),
        cold=False,
    )
    assert row["e2e_latency_ms"] is None
    assert row["end_of_turn_delay_ms"] is None
    assert row["tokens_in"] is None
    assert row["cache_hit"] is None
    assert row["first_fragment"] is None
    assert row["user_text"] is None
    assert row["started_speaking_at"] is None
    assert row["stopped_speaking_at"] is None


def test_tracker_user_state(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = JsonlLog(path, session="job-1")
    TurnTracker(log).on_user_state(
        UserStateChangedEvent(old_state="listening", new_state="speaking")
    )
    log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["kind"] == "user_state"
    assert row["old"] == "listening"
    assert row["new"] == "speaking"


def test_tracker_error(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = JsonlLog(path, session="job-1")
    error = LLMError(
        timestamp=0.0, label="llm", error=APIConnectionError(), recoverable=False
    )
    TurnTracker(log).on_error(ErrorEvent(error=error, source=None))
    log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["kind"] == "error"
    assert row["component"] == "llm"
    assert row["recoverable"] is False
    assert row["message"]


def test_jsonl_log(tmp_path: Path) -> None:
    path = tmp_path / "state" / "events.jsonl"
    log = JsonlLog(path, session="job-1")
    log.write("agent_state", old="listening", new="thinking")
    log.turn = 1
    log.write("user_input_transcribed", text="Привет")
    log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    for row in rows:
        assert {"ts", "session", "turn", "kind"} <= row.keys()
        assert row["session"] == "job-1"
    assert rows[0]["turn"] == 0
    assert rows[1]["turn"] == 1
    assert rows[1]["text"] == "Привет"
