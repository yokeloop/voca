import importlib.util
import json
import statistics
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "bench" / "report.py"
    spec = importlib.util.spec_from_file_location("report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["report"] = module
    spec.loader.exec_module(module)
    return module


report = load()


def turn(device: str, cold: bool, e2e: float | None, **fields: Any) -> dict[str, Any]:
    return {
        "ts": fields.pop("ts", 0.0),
        "session": fields.pop("session", "s1"),
        "device": device,
        "kind": "turn",
        "variant": "base",
        "cold": cold,
        "e2e_latency_ms": e2e,
        "end_of_turn_delay_ms": None,
        "transcription_delay_ms": None,
        "llm_ttft_ms": None,
        "first_sentence_ms": None,
        "tts_ttfb_ms": None,
        "prepare_ms": None,
        "markdown_removed": 0,
        "leftover_digits": 0,
        "interrupted": False,
        "text": "",
        **fields,
    }


def test_percentile() -> None:
    assert report.percentile([100, 200, 300, 400], 0.5) == pytest.approx(250)
    assert report.percentile([100, 200, 300, 400], 0.95) == pytest.approx(385)
    assert report.percentile([5], 0.5) == 5
    assert report.percentile([5], 0.95) == 5
    assert report.percentile([], 0.5) is None


def test_variant_label() -> None:
    assert report.variant_label({}) == "base"
    assert report.variant_label({"VOCA_ENDPOINTING_MIN_DELAY": "0.3"}) == (
        "min_delay=0.3"
    )
    both = {
        "VOCA_PREEMPTIVE_GENERATION": "true",
        "VOCA_ENDPOINTING_MIN_DELAY": "0.3",
    }
    assert report.variant_label(both) == "min_delay=0.3,preemptive=true"


def test_load_rows_window(tmp_path: Path) -> None:
    rows = [
        {"ts": 1.0, "kind": "turn"},
        {"ts": 3.0, "kind": "turn"},
        {"ts": 2.0, "kind": "agent_state"},
        {"ts": 2.5, "kind": "turn"},
        {"ts": 5.0, "kind": "turn"},
    ]
    path = tmp_path / "events.jsonl"
    lines = [json.dumps(row) for row in rows]
    lines.insert(2, "")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    window = report.load_rows(path, 2.0, 3.0)
    assert [row["ts"] for row in window] == [3.0, 2.0, 2.5]
    assert [row["ts"] for row in report.turn_rows(window)] == [2.5, 3.0]
    assert len(report.load_rows(path)) == 5


def test_summarize() -> None:
    turns = [
        turn("host", True, 3000.0, text="а" * 10),
        turn(
            "host",
            False,
            1000.0,
            text="б" * 20,
            end_of_turn_delay_ms=500.0,
            llm_ttft_ms=800.0,
            tts_ttfb_ms=600.0,
            markdown_removed=2,
            leftover_digits=1,
        ),
        turn(
            "host",
            False,
            2000.0,
            text="в" * 30,
            end_of_turn_delay_ms=400.0,
            llm_ttft_ms=700.0,
        ),
        turn("host", False, None, text="г" * 40, interrupted=True),
        turn("phone", False, 900.0, text="д" * 5),
    ]
    groups = report.summarize(turns)
    keys = [(g["variant"], g["device"], g["phase"]) for g in groups]
    assert keys == [
        ("base", "host", "cold"),
        ("base", "host", "warm"),
        ("base", "host", "all"),
        ("base", "phone", "warm"),
        ("base", "phone", "all"),
    ]
    warm = groups[1]
    assert warm["n"] == 3
    assert warm["e2e_null"] == 1
    assert warm["interrupted"] == 1
    assert warm["e2e"]["p50"] == pytest.approx(1500)
    assert warm["e2e"]["n"] == 2
    assert warm["links"]["tts_ttfb_ms"]["n"] == 1
    assert warm["decomposition"]["p50"] == pytest.approx(1900)
    assert warm["decomposition"]["n"] == 1
    every = groups[2]
    assert every["n"] == 4
    assert every["markdown_share"] == pytest.approx(0.25)
    assert every["leftover_digits"] == 1
    assert every["median_chars"] == statistics.median([10, 20, 30, 40])


def test_render() -> None:
    turns = [
        turn("host", False, 1000.0),
        turn("host", False, 2000.0),
        turn("host", False, None),
    ]
    table = report.render(report.summarize(turns))
    lines = table.splitlines()
    assert any(line.startswith("| base | host | warm | 3 |") for line in lines)
    assert "—" in table


def test_calibrate() -> None:
    mic = [-80.0] * 1000
    for i in range(100, 300):
        mic[i] = -20.0
    monitor = [-120.0] * 420 + [-20.0] * 580
    [item] = report.calibrate(mic, monitor, [(10.0, 400, 900.0)])
    assert item.turn_ts == 10.0
    assert item.mic_end_s == pytest.approx(3.00, abs=0.010)
    assert item.audio_start_s == pytest.approx(4.20, abs=0.010)
    assert item.client_ms == pytest.approx(1200, abs=10)
    assert item.e2e_ms == 900.0
    assert item.transport_ms == pytest.approx(300, abs=10)
    assert item.valid


def test_calibrate_no_monitor() -> None:
    mic = [-80.0] * 1000
    for i in range(100, 300):
        mic[i] = -20.0
    monitor = [-120.0] * 1000
    [item] = report.calibrate(mic, monitor, [(10.0, 400, 900.0)])
    assert not item.valid
    assert item.note == "нет звука monitor"
