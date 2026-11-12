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
