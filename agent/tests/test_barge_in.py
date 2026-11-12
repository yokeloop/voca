import importlib.util
import math
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "bench" / "barge_in.py"
    spec = importlib.util.spec_from_file_location("barge_in", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["barge_in"] = module
    spec.loader.exec_module(module)
    return module


barge_in = load()
RATE = 48000
SECONDS = 10.0


def noise(rng: random.Random, amplitude: int) -> int:
    return rng.randint(-amplitude, amplitude)


def monitor_signal(end_s: float) -> list[int]:
    rng = random.Random(1)
    samples = [0] * int(RATE * SECONDS)
    start = 0.5
    while start < end_s:
        stop = min(start + 0.300, end_s)
        for i in range(round(start * RATE), round(stop * RATE)):
            samples[i] = noise(rng, 8000)
        start += 0.380
    return samples


def mic_signal(monitor: list[int]) -> list[int]:
    rng = random.Random(2)
    delay = round(0.120 * RATE)
    gain = 10 ** (-20 / 20)
    samples = [0] * len(monitor)
    for i in range(delay, len(monitor)):
        samples[i] = round(monitor[i - delay] * gain)
    for i in range(round(5.300 * RATE), len(samples)):
        samples[i] = max(-32768, min(32767, samples[i] + noise(rng, 6000)))
    return samples


def envelopes(monitor_end_s: float) -> tuple[list[float], list[float]]:
    monitor = monitor_signal(monitor_end_s)
    mic = mic_signal(monitor)
    return barge_in.envelope_db(mic, RATE), barge_in.envelope_db(monitor, RATE)


def test_envelope_db() -> None:
    sine = [round(16384 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(RATE)]
    levels = barge_in.envelope_db(sine, RATE)
    assert len(levels) == 100
    assert all(level == pytest.approx(-9.03, abs=0.5) for level in levels)
    assert barge_in.envelope_db([0] * 960, RATE) == [-120.0, -120.0]


def test_measure_synthetic() -> None:
    mic, monitor = envelopes(6.000)
    repeat = barge_in.measure(mic, monitor, 590, 5.900, 0.5)
    assert repeat.valid
    assert repeat.pause_ts == 5.900
    assert repeat.onset_s == pytest.approx(5.300, abs=0.010)
    assert repeat.stop_s == pytest.approx(6.000, abs=0.010)
    assert repeat.lag_ms == pytest.approx(120, abs=10)
    assert repeat.delay_ms == pytest.approx(820, abs=10)


def test_measure_silent_monitor() -> None:
    mic, monitor = envelopes(5.000)
    repeat = barge_in.measure(mic, monitor, 590, 5.900, 0.5)
    assert not repeat.valid
    assert repeat.note == "monitor молчал до onset"


def row(
    ts: float, kind: str, session: str = "s1", **fields: object
) -> dict[str, object]:
    return {"ts": ts, "session": session, "kind": kind, **fields}


def state(ts: float, old: str, new: str, session: str = "s1") -> dict[str, object]:
    return row(ts, "agent_state", session, old=old, new=new)


def test_pause_moments() -> None:
    rows = [
        state(1.0, "thinking", "speaking"),
        row(3.99, "turn", interrupted=False),
        state(4.0, "speaking", "listening"),
        state(5.0, "thinking", "speaking"),
        state(6.0, "speaking", "listening"),
        row(6.5, "turn", interrupted=True),
    ]
    pauses = barge_in.pause_moments(rows)
    assert [pause["ts"] for pause in pauses] == [6.0]


def test_false_counts_speech() -> None:
    rows = [
        row(0.5, "user_input_transcribed", text="до"),
        state(1.0, "thinking", "speaking"),
        row(2.0, "user_input_transcribed", text="эхо"),
        row(2.5, "false_interruption", resumed=True),
        state(3.0, "speaking", "listening"),
        row(3.5, "turn", interrupted=True),
        row(4.0, "false_interruption", resumed=True),
        state(10.0, "thinking", "speaking", session="s2"),
        row(13.0, "turn", session="s2", interrupted=False),
        state(13.01, "speaking", "listening", session="s2"),
    ]
    counts = barge_in.false_counts(rows, "speech")
    assert counts["speaking_s"] == pytest.approx(5.01)
    assert counts["counts"] == {
        "false_interruption": 2,
        "pause": 1,
        "interrupted_turn": 1,
        "transcribed_while_speaking": 1,
    }
    assert counts["sessions"]["s2"]["speaking_s"] == pytest.approx(3.01)
    assert counts["sessions"]["s2"]["counts"]["pause"] == 0
    assert counts["sessions"]["s1"]["counts"]["pause"] == 1
    assert counts["clean"] is False
    clean = barge_in.false_counts(rows[7:], "speech")
    assert clean["clean"] is True


def test_false_counts_silence() -> None:
    rows = [
        row(1.0, "user_state", old="listening", new="speaking"),
        row(1.5, "user_state", old="speaking", new="listening"),
    ]
    quiet = barge_in.false_counts(rows, "silence")
    assert quiet["counts"] == {"user_input_transcribed": 0, "turn": 0, "thinking": 0}
    assert quiet["vad_speaking"] == 1
    assert quiet["clean"] is True
    rows += [
        row(2.0, "user_input_transcribed", text="шум"),
        state(2.1, "listening", "thinking"),
        row(3.0, "turn", interrupted=False),
    ]
    noisy = barge_in.false_counts(rows, "silence")
    assert noisy["counts"] == {"user_input_transcribed": 1, "turn": 1, "thinking": 1}
    assert noisy["clean"] is False


def test_tail_match() -> None:
    history = "Поезд прибывает в 5 часов, а затем"
    ok, extra_history, extra_heard = barge_in.tail_match(
        history, "поезд прибывает в пять часов а"
    )
    assert ok
    assert extra_history == ["затем"]
    assert extra_heard == []
    ok, extra_history, _ = barge_in.tail_match(history, "поезд прибывает")
    assert not ok
    assert len(extra_history) == 5
