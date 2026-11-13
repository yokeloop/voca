import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
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


def run_json(name: str) -> Path:
    return G6_DIR / name / "run.json"


def agent_env() -> dict[str, str]:
    try:
        pid = AGENT_PID.read_text(encoding="utf-8").strip()
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        raise SystemExit(f"агент не запущен ({AGENT_PID}): scripts/up.sh agent")
    environ = dict(
        item.split("=", 1) for item in raw.decode().split("\0") if "=" in item
    )
    return {name: environ[name] for name in LEVER_VARS if name in environ}


def cmd_start(args: argparse.Namespace) -> int:
    from barge_in import write_json

    directory = G6_DIR / args.run
    directory.mkdir(parents=True, exist_ok=True)
    if run_json(args.run).exists():
        raise SystemExit(f"прогон {args.run} уже есть")
    env = agent_env()
    meta = {
        "run": args.run,
        "started_at": time.time(),
        "env": env,
        "variant": variant_label(env),
    }
    write_json(run_json(args.run), meta)
    print(f"run {meta['run']}")
    print(f"variant {meta['variant']}")
    print(f"started_at {meta['started_at']:.3f}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from barge_in import events_path, read_json, write_json

    meta = read_json(run_json(args.run))
    meta["stopped_at"] = time.time()
    events = events_path(args.events)
    rows = (
        load_rows(events, meta["started_at"], meta["stopped_at"])
        if events.exists()
        else []
    )
    snapshot = G6_DIR / args.run / "events.jsonl"
    snapshot.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    turns = turn_rows(rows)
    meta["rows"] = len(rows)
    meta["turns"] = len(turns)
    meta["sessions"] = len({row["session"] for row in turns})
    meta["sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    write_json(run_json(args.run), meta)
    print(f"stopped_at {meta['stopped_at']:.3f}")
    for name in ("rows", "turns", "sessions", "sha256"):
        print(f"{name} {meta[name]}")
    print(f"devices {sorted({row['device'] for row in turns})}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    from barge_in import read_json, write_json

    turns = []
    metas = []
    for name in args.run:
        meta = read_json(run_json(name))
        own = turn_rows(load_rows(G6_DIR / name / "events.jsonl"))
        if args.limit is not None:
            own = own[: args.limit]
        turns.extend({**row, "variant": meta["variant"]} for row in own)
        metas.append(meta)
    groups = summarize(turns)
    print(render(groups))
    print()
    print("run | variant | started_at | stopped_at | turns | sessions | sha256")
    for meta in metas:
        print(
            f"{meta['run']} | {meta['variant']} | {meta['started_at']:.3f}"
            f" | {fmt(meta.get('stopped_at'), 3)} | {meta.get('turns', '—')}"
            f" | {meta.get('sessions', '—')} | {meta.get('sha256', '—')}"
        )
    out = args.out or G6_DIR / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, groups)
    return 0


def fmt(value: float | None, digits: int) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def cmd_calibrate(args: argparse.Namespace) -> int:
    from barge_in import (
        G5_DIR,
        envelope_db,
        events_path,
        read_json,
        read_stereo,
        write_json,
    )

    directory = G5_DIR / args.audio_run
    meta = read_json(directory / "audio.json")
    rate, left, right = read_stereo(directory / "audio.wav")
    mic = envelope_db(left, rate)
    monitor = envelope_db(right, rate)
    turns = turn_rows(
        load_rows(events_path(args.events), meta["started_at"], meta["stopped_at"])
    )
    anchors = [
        (
            row["ts"],
            round((row["started_speaking_at"] - meta["started_at"]) / FRAME_S),
            row.get("e2e_latency_ms"),
        )
        for row in turns
        if row.get("started_speaking_at") is not None
    ]
    measured = iter(calibrate(mic, monitor, anchors))
    items = [
        next(measured)
        if row.get("started_speaking_at") is not None
        else Calib(
            row["ts"],
            None,
            None,
            None,
            row.get("e2e_latency_ms"),
            None,
            False,
            "нет started_speaking_at",
        )
        for row in turns
    ]
    print(
        "№ | turn ts | конец речи, с | первый звук, с | клиент, мс | e2e, мс"
        " | доставка, мс | valid | note"
    )
    for number, item in enumerate(items, 1):
        print(
            f"{number} | {item.turn_ts:.3f} | {fmt(item.mic_end_s, 2)}"
            f" | {fmt(item.audio_start_s, 2)} | {fmt(item.client_ms, 0)}"
            f" | {fmt(item.e2e_ms, 0)} | {fmt(item.transport_ms, 0)}"
            f" | {item.valid} | {item.note}"
        )
    valid = [item for item in items if item.valid]
    client = (
        statistics.median(i.client_ms for i in valid if i.client_ms is not None)
        if valid
        else None
    )
    transport = (
        statistics.median(i.transport_ms for i in valid if i.transport_ms is not None)
        if valid
        else None
    )
    print(f"медиана клиентской задержки по valid: {fmt(client, 0)} мс")
    print(f"медиана доставки по valid: {fmt(transport, 0)} мс")
    G6_DIR.mkdir(parents=True, exist_ok=True)
    write_json(
        G6_DIR / "calibration.json",
        {
            "audio_run": args.audio_run,
            "items": [asdict(item) for item in items],
            "client_ms_median": client,
            "transport_ms_median": transport,
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="G6: сквозная латентность по JSONL (окна прогонов, сводка, калибровка)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--run", required=True)
    stop = sub.add_parser("stop")
    stop.add_argument("--run", required=True)
    stop.add_argument("--events", type=Path)
    summary = sub.add_parser("summary")
    summary.add_argument("--run", required=True, action="append")
    summary.add_argument("--limit", type=int)
    summary.add_argument("--out", type=Path)
    calib = sub.add_parser("calibrate")
    calib.add_argument("--audio-run", required=True)
    calib.add_argument("--events", type=Path)
    args = parser.parse_args()
    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "summary": cmd_summary,
        "calibrate": cmd_calibrate,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
