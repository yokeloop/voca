import array
import math
import statistics
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

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
