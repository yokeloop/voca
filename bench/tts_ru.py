import argparse
import asyncio
import csv
import io
import statistics
import subprocess
import sys
import wave
from pathlib import Path

import httpx
from audio_stand import (
    AUDIO_URL,
    BENCH_DIR,
    ROOT,
    TTS_MODEL,
    TTS_SAMPLE_RATE,
    flush_input,
    health,
    make_tts,
    read_key,
    read_line,
    synthesize,
    write_wav,
)
from livekit import rtc
from livekit.agents import APIError

TTS_DIR = BENCH_DIR / "tts"
CORPUS = ROOT / "bench" / "corpus" / "tts_ru.txt"
VARIANTS: dict[str, str | None] = {"default": None, "ru": "ru"}
RENDER_HEADER = ["variant", "n", "chars", "ttfb_s", "total_s", "audio_s", "rtf"]


def load_corpus() -> list[str]:
    return CORPUS.read_text(encoding="utf-8").splitlines()


def duration(frame: rtc.AudioFrame) -> float:
    return frame.samples_per_channel / frame.sample_rate


def percentile(values: list[float], q: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def write_tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def verdict(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return ok


async def cmd_check(args: argparse.Namespace) -> int:
    text = load_corpus()[0]
    tts = make_tts(args.base_url, args.voice, "ru")
    frame, _, _ = await synthesize(tts, text)
    await tts.aclose()
    body = {
        "model": TTS_MODEL,
        "input": text,
        "voice": args.voice,
        "language": "ru",
        "speed": 1.0,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{args.base_url}/audio/speech", json=body)
        response.raise_for_status()
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            wav_rate = wav.getframerate()
            wav_seconds = wav.getnframes() / wav_rate
        stream_body = {**body, "stream_format": "sse", "response_format": "pcm"}
        async with client.stream(
            "POST", f"{args.base_url}/audio/speech", json=stream_body
        ) as stream:
            content_type = stream.headers.get("content-type", "")
            await stream.aread()
    plugin_seconds = duration(frame)
    results = [
        verdict("wav sample rate", wav_rate == TTS_SAMPLE_RATE, f"{wav_rate} Гц"),
        verdict(
            "plugin sample rate",
            frame.sample_rate == TTS_SAMPLE_RATE,
            f"{frame.sample_rate} Гц",
        ),
        verdict(
            "duration",
            abs(plugin_seconds - wav_seconds) <= 0.05 * wav_seconds,
            f"плагин {plugin_seconds:.3f} с, wav {wav_seconds:.3f} с",
        ),
        verdict(
            "stream content-type",
            content_type.split(";")[0].strip() == "text/event-stream",
            content_type,
        ),
    ]
    return 0 if all(results) else 1


async def cmd_render(args: argparse.Namespace) -> int:
    rows = []
    for variant, language in VARIANTS.items():
        tts = make_tts(args.base_url, args.voice, language)
        for number, text in enumerate(load_corpus(), start=1):
            frame, ttfb, total = await synthesize(tts, text)
            write_wav(TTS_DIR / variant / f"{number:02d}.wav", frame)
            seconds = duration(frame)
            print(
                f"{variant}\t{number:02d}\tTTFB {ttfb:.3f} с\tвсего {total:.3f} с"
                f"\tаудио {seconds:.2f} с"
            )
            rows.append(
                [
                    variant,
                    f"{number:02d}",
                    str(len(text)),
                    f"{ttfb:.3f}",
                    f"{total:.3f}",
                    f"{seconds:.3f}",
                    f"{total / seconds:.4f}",
                ]
            )
        await tts.aclose()
    write_tsv(TTS_DIR / "render.tsv", RENDER_HEADER, rows)
    for variant in VARIANTS:
        ttfbs = [float(r[3]) for r in rows if r[0] == variant]
        totals = [float(r[4]) for r in rows if r[0] == variant]
        print(
            f"{variant}: TTFB p50 {percentile(ttfbs, 50):.3f} p95 {percentile(ttfbs, 95):.3f},"
            f" всего p50 {percentile(totals, 50):.3f} p95 {percentile(totals, 95):.3f}"
        )
    return 0


def play(path: Path) -> None:
    subprocess.run(["pw-play", str(path)], check=False)


def save_listening(path: Path, answers: dict[tuple[str, str], list[str]]) -> None:
    write_tsv(
        path,
        ["n", "variant", "clear", "note", "text"],
        [[n, variant, *rest] for (n, variant), rest in answers.items()],
    )


def load_listening(path: Path) -> dict[tuple[str, str], list[str]]:
    answers: dict[tuple[str, str], list[str]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as source:
            for row in csv.DictReader(source, delimiter="\t"):
                key = (row["n"], row["variant"])
                answers.pop(key, None)
                answers[key] = [row["clear"], row["note"], row["text"]]
    return answers


def cmd_listen(args: argparse.Namespace) -> int:
    path = TTS_DIR / "listening.tsv"
    answers = load_listening(path)
    start = int(args.start or 1)
    for number, text in enumerate(load_corpus(), start=1):
        if number < start:
            continue
        print(f"\n{number:02d}. {text}")
        for variant in VARIANTS:
            wav = TTS_DIR / variant / f"{number:02d}.wav"
            while True:
                print(f"  {variant}")
                play(wav)
                answer = read_key(
                    "  разборчиво без пропусков и повторов? [y/n, p — повторить] ",
                    ["y", "n", "p"],
                )
                if answer != "p":
                    break
            flush_input()
            note = read_line("  заметка (Enter — пусто): ").strip()
            key = (f"{number:02d}", variant)
            answers.pop(key, None)
            answers[key] = [answer, note, text]
            save_listening(path, answers)
    difference = read_key("\nразница default/ru слышна? [y/n] ", ["y", "n"])
    better = read_key("какой вариант лучше? [d — default, r — ru] ", ["d", "r"])
    for name, value in [
        ("difference", difference),
        ("better", "default" if better == "d" else "ru"),
    ]:
        answers.pop(("summary", name), None)
        answers[("summary", name)] = [value, "", ""]
    save_listening(path, answers)
    print(f"ответы: {path}")
    return 0


def render_rates(variant: str) -> dict[int, float]:
    rates: dict[int, float] = {}
    path = TTS_DIR / "render.tsv"
    if not path.exists():
        return rates
    with path.open(encoding="utf-8") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            if row["variant"] == variant:
                rates[int(row["n"])] = float(row["audio_s"]) / int(row["chars"])
    return rates


async def cmd_soak(args: argparse.Namespace) -> int:
    corpus = load_corpus()
    rates = render_rates(args.variant)
    tts = make_tts(args.base_url, args.voice, VARIANTS[args.variant])
    rows = []
    errors = 0
    flags = 0
    ttfbs: list[float] = []
    totals: list[float] = []
    for index in range(args.count):
        number = index % len(corpus) + 1
        text = corpus[number - 1]
        try:
            frame, ttfb, total = await synthesize(tts, text)
        except APIError as exc:
            errors += 1
            print(f"{index + 1:03d}\t{number:02d}\tошибка {exc!r}")
            rows.append([str(index + 1), f"{number:02d}", "", "", "", "error"])
            continue
        seconds = duration(frame)
        if seconds == 0:
            errors += 1
            print(f"{index + 1:03d}\t{number:02d}\tпустое аудио")
            rows.append([str(index + 1), f"{number:02d}", "", "", "0", "empty"])
            continue
        ttfbs.append(ttfb)
        totals.append(total)
        flag = ""
        reference = rates.get(number)
        if reference:
            ratio = seconds / len(text) / reference
            if ratio > 2 or ratio < 0.5:
                flag = f"x{ratio:.2f}"
                flags += 1
        print(
            f"{index + 1:03d}\t{number:02d}\tTTFB {ttfb:.3f}\tвсего {total:.3f}"
            f"\tаудио {seconds:.2f}\t{flag}"
        )
        rows.append(
            [
                str(index + 1),
                f"{number:02d}",
                f"{ttfb:.3f}",
                f"{total:.3f}",
                f"{seconds:.3f}",
                flag,
            ]
        )
    await tts.aclose()
    write_tsv(
        TTS_DIR / f"soak-{args.variant}.tsv",
        ["i", "n", "ttfb_s", "total_s", "audio_s", "flag"],
        rows,
    )
    print(f"фраз {args.count}, ошибок {errors}, флагов {flags}")
    if ttfbs:
        print(
            f"TTFB p50 {percentile(ttfbs, 50):.3f} p95 {percentile(ttfbs, 95):.3f},"
            f" всего p50 {percentile(totals, 50):.3f} p95 {percentile(totals, 95):.3f}"
        )
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="G2: русский TTS через audiocpp_server"
    )
    parser.add_argument("--base-url", default=AUDIO_URL)
    parser.add_argument("--voice", default="F1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("render")
    listen = sub.add_parser("listen")
    listen.add_argument("--from", dest="start")
    soak = sub.add_parser("soak")
    soak.add_argument("--variant", choices=list(VARIANTS), required=True)
    soak.add_argument("--count", type=int, default=200)
    args = parser.parse_args()
    if args.command == "listen":
        return cmd_listen(args)
    health(args.base_url)
    commands = {"check": cmd_check, "render": cmd_render, "soak": cmd_soak}
    try:
        return asyncio.run(commands[args.command](args))
    except (APIError, httpx.HTTPError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
