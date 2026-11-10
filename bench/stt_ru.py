import argparse
import array
import asyncio
import csv
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from audio_stand import AUDIO_URL, BENCH_DIR, ROOT, health, make_stt, read_wav, trim
from livekit import rtc
from livekit.agents import APIError, stt
from livekit.plugins import silero

STT_DIR = BENCH_DIR / "stt"
CORPUS = ROOT / "bench" / "corpus" / "stt_ru.tsv"
MODELS_DIR = Path(os.environ.get("VOCA_MODELS_DIR", Path.home() / "models"))
SAMPLES_DIR = MODELS_DIR / "audio-cpp" / "samples"
FALLBACK_FILE = SAMPLES_DIR / "cat_Russian_0.wav"
FRAME_MS = 20
SILENT_PEAK = 2000
PUNCT = re.compile(r"[^\w\s]|_")


@dataclass
class Phrase:
    id: str
    text: str
    keys: list[list[str]]


@dataclass
class Result:
    seconds: float
    batch_time: float
    batch_text: str
    adapter_text: str
    finals: int

    @property
    def rtf(self) -> float:
        return self.batch_time / self.seconds


def peak(frame: rtc.AudioFrame) -> int:
    samples = array.array("h", bytes(frame.data))
    return max((abs(sample) for sample in samples), default=0)


def duration(frame: rtc.AudioFrame) -> float:
    return frame.samples_per_channel / frame.sample_rate


def load_corpus() -> list[Phrase]:
    phrases = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        id_, text, keys = line.split("\t")
        phrases.append(Phrase(id_, text, [key.split("/") for key in keys.split("|")]))
    return phrases


def normalize(text: str) -> str:
    text = PUNCT.sub(" ", text.lower().replace("ё", "е"))
    return " ".join(text.split())


def key_found(forms: list[str], text: str) -> bool:
    padded = f" {normalize(text)} "
    return any(f" {normalize(form)} " in padded for form in forms)


def split_frames(frame: rtc.AudioFrame) -> list[rtc.AudioFrame]:
    step = frame.sample_rate * FRAME_MS // 1000
    data = bytes(frame.data)
    width = 2 * frame.num_channels
    frames = []
    for start in range(0, frame.samples_per_channel, step):
        count = min(step, frame.samples_per_channel - start)
        frames.append(
            rtc.AudioFrame(
                data=data[start * width : (start + count) * width],
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
                samples_per_channel=count,
            )
        )
    return frames


def silence(sample_rate: int, seconds: float) -> rtc.AudioFrame:
    count = int(sample_rate * seconds)
    return rtc.AudioFrame(
        data=b"\0\0" * count,
        sample_rate=sample_rate,
        num_channels=1,
        samples_per_channel=count,
    )


async def recognize_batch(base_url: str, frame: rtc.AudioFrame) -> tuple[str, float]:
    model = make_stt(base_url)
    start = time.perf_counter()
    event = await model.recognize(frame)
    elapsed = time.perf_counter() - start
    await model.aclose()
    return event.alternatives[0].text if event.alternatives else "", elapsed


async def recognize_adapter(
    base_url: str, frame: rtc.AudioFrame, vad: silero.VAD
) -> tuple[str, int]:
    adapter = stt.StreamAdapter(stt=make_stt(base_url), vad=vad)
    stream = adapter.stream()
    texts: list[str] = []

    async def collect() -> None:
        async for event in stream:
            if (
                event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
                and event.alternatives
            ):
                texts.append(event.alternatives[0].text)

    task = asyncio.create_task(collect())
    for chunk in split_frames(frame):
        stream.push_frame(chunk)
    for chunk in split_frames(silence(frame.sample_rate, 1.0)):
        stream.push_frame(chunk)
    stream.end_input()
    await task
    await stream.aclose()
    await adapter.aclose()
    return " ".join(t for t in texts if t), len(texts)


async def run_both(base_url: str, path: Path, vad: silero.VAD) -> Result:
    frame = read_wav(path)
    batch_text, batch_time = await recognize_batch(base_url, frame)
    adapter_text, finals = await recognize_adapter(base_url, frame, vad)
    return Result(duration(frame), batch_time, batch_text, adapter_text, finals)


def write_tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def load_vad() -> silero.VAD:
    vad = silero.VAD.load(min_silence_duration=0.3)
    assert isinstance(vad, silero.VAD)
    return vad


async def cmd_samples(args: argparse.Namespace) -> int:
    files = sorted(SAMPLES_DIR.glob("*Russian*.wav"))
    if not files:
        print(f"нет образцов {SAMPLES_DIR}/*Russian*.wav")
        return 1
    vad = load_vad()
    rows = []
    skipped = []
    failed = 0
    for path in files:
        level = peak(read_wav(path))
        if level < SILENT_PEAK:
            print(f"{path.name}\tskip: пик {level} < {SILENT_PEAK}")
            skipped.append(path.name)
            rows.append([path.name, "", "", "", "", "", "", f"skip peak {level}"])
            continue
        try:
            result = await run_both(args.base_url, path, vad)
        except (APIError, RuntimeError) as exc:
            print(f"{path.name}: ошибка {exc!r}")
            failed = 1
            continue
        if not result.batch_text.strip() or not result.adapter_text.strip():
            failed = 1
        print(
            f"{path.name}\t{result.seconds:.2f} с\tбатч {result.batch_time:.3f} с"
            f"\tRTF {result.rtf:.3f}\tфиналов {result.finals}"
        )
        print(f"  батч:    {result.batch_text}")
        print(f"  адаптер: {result.adapter_text}")
        rows.append(
            [
                path.name,
                f"{result.seconds:.3f}",
                f"{result.batch_time:.3f}",
                f"{result.rtf:.4f}",
                result.batch_text,
                result.adapter_text,
                str(result.finals),
                "ok",
            ]
        )
    write_tsv(
        STT_DIR / "samples.tsv",
        ["file", "seconds", "batch_s", "rtf", "batch", "adapter", "finals", "status"],
        rows,
    )
    if skipped:
        print(f"пропущено тихих образцов {len(skipped)}: {', '.join(skipped)}")
    rtfs = [float(row[3]) for row in rows if row[7] == "ok"]
    if rtfs:
        print(
            f"распознано файлов {len(rtfs)}, RTF медиана {statistics.median(rtfs):.3f},"
            f" максимум {max(rtfs):.3f}"
        )
    return failed


