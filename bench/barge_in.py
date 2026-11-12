import array
import difflib
import json
import math
import re
import statistics
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voca.speech.numbers import normalize_numbers

ROOT = Path(__file__).resolve().parents[1]
G5_DIR = ROOT / ".local" / "g5"
RATE = 48000
FRAME_S = 0.010
SILENCE_DB = -60.0
ONSET_MARGIN_DB = 10.0
ONSET_FRAMES = 3
STOP_SILENCE_S = 0.300
SEARCH_BEFORE_S = 3.0
REFERENCE_S = 2.0
MAX_LAG_S = 0.5
TAIL_WORDS = 2
FLOOR_DB = -120.0
SILENT_BEFORE_FRAMES = 10
TURN_BEFORE_S = 0.1
PUNCT = re.compile(r"[^\w\s]|_")


@dataclass
class Repeat:
    pause_ts: float
    onset_s: float | None
    stop_s: float | None
    delay_ms: float | None
    lag_ms: float | None
    valid: bool
    note: str


def read_stereo(path: Path) -> tuple[int, array.array[int], array.array[int]]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 2:
            raise ValueError(f"{path}: нужен PCM16 2 канала")
        rate = wav.getframerate()
        data = wav.readframes(wav.getnframes())
    samples = array.array("h")
    samples.frombytes(data)
    return rate, samples[0::2], samples[1::2]


def envelope_db(samples: Sequence[int], rate: int) -> list[float]:
    size = round(rate * FRAME_S)
    levels = []
    for start in range(0, len(samples) - size + 1, size):
        chunk = samples[start : start + size]
        power = math.sumprod(chunk, chunk) / size
        levels.append(FLOOR_DB if power == 0 else 10 * math.log10(power / 32768**2))
    return levels


def loud_frames(
    mic: list[float], monitor: list[float], frames: range, lag: int
) -> list[int]:
    return [
        i
        for i in frames
        if 0 <= i < len(monitor) and i + lag < len(mic) and monitor[i] > SILENCE_DB
    ]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    norm = math.sqrt(math.sumprod(dx, dx) * math.sumprod(dy, dy))
    if norm == 0:
        return None
    return math.sumprod(dx, dy) / norm


def estimate_lag(
    mic: list[float], monitor: list[float], frames: range, max_lag: int
) -> int:
    best_lag = 0
    best = -math.inf
    for lag in range(max_lag + 1):
        used = loud_frames(mic, monitor, frames, lag)
        score = pearson([mic[i + lag] for i in used], [monitor[i] for i in used])
        if score is not None and score > best:
            best_lag = lag
            best = score
    return best_lag


def echo_gain(mic: list[float], monitor: list[float], lag: int, frames: range) -> float:
    used = loud_frames(mic, monitor, frames, lag)
    return statistics.median(mic[i + lag] - monitor[i] for i in used)


def find_onset(
    mic: list[float],
    monitor: list[float],
    lag: int,
    gain: float,
    start: int,
    end: int,
) -> int | None:
    run = 0
    for i in range(max(start, 0), min(end, len(mic))):
        echo = monitor[i - lag] + gain if 0 <= i - lag < len(monitor) else FLOOR_DB
        if mic[i] > max(echo, SILENCE_DB) + ONSET_MARGIN_DB:
            run += 1
            if run == ONSET_FRAMES:
                return i - ONSET_FRAMES + 1
        else:
            run = 0
    return None


def find_stop(monitor: list[float], start: int) -> int | None:
    need = round(STOP_SILENCE_S / FRAME_S)
    run = 0
    for i in range(max(start, 0), len(monitor)):
        if monitor[i] < SILENCE_DB:
            run += 1
            if run == need:
                return i - need + 1
        else:
            run = 0
    return None


def measure(
    mic: list[float],
    monitor: list[float],
    pause_frame: int,
    pause_ts: float,
    min_duration: float,
) -> Repeat:
    search_start = pause_frame - round(SEARCH_BEFORE_S / FRAME_S)
    reference = range(search_start - round(REFERENCE_S / FRAME_S), search_start)
    if not loud_frames(mic, monitor, reference, 0):
        return Repeat(
            pause_ts, None, None, None, None, False, "нет речи агента в опорном окне"
        )
    lag = estimate_lag(mic, monitor, reference, round(MAX_LAG_S / FRAME_S))
    gain = echo_gain(mic, monitor, lag, reference)
    lag_ms = lag * FRAME_S * 1000
    onset = find_onset(mic, monitor, lag, gain, search_start, pause_frame)
    if onset is None:
        return Repeat(pause_ts, None, None, None, lag_ms, False, "нет onset")
    onset_s = onset * FRAME_S
    stop = find_stop(monitor, onset - lag)
    if stop is None:
        return Repeat(pause_ts, onset_s, None, None, lag_ms, False, "нет прекращения")
    stop_s = stop * FRAME_S
    delay_ms = (stop + lag - onset) * FRAME_S * 1000
    before = range(max(onset - lag - SILENT_BEFORE_FRAMES, 0), max(onset - lag, 0))
    if all(monitor[i] < SILENCE_DB for i in before):
        return Repeat(
            pause_ts,
            onset_s,
            stop_s,
            delay_ms,
            lag_ms,
            False,
            "monitor молчал до onset",
        )
    note = ""
    if delay_ms < min_duration * 1000:
        note = "раньше min_duration — проверить по записи"
    return Repeat(pause_ts, onset_s, stop_s, delay_ms, lag_ms, True, note)


