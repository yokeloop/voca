import argparse
import asyncio
import csv
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from audio_stand import (
    AUDIO_URL,
    BENCH_DIR,
    ROOT,
    health,
    make_tts,
    synthesize,
    write_wav,
)
from livekit.agents import APIError
from livekit.agents.voice.transcription.filters import filter_emoji, filter_markdown

from voca.speech.fragments import FirstFragmentTokenizer
from voca.speech.markup import strip_latex_and_tables, strip_tool_calls
from voca.speech.numbers import normalize_numbers
from voca.speech.prepare import prepare, spell_digits
from voca.speech.stress import load_accent

OUT_DIR = BENCH_DIR / "prepare"
CORPUS_DIR = ROOT / "bench" / "corpus"
PIPELINE = CORPUS_DIR / "tts_pipeline.json"
REPLIES = CORPUS_DIR / "tts_prepare.json"
SOURCE = CORPUS_DIR / "tts_ru.txt"
NORM = CORPUS_DIR / "tts_ru_norm.txt"
CHUNK = 7
MARKUP = ("<|", "**", "#", "|", "$")
VOICE = "F4"
CLIPS = (1, 3, 5, 7, 10)


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


def squash(text: str) -> str:
    return " ".join(text.casefold().split())


def phrases() -> list[tuple[str, str]]:
    pipeline = json.loads(PIPELINE.read_text(encoding="utf-8"))
    replies = json.loads(REPLIES.read_text(encoding="utf-8"))["replies"]
    return [(f"pipeline-{item['id']}", item["answer"]) for item in pipeline] + [
        (item["id"], item["reply"]) for item in replies
    ]


async def deltas(text: str) -> AsyncIterator[str]:
    for start in range(0, len(text), CHUNK):
        yield text[start : start + CHUNK]


async def segments(text: str) -> list[str]:
    stream = FirstFragmentTokenizer().stream()
    chain = strip_latex_and_tables(
        filter_emoji(filter_markdown(strip_tool_calls(deltas(text))))
    )
    async for chunk in chain:
        stream.push_text(chunk)
    stream.end_input()
    tokens = [data.token async for data in stream]
    await stream.aclose()
    return [token.strip() for token in tokens if token.strip()]


def cmd_norm(args: argparse.Namespace) -> int:
    source = SOURCE.read_text(encoding="utf-8").splitlines()
    expected = NORM.read_text(encoding="utf-8").splitlines()
    rows = []
    matches = 0
    for index, (line, want) in enumerate(zip(source, expected, strict=True), 1):
        got = normalize_numbers(line)
        match = squash(got) == squash(want)
        matches += match
        if not match:
            print(f"{index}\tожидалось: {want}\n\tполучилось: {got}")
        rows.append([str(index), line, want, got, "1" if match else "0"])
    write_tsv(OUT_DIR / "norm.tsv", ["n", "source", "expected", "got", "match"], rows)
    print(f"{matches}/{len(source)}")
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    stress = load_accent(args.omograph)
    rows = []
    timings: list[float] = []
    digits = 0
    leftover = 0
    markup = dict.fromkeys(MARKUP, 0)
    items = phrases()
    for phrase_id, text in items:
        for segment in await segments(text):
            spelled, _ = spell_digits(normalize_numbers(segment))
            for mark in MARKUP:
                markup[mark] += spelled.count(mark)
            start = time.perf_counter()
            prepared = prepare(segment, stress)
            elapsed = (time.perf_counter() - start) * 1000
            timings.append(elapsed)
            digits += len(re.findall(r"\d", prepared.text))
            leftover += prepared.leftover_digits
            rows.append(
                [
                    phrase_id,
                    segment,
                    prepared.text,
                    f"{elapsed:.1f}",
                    str(prepared.leftover_digits),
                ]
            )
    write_tsv(
        OUT_DIR / "run.tsv", ["id", "segment", "prepared", "ms", "leftover"], rows
    )
    print(f"фраз: {len(items)}, сегментов: {len(rows)}")
    print(f"цифр в подготовленном тексте: {digits}")
    print(f"leftover_digits: {leftover}")
    for mark in MARKUP:
        print(f"остаток {mark}: {markup[mark]}")
    print(
        f"prepare p50 {percentile(timings, 50):.1f} мс, "
        f"p95 {percentile(timings, 95):.1f} мс"
    )
    return 1 if digits or any(markup.values()) else 0


async def cmd_render(args: argparse.Namespace) -> int:
    health(AUDIO_URL)
    stress = load_accent(args.omograph)
    tts = make_tts(AUDIO_URL, VOICE, "ru")
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    for number in CLIPS:
        line = lines[number - 1]
        prepared = prepare(line, stress)
        frame, _, _ = await synthesize(tts, prepared.text)
        path = OUT_DIR / f"clip-{number:02d}.wav"
        write_wav(path, frame)
        path.with_suffix(".txt").write_text(
            f"{line}\n{prepared.text}\n", encoding="utf-8"
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path.name}\n  {line}\n  {prepared.text}\n  sha256 {digest}")
    await tts.aclose()
    return 0


def cmd_listen(args: argparse.Namespace) -> int:
    for wav in sorted(OUT_DIR.glob("clip-*.wav")):
        source, prepared = (
            wav.with_suffix(".txt").read_text(encoding="utf-8").split("\n")[:2]
        )
        print(f"\n{wav.stem}\n  {source}\n  {prepared}")
        subprocess.run(["pw-play", str(wav)], check=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Подготовка текста перед TTS: числа, разметка, первый фрагмент"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("norm")
    run = sub.add_parser("run")
    run.add_argument("--omograph", default="turbo3.1")
    render = sub.add_parser("render")
    render.add_argument("--omograph", default="turbo3.1")
    sub.add_parser("listen")
    args = parser.parse_args()
    if args.command == "norm":
        return cmd_norm(args)
    if args.command == "listen":
        return cmd_listen(args)
    commands = {"run": cmd_run, "render": cmd_render}
    try:
        return asyncio.run(commands[args.command](args))
    except (APIError, httpx.HTTPError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