async def cmd_recordings(args: argparse.Namespace) -> int:
    vad = load_vad()
    rows = []
    failed = 0
    complete = 0
    rtfs = []
    for phrase in load_corpus():
        path = STT_DIR / f"{phrase.id}.wav"
        if not path.exists():
            continue
        try:
            result = await run_both(args.base_url, path, vad)
        except (APIError, RuntimeError) as exc:
            print(f"{phrase.id}: ошибка {exc!r}")
            failed = 1
            continue
        if not result.batch_text.strip():
            failed = 1
        found = [k for k in phrase.keys if key_found(k, result.batch_text)]
        missing = [k for k in phrase.keys if k not in found]
        if not missing:
            complete += 1
        rtfs.append(result.rtf)
        print(
            f"{phrase.id}\tключи {len(found)}/{len(phrase.keys)}\tRTF {result.rtf:.3f}"
        )
        print(f"  эталон:  {phrase.text}")
        print(f"  батч:    {result.batch_text}")
        print(f"  адаптер: {result.adapter_text}")
        if missing:
            print(f"  нет: {', '.join('/'.join(k) for k in missing)}")
        rows.append(
            [
                phrase.id,
                phrase.text,
                result.batch_text,
                result.adapter_text,
                f"{len(found)}/{len(phrase.keys)}",
                "|".join("/".join(k) for k in missing),
                f"{result.rtf:.4f}",
            ]
        )
    if not rows:
        print(f"нет записей {STT_DIR}/<id>.wav: stt_ru.py record")
        return 1
    write_tsv(
        STT_DIR / "recordings.tsv",
        ["id", "reference", "batch", "adapter", "keys", "missing", "rtf"],
        rows,
    )
    print(
        f"фраз с полным набором ключей {complete}/{len(rows)},"
        f" RTF медиана {statistics.median(rtfs):.3f}"
    )
    return failed


def rtf5_file(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    for path in sorted(STT_DIR.glob("*.wav")):
        if duration(read_wav(path)) >= 5.0:
            return path
    return FALLBACK_FILE


async def rtf5_times(base_url: str, path: Path, repeat: int) -> list[float]:
    segment = trim(read_wav(path), 5.0)
    times = []
    for _ in range(repeat):
        text, elapsed = await recognize_batch(base_url, segment)
        if not text.strip():
            raise RuntimeError(f"пустой текст на {path}")
        times.append(elapsed)
    return times


async def cmd_rtf5(args: argparse.Namespace) -> int:
    path = rtf5_file(args.file)
    times = await rtf5_times(args.base_url, path, args.repeat)
    rtfs = [t / 5.0 for t in times]
    print(
        f"{path} сегмент 5 с × {args.repeat}: RTF медиана {statistics.median(rtfs):.3f},"
        f" максимум {max(rtfs):.3f}"
    )
    return 0


def record_one(path: Path, target: str | None) -> None:
    command = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
    if target:
        command += ["--target", target]
    command.append(str(path))
    process = subprocess.Popen(command)
    input("  запись… Enter — стоп ")
    process.send_signal(signal.SIGINT)
    process.wait()
    print(f"  {path.name}: {duration(read_wav(path)):.2f} с")


def cmd_record(args: argparse.Namespace) -> int:
    STT_DIR.mkdir(parents=True, exist_ok=True)
    phrases = load_corpus()
    if args.start:
        phrases = [p for p in phrases if p.id >= args.start.zfill(2)]
    for phrase in phrases:
        path = STT_DIR / f"{phrase.id}.wav"
        print(f"\n{phrase.id}. {phrase.text}")
        while True:
            exists = path.exists()
            prompt = (
                "  файл есть: Enter — дальше, r — перезаписать, q — выйти "
                if exists
                else "  Enter — начать, q — выйти "
            )
            answer = input(prompt).strip().lower()
            if answer == "q":
                return 0
            if exists and answer != "r":
                break
            record_one(path, args.target)
            again = (
                input("  Enter — дальше, r — перезаписать, q — выйти ").strip().lower()
            )
            if again == "q":
                return 0
            if again != "r":
                break
    print("все фразы записаны")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="G1: русский STT через audiocpp_server"
    )
    parser.add_argument("--base-url", default=AUDIO_URL)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("samples")
    record = sub.add_parser("record")
    record.add_argument("--from", dest="start")
    record.add_argument("--target")
    sub.add_parser("recordings")
    rtf5 = sub.add_parser("rtf5")
    rtf5.add_argument("--file", type=Path)
    rtf5.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if args.command == "record":
        return cmd_record(args)
    health(args.base_url)
    commands = {"samples": cmd_samples, "recordings": cmd_recordings, "rtf5": cmd_rtf5}
    try:
        return asyncio.run(commands[args.command](args))
    except (APIError, RuntimeError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
