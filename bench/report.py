import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
G6_DIR = ROOT / ".local" / "g6"
AGENT_PID = ROOT / ".local" / "run" / "agent.pid"
LEVER_VARS = ("VOCA_ENDPOINTING_MIN_DELAY", "VOCA_PREEMPTIVE_GENERATION")
LEVER_NAMES = {
    "VOCA_ENDPOINTING_MIN_DELAY": "min_delay",
    "VOCA_PREEMPTIVE_GENERATION": "preemptive",
}
LINKS = (
    "end_of_turn_delay_ms",
    "transcription_delay_ms",
    "llm_ttft_ms",
    "first_sentence_ms",
    "tts_ttfb_ms",
    "prepare_ms",
)
DECOMPOSITION = ("end_of_turn_delay_ms", "llm_ttft_ms", "tts_ttfb_ms")
PHASES = ("cold", "warm", "all")
FRAME_S = 0.010
MONITOR_SILENCE_DB = -60.0
RUN_FRAMES = 3
MIC_MARGIN_DB = 20.0
MIC_FLOOR_PERCENTILE = 0.10
ANCHOR_BEFORE_S = 0.5
ANCHOR_AFTER_S = 1.5
SPEECH_BACK_S = 10.0
HEADER = (
    "вариант",
    "устройство",
    "фаза",
    "n",
    "e2e null",
    "перебито",
    "e2e p50",
    "e2e p95",
    "eou p50/p95",
    "stt p50/p95",
    "llm_ttft p50/p95",
    "first_sentence p50/p95",
    "tts_ttfb p50/p95",
    "prepare p50/p95",
    "разложение p50/p95",
    "Markdown, %",
    "медиана, симв.",
    "leftover_digits",
)


@dataclass
class Calib:
    turn_ts: float
    mic_end_s: float | None
    audio_start_s: float | None
    client_ms: float | None
    e2e_ms: float | None
    transport_ms: float | None
    valid: bool
    note: str


def load_rows(
    path: Path, since: float | None = None, until: float | None = None
) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        if since is not None and row["ts"] < since:
            continue
        if until is not None and row["ts"] > until:
            continue
        rows.append(row)
    return rows


def turn_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((row for row in rows if row["kind"] == "turn"), key=lambda r: r["ts"])


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def variant_label(env: dict[str, str]) -> str:
    pairs = [f"{LEVER_NAMES[name]}={env[name]}" for name in LEVER_VARS if name in env]
    return ",".join(pairs) if pairs else "base"


def stats(values: list[float]) -> dict[str, Any]:
    return {
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "n": len(values),
    }


def group(
    variant: str, device: str, phase: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    timed = [row for row in rows if row.get("e2e_latency_ms") is not None]
    return {
        "variant": variant,
        "device": device,
        "phase": phase,
        "n": len(rows),
        "e2e_null": len(rows) - len(timed),
        "interrupted": sum(row.get("interrupted") is True for row in rows),
        "e2e": stats([row["e2e_latency_ms"] for row in timed]),
        "links": {
            name: stats([row[name] for row in rows if row.get(name) is not None])
            for name in LINKS
        },
        "decomposition": stats(
            [
                sum(row[name] for name in DECOMPOSITION)
                for row in timed
                if all(row.get(name) is not None for name in DECOMPOSITION)
            ]
        ),
        "markdown_share": sum((row.get("markdown_removed") or 0) > 0 for row in rows)
        / len(rows),
        "median_chars": statistics.median(len(row.get("text") or "") for row in rows),
        "leftover_digits": sum(row.get("leftover_digits") or 0 for row in rows),
    }


def summarize(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = []
    for variant in sorted({row["variant"] for row in turns}):
        own = [row for row in turns if row["variant"] == variant]
        for device in sorted({row["device"] for row in own}):
            rows = [row for row in own if row["device"] == device]
            phases = {
                "cold": [row for row in rows if row.get("cold") is True],
                "warm": [row for row in rows if row.get("cold") is False],
                "all": rows,
            }
            for phase in PHASES:
                if phases[phase]:
                    groups.append(group(variant, device, phase, phases[phase]))
    return groups


def fmt_ms(value: float | None) -> str:
    return "—" if value is None else str(round(value))


def pair(item: dict[str, Any]) -> str:
    return f"{fmt_ms(item['p50'])}/{fmt_ms(item['p95'])}"


def render(groups: list[dict[str, Any]]) -> str:
    lines = [
        "| " + " | ".join(HEADER) + " |",
        "|" + "|".join(" --- " for _ in HEADER) + "|",
    ]
    for item in groups:
        cells = [
            item["variant"],
            item["device"],
            item["phase"],
            str(item["n"]),
            str(item["e2e_null"]),
            str(item["interrupted"]),
            fmt_ms(item["e2e"]["p50"]),
            fmt_ms(item["e2e"]["p95"]),
            *(pair(item["links"][name]) for name in LINKS),
            pair(item["decomposition"]),
            str(round(item["markdown_share"] * 100)),
            fmt_ms(item["median_chars"]),
            str(item["leftover_digits"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def first_sound(monitor: list[float], start: int, end: int) -> int | None:
    run = 0
    for i in range(max(start, 0), min(end, len(monitor))):
        if monitor[i] > MONITOR_SILENCE_DB:
            run += 1
            if run == RUN_FRAMES:
                return i - RUN_FRAMES + 1
        else:
            run = 0
    return None


def speech_end(
    mic: list[float], floor_db: float, before: int, start: int
) -> int | None:
    end = None
    run = 0
    for i in range(max(start, 0), min(before, len(mic))):
        if mic[i] > floor_db + MIC_MARGIN_DB:
            run += 1
            continue
        if run >= RUN_FRAMES:
            end = i
        run = 0
    if run >= RUN_FRAMES:
        end = min(before, len(mic))
    return end


def calibrate(
    mic: list[float],
    monitor: list[float],
    anchors: list[tuple[float, int, float | None]],
) -> list[Calib]:
    floor_db = percentile(mic, MIC_FLOOR_PERCENTILE)
    before = round(ANCHOR_BEFORE_S / FRAME_S)
    after = round(ANCHOR_AFTER_S / FRAME_S)
    back = round(SPEECH_BACK_S / FRAME_S)
    items = []
    for turn_ts, anchor, e2e_ms in anchors:
        audio_start = first_sound(monitor, anchor - before, anchor + after)
        if audio_start is None:
            items.append(
                Calib(
                    turn_ts, None, None, None, e2e_ms, None, False, "нет звука monitor"
                )
            )
            continue
        audio_start_s = audio_start * FRAME_S
        mic_end = (
            None
            if floor_db is None
            else speech_end(mic, floor_db, audio_start, audio_start - back)
        )
        if mic_end is None:
            items.append(
                Calib(
                    turn_ts,
                    None,
                    audio_start_s,
                    None,
                    e2e_ms,
                    None,
                    False,
                    "нет речи на микрофоне",
                )
            )
            continue
        client_ms = (audio_start - mic_end) * FRAME_S * 1000
        if e2e_ms is None:
            items.append(
                Calib(
                    turn_ts,
                    mic_end * FRAME_S,
                    audio_start_s,
                    client_ms,
                    None,
                    None,
                    False,
                    "нет e2e",
                )
            )
            continue
        items.append(
            Calib(
                turn_ts,
                mic_end * FRAME_S,
                audio_start_s,
                client_ms,
                e2e_ms,
                client_ms - e2e_ms,
                True,
                "",
            )
        )
    return items