def load_events(
    path: Path, since: float | None, until: float | None
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


def speaking_intervals(
    rows: list[dict[str, Any]], until: float | None = None
) -> list[tuple[str, float, float]]:
    intervals = []
    open_at: dict[str, float] = {}
    for row in rows:
        if row["kind"] != "agent_state":
            continue
        session = row["session"]
        if row["old"] == "speaking" and session in open_at:
            intervals.append((session, open_at.pop(session), row["ts"]))
        if row["new"] == "speaking":
            open_at[session] = row["ts"]
    end = until if until is not None else max((row["ts"] for row in rows), default=0.0)
    for session, start in open_at.items():
        intervals.append((session, start, max(end, start)))
    return intervals


def pause_moments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns = [row for row in rows if row["kind"] == "turn"]
    return [
        row
        for row in rows
        if row["kind"] == "agent_state"
        and row["old"] == "speaking"
        and row["new"] == "listening"
        and not any(
            turn["session"] == row["session"]
            and row["ts"] - TURN_BEFORE_S <= turn["ts"] <= row["ts"]
            for turn in turns
        )
    ]


def speech_counts(
    rows: list[dict[str, Any]], intervals: list[tuple[str, float, float]]
) -> dict[str, int]:
    return {
        "false_interruption": sum(row["kind"] == "false_interruption" for row in rows),
        "pause": len(pause_moments(rows)),
        "interrupted_turn": sum(
            row["kind"] == "turn" and row.get("interrupted") is True for row in rows
        ),
        "transcribed_while_speaking": sum(
            row["kind"] == "user_input_transcribed"
            and any(
                session == row["session"] and start <= row["ts"] <= end
                for session, start, end in intervals
            )
            for row in rows
        ),
    }


def silence_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "user_input_transcribed": sum(
            row["kind"] == "user_input_transcribed" for row in rows
        ),
        "turn": sum(row["kind"] == "turn" for row in rows),
        "thinking": sum(
            row["kind"] == "agent_state" and row["new"] == "thinking" for row in rows
        ),
    }


def false_counts(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    sessions = sorted({row["session"] for row in rows})
    if mode == "speech":
        intervals = speaking_intervals(rows)
        per_session = {}
        for session in sessions:
            own = [row for row in rows if row["session"] == session]
            own_intervals = [item for item in intervals if item[0] == session]
            per_session[session] = {
                "speaking_s": sum(end - start for _, start, end in own_intervals),
                "counts": speech_counts(own, own_intervals),
            }
        counts = speech_counts(rows, intervals)
        return {
            "mode": mode,
            "speaking_s": sum(end - start for _, start, end in intervals),
            "counts": counts,
            "sessions": per_session,
            "clean": not any(counts.values()),
        }
    if mode == "silence":
        counts = silence_counts(rows)
        return {
            "mode": mode,
            "counts": counts,
            "vad_speaking": sum(
                row["kind"] == "user_state" and row["new"] == "speaking" for row in rows
            ),
            "sessions": {
                session: {
                    "counts": silence_counts(
                        [row for row in rows if row["session"] == session]
                    )
                }
                for session in sessions
            },
            "clean": not any(counts.values()),
        }
    raise ValueError(f"неизвестный режим {mode}")


def norm_words(text: str) -> list[str]:
    text = normalize_numbers(text).lower().replace("ё", "е")
    return PUNCT.sub(" ", text).split()


def tail_match(history: str, heard: str) -> tuple[bool, list[str], list[str]]:
    history_words = norm_words(history)
    heard_words = norm_words(heard)
    matcher = difflib.SequenceMatcher(a=history_words, b=heard_words, autojunk=False)
    i_end = j_end = 0
    for block in matcher.get_matching_blocks():
        if block.size:
            i_end = block.a + block.size
            j_end = block.b + block.size
    extra_history = history_words[i_end:]
    extra_heard = heard_words[j_end:]
    ok = len(extra_history) <= TAIL_WORDS and len(extra_heard) <= TAIL_WORDS
    return ok, extra_history, extra_heard
