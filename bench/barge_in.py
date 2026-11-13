import argparse
import array
import asyncio
import difflib
import hashlib
import json
import math
import re
import signal
import statistics
import subprocess
import time
import wave
from collections.abc import Sequence
from dataclasses import asdict, dataclass
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
LINK_WAIT_S = 3.0
NODE = "voca-g5"
VARIANT_VARS = (
    "VOCA_INTERRUPTION_MIN_DURATION",
    "VOCA_INTERRUPTION_MIN_WORDS",
    "VOCA_AEC_WARMUP",
)
AGENT_PID = ROOT / ".local" / "run" / "agent.pid"
HISTORY_LEAD_S = 0.5
HISTORY_TAIL = 8
SPEECH_WINDOW_S = 600
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


def speaking_intervals(rows: list[dict[str, Any]]) -> list[tuple[str, float, float]]:
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
    end = max((row["ts"] for row in rows), default=0.0)
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


def run_dir(name: str) -> Path:
    return G5_DIR / name


def events_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    from voca.config import Settings

    return Settings().state_dir / "events.jsonl"


def output(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def output_lines(command: list[str]) -> list[str]:
    return [line.strip() for line in output(command).splitlines()]


def agent_variant() -> dict[str, str]:
    try:
        pid = AGENT_PID.read_text(encoding="utf-8").strip()
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        raise SystemExit(f"агент не запущен ({AGENT_PID}): scripts/up.sh agent")
    environ = dict(
        item.split("=", 1) for item in raw.decode().split("\0") if "=" in item
    )
    return {name: environ.get(name, "default") for name in VARIANT_VARS}


def wait_ports(process: subprocess.Popen[bytes]) -> None:
    needed = {f"{NODE}:input_FL", f"{NODE}:input_FR"}
    missing = needed
    deadline = time.monotonic() + LINK_WAIT_S
    while time.monotonic() < deadline:
        missing = needed - set(output_lines(["pw-link", "-i"]))
        if not missing:
            return
        time.sleep(0.1)
    stop_record(process)
    raise SystemExit(f"нет портов записи: {', '.join(sorted(missing))}")


def stop_record(process: subprocess.Popen[bytes]) -> None:
    process.send_signal(signal.SIGINT)
    process.wait()


def peak_db(samples: array.array[int]) -> float:
    peak = max((abs(sample) for sample in samples), default=0)
    return FLOOR_DB if peak == 0 else 20 * math.log10(peak / 32768)


def cmd_record(args: argparse.Namespace) -> int:
    from audio_stand import read_line

    mic = args.mic or output(["pactl", "get-default-source"]).strip()
    speaker = args.speaker or output(["pactl", "get-default-sink"]).strip()
    variant = agent_variant()
    directory = run_dir(args.run)
    directory.mkdir(parents=True, exist_ok=True)
    wav_path = directory / "audio.wav"
    started_at = time.time()
    process = subprocess.Popen(
        [
            "pw-record",
            "--target",
            "0",
            "-P",
            f'{{ node.name = "{NODE}" }}',
            "--channels",
            "2",
            "--channel-map",
            "FL,FR",
            "--rate",
            str(RATE),
            "--format",
            "s16",
            str(wav_path),
        ]
    )
    wait_ports(process)
    ports = output_lines(["pw-link", "-o"])
    mic_port = next((p for p in ports if p.startswith(f"{mic}:capture_")), None)
    monitor_port = f"{speaker}:monitor_FL"
    missing = [f"{mic}:capture_*"] if mic_port is None else []
    if monitor_port not in ports:
        missing.append(monitor_port)
    if mic_port is None or missing:
        stop_record(process)
        raise SystemExit(f"нет порта источника: {', '.join(missing)}")
    subprocess.run(["pw-link", mic_port, f"{NODE}:input_FL"], check=True)
    subprocess.run(["pw-link", monitor_port, f"{NODE}:input_FR"], check=True)
    try:
        read_line("запись… Enter — стоп ")
    except (EOFError, KeyboardInterrupt):
        print()
    stop_record(process)
    stopped_at = time.time()
    meta = {
        "run": args.run,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "rate": RATE,
        "mic": mic,
        "speaker": speaker,
        "mic_port": mic_port,
        "monitor_port": monitor_port,
        "variant": variant,
    }
    write_json(directory / "audio.json", meta)
    rate, left, right = read_stereo(wav_path)
    digest = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    print(f"{wav_path}: {len(left) / rate:.2f} с")
    print(f"  пик FL (микрофон) {peak_db(left):.1f} dBFS")
    print(f"  пик FR (monitor) {peak_db(right):.1f} dBFS")
    print(f"  sha256 {digest}")
    return 0


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def read_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"нет {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def variant_min_duration(variant: dict[str, str]) -> float:
    value = variant.get("VOCA_INTERRUPTION_MIN_DURATION", "default")
    if value != "default":
        return float(value)
    from voca.config import Settings

    return Settings().interruption_min_duration


def fmt(value: float | None, digits: int) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def cmd_analyze(args: argparse.Namespace) -> int:
    directory = run_dir(args.run)
    meta = read_json(directory / "audio.json")
    rate, left, right = read_stereo(directory / "audio.wav")
    mic = envelope_db(left, rate)
    monitor = envelope_db(right, rate)
    rows = load_events(events_path(args.events), meta["started_at"], meta["stopped_at"])
    min_duration = variant_min_duration(meta["variant"])
    repeats = [
        measure(
            mic,
            monitor,
            round((pause["ts"] - meta["started_at"]) / FRAME_S),
            pause["ts"],
            min_duration,
        )
        for pause in pause_moments(rows)
    ]
    print(
        "№ | pause ts | onset, с | прекращение, с | задержка, мс | lag, мс | valid | note"
    )
    for number, repeat in enumerate(repeats, 1):
        print(
            f"{number} | {repeat.pause_ts:.3f} | {fmt(repeat.onset_s, 3)}"
            f" | {fmt(repeat.stop_s, 3)} | {fmt(repeat.delay_ms, 0)}"
            f" | {fmt(repeat.lag_ms, 0)} | {repeat.valid} | {repeat.note}"
        )
    delays = [r.delay_ms for r in repeats if r.valid and r.delay_ms is not None]
    max_delay = max(delays, default=None)
    print(f"максимум задержки по valid: {fmt(max_delay, 0)} мс")
    write_json(
        directory / "analyze.json",
        {
            "run": args.run,
            "variant": meta["variant"],
            "min_duration": min_duration,
            "repeats": [asdict(repeat) for repeat in repeats],
            "max_delay_ms": max_delay,
        },
    )
    return 0


def cmd_false(args: argparse.Namespace) -> int:
    directory = run_dir(args.run)
    since, until = args.since, args.until
    if since is None or until is None:
        meta = read_json(directory / "audio.json")
        since = meta["started_at"] if since is None else since
        until = meta["stopped_at"] if until is None else until
    rows = load_events(events_path(args.events), since, until)
    result = false_counts(rows, args.mode)
    for session, item in result["sessions"].items():
        line = f"{session}: {item['counts']}"
        if args.mode == "speech":
            line += f", speaking_s {item['speaking_s']:.1f}"
        print(line)
    print(f"итог: {result['counts']}")
    if args.mode == "speech":
        short = result["speaking_s"] < SPEECH_WINDOW_S
        mark = " (окно короче 600 с)" if short else ""
        print(f"speaking_s {result['speaking_s']:.1f}{mark}")
    else:
        print(f"vad_speaking {result['vad_speaking']} (вне вердикта)")
    print(f"clean: {result['clean']}")
    directory.mkdir(parents=True, exist_ok=True)
    write_json(
        directory / f"false-{args.mode}.json",
        {"run": args.run, "since": since, "until": until, **result},
    )
    return 0


def monitor_segment(
    monitor: list[float], samples: array.array[int], rate: int, start: int, stop: int
) -> array.array[int] | None:
    first = next(
        (i for i in range(max(start, 0), stop) if monitor[i] > SILENCE_DB), None
    )
    if first is None:
        return None
    size = round(rate * FRAME_S)
    return samples[first * size : stop * size]


async def recognize(base_url: str, samples: array.array[int], rate: int) -> str:
    from livekit import rtc
    from stt_ru import recognize_batch

    frame = rtc.AudioFrame(
        data=samples.tobytes(),
        sample_rate=rate,
        num_channels=1,
        samples_per_channel=len(samples),
    )
    resampler = rtc.AudioResampler(rate, 16000)
    frames = resampler.push(frame) + resampler.flush()
    text, _ = await recognize_batch(base_url, rtc.combine_audio_frames(frames))
    return text


def cmd_history(args: argparse.Namespace) -> int:
    from audio_stand import health

    directory = run_dir(args.run)
    meta = read_json(directory / "audio.json")
    analysis = read_json(directory / "analyze.json")
    health(args.base_url)
    rate, _, right = read_stereo(directory / "audio.wav")
    monitor = envelope_db(right, rate)
    rows = load_events(events_path(args.events), meta["started_at"], meta["stopped_at"])
    repeats = [Repeat(**item) for item in analysis["repeats"]]
    results = []
    for turn in rows:
        if turn["kind"] != "turn" or turn.get("interrupted") is not True:
            continue
        text = turn.get("text") or ""
        started = turn.get("started_speaking_at")
        repeat = None
        if started is not None:
            repeat = next(
                (
                    r
                    for r in repeats
                    if r.valid
                    and r.stop_s is not None
                    and started <= r.pause_ts <= turn["ts"]
                ),
                None,
            )
        item: dict[str, Any] = {
            "turn_ts": turn["ts"],
            "session": turn["session"],
            "history_tail": " ".join(norm_words(text)[-HISTORY_TAIL:]),
        }
        segment = None
        if started is not None and repeat is not None and repeat.stop_s is not None:
            start = round((started - HISTORY_LEAD_S - meta["started_at"]) / FRAME_S)
            stop = round(repeat.stop_s / FRAME_S)
            segment = monitor_segment(monitor, right, rate, start, stop)
        if repeat is None or segment is None:
            item |= {"pause_ts": None, "verdict": "нет отрезка"}
            print(f"{turn['ts']:.3f}: нет отрезка; история «{item['history_tail']}»")
            results.append(item)
            continue
        heard = asyncio.run(recognize(args.base_url, segment, rate))
        ok, extra_history, extra_heard = tail_match(text, heard)
        verdict = "±2 слова" if ok else "расхождение"
        item |= {
            "pause_ts": repeat.pause_ts,
            "heard": heard,
            "heard_tail": " ".join(norm_words(heard)[-HISTORY_TAIL:]),
            "extra_history": extra_history,
            "extra_heard": extra_heard,
            "ok": ok,
            "verdict": verdict,
        }
        print(f"повтор {repeat.pause_ts:.3f}:")
        print(f"  история: …{item['history_tail']}")
        print(f"  услышано: …{item['heard_tail']}")
        print(f"  лишнее в истории: {extra_history}; в услышанном: {extra_heard}")
        print(f"  вердикт: {verdict}")
        results.append(item)
    write_json(directory / "history.json", {"run": args.run, "repeats": results})
    return 0


def main() -> int:
    from audio_stand import AUDIO_URL

    parser = argparse.ArgumentParser(
        description="G5: перебивание на колонках (запись PipeWire, замер, JSONL)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("--run", required=True)
    record.add_argument("--mic")
    record.add_argument("--speaker")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--run", required=True)
    analyze.add_argument("--events", type=Path)
    false = sub.add_parser("false")
    false.add_argument("--run", required=True)
    false.add_argument("--mode", choices=["speech", "silence"], required=True)
    false.add_argument("--events", type=Path)
    false.add_argument("--since", type=float)
    false.add_argument("--until", type=float)
    history = sub.add_parser("history")
    history.add_argument("--run", required=True)
    history.add_argument("--events", type=Path)
    history.add_argument("--base-url", default=AUDIO_URL)
    args = parser.parse_args()
    commands = {
        "record": cmd_record,
        "analyze": cmd_analyze,
        "false": cmd_false,
        "history": cmd_history,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
