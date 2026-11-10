import argparse
import asyncio
import csv
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from audio_stand import (
    AUDIO_URL,
    BENCH_DIR,
    ROOT,
    flush_input,
    health,
    make_tts,
    read_key,
    read_line,
    synthesize,
    write_wav,
)
from livekit.agents import APIError, llm, tokenize
from livekit.plugins import openai
from llm_common import SYSTEM_RU, make_llm

os.environ.setdefault("HF_HUB_OFFLINE", "1")
import ruaccent
from ruaccent import RUAccent

OUT_DIR = BENCH_DIR / "pipeline"
CORPUS = ROOT / "bench" / "corpus" / "tts_pipeline.json"
NORM_CORPUS = ROOT / "bench" / "corpus" / "tts_ru_norm.txt"
STRESS_CORPUS = ROOT / "bench" / "corpus" / "tts_ru_stress.tsv"
MODELS_DIR = Path(os.environ.get("VOCA_MODELS_DIR", Path.home() / "models"))
ACCENT_DIR = MODELS_DIR / "ruaccent" / "accentuator-b78ae5e"
VOICE = "F4"
NUMBERS_LINE = (
    "Числа, даты, время, суммы и телефоны пиши словами в правильном падеже, "
    "без цифр и символов."
)
SYSTEM = f"{SYSTEM_RU}\n{NUMBERS_LINE}"
LEFTOVER = re.compile(r"[0-9%№$€₽°+=*/#@]")
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
ACUTE = "́"
CLIPS = 6
POEZD = {
    "p1": "По́езд прибывает утром.",
    "p2": "по́езд прибывает утром.",
    "p3": "Наш по́езд прибывает утром.",
    "p4": "ПО́езд прибывает утром.",
}


def load_accent(omograph: str) -> RUAccent:
    package = pathlib.Path(ruaccent.__file__).parent
    link = package / "koziev"
    if not link.exists():
        link.symlink_to(ACCENT_DIR / "koziev")
    accent = RUAccent()
    accent.load(
        omograph_model_size=omograph, use_dictionary=True, workdir=str(ACCENT_DIR)
    )
    return accent


def vowels(word: str) -> int:
    return sum(1 for char in word if char in VOWELS)


def to_tts(marked: str) -> str:
    text = re.sub(r"\+(\w)", lambda m: m.group(1) + ACUTE, marked)

    def word(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if vowels(token) > 1 else token.replace(ACUTE, "")

    return re.sub(r"[\ẃ-]+", word, text)


def place(accent: RUAccent, text: str) -> tuple[str, float]:
    start = time.perf_counter()
    marked = accent.process_all(text)
    elapsed = time.perf_counter() - start
    return to_tts(marked), elapsed


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


def stress_words(text: str) -> list[tuple[str, int]]:
    words = []
    for token in re.findall(r"[\ẃ]+", text):
        plain = token.replace(ACUTE, "")
        position = token.find(ACUTE)
        if vowels(plain) > 1 and "ё" not in plain.lower():
            words.append((plain.lower(), position))
    return words


def cmd_accent(args: argparse.Namespace) -> int:
    reference = [
        line.split("\t")[1]
        for line in STRESS_CORPUS.read_text(encoding="utf-8").splitlines()
    ]
    norm = NORM_CORPUS.read_text(encoding="utf-8").splitlines()
    rows = []
    for omograph in args.omograph.split(","):
        start = time.perf_counter()
        accent = load_accent(omograph)
        loaded = time.perf_counter() - start
        for text in norm:
            place(accent, text)
        times = [place(accent, text)[1] for _ in range(3) for text in norm]
        correct = 0
        total = 0
        misses = []
        for text in reference:
            plain = text.replace(ACUTE, "")
            placed, _ = place(accent, plain)
            got = stress_words(placed)
            want = stress_words(text)
            for (word, position), (_, guess) in zip(want, got, strict=True):
                total += 1
                if position == guess:
                    correct += 1
                else:
                    misses.append(word)
            print(f"  {placed}")
        p50 = percentile(times, 50) * 1000
        p95 = percentile(times, 95) * 1000
        print(
            f"{omograph}: загрузка {loaded:.1f} с, на фразу p50 {p50:.1f} мс,"
            f" p95 {p95:.1f} мс; точность {correct}/{total}"
            f" ({100 * correct / total:.1f} %), промахи: {', '.join(misses) or '—'}"
        )
        rows.append(
            [
                omograph,
                f"{loaded:.2f}",
                f"{p50:.1f}",
                f"{p95:.1f}",
                f"{correct}/{total}",
                ",".join(misses),
            ]
        )
    write_tsv(
        OUT_DIR / "accent.tsv",
        ["omograph", "load_s", "p50_ms", "p95_ms", "correct", "misses"],
        rows,
    )
    return 0


@dataclass
class Reply:
    text: str
    ttft: float
    first_sentence: float
    sentence: str


async def ask_llm(model: openai.LLM, user: str) -> Reply:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=SYSTEM)
    ctx.add_message(role="user", content=user)
    sentences = tokenize.blingfire.SentenceTokenizer(retain_format=True).stream()
    start = time.perf_counter()
    ttft = 0.0
    first: list[tuple[float, str]] = []

    async def first_sentence() -> None:
        async for token in sentences:
            if not first:
                first.append((time.perf_counter() - start, token.token))

    reader = asyncio.create_task(first_sentence())
    text = ""
    async with model.chat(chat_ctx=ctx) as stream:
        async for chunk in stream:
            delta = chunk.delta.content if chunk.delta else None
            if delta:
                if not text:
                    ttft = time.perf_counter() - start
                text += delta
                sentences.push_text(delta)
    sentences.end_input()
    await reader
    await sentences.aclose()
    elapsed, sentence = first[0] if first else (time.perf_counter() - start, text)
    return Reply(text, ttft, elapsed, sentence)


async def cmd_run(args: argparse.Namespace) -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    accent = load_accent(args.omograph)
    model = make_llm()
    tts = make_tts(args.base_url, VOICE, "ru")
    await ask_llm(model, "Сколько будет два плюс два?")
    warm, _ = place(accent, "Прогревочная фраза номер один.")
    await synthesize(tts, warm)
    rows = []
    totals = []
    links: dict[str, list[float]] = {
        "ttft": [],
        "sentence": [],
        "accent": [],
        "tts": [],
    }
    leftovers = 0
    for item in corpus:
        reply = await ask_llm(model, item["user"])
        placed, accent_s = place(accent, reply.sentence.strip())
        _, tts_ttfb, _ = await synthesize(tts, placed)
        total = reply.first_sentence + accent_s + tts_ttfb
        found = LEFTOVER.findall(reply.text)
        if found:
            leftovers += 1
        totals.append(total)
        links["ttft"].append(reply.ttft)
        links["sentence"].append(reply.first_sentence - reply.ttft)
        links["accent"].append(accent_s)
        links["tts"].append(tts_ttfb)
        print(
            f"{item['id']}\tTTFT {reply.ttft * 1000:.0f}\tпредложение"
            f" {reply.first_sentence * 1000:.0f}\tударения {accent_s * 1000:.0f}"
            f"\tTTS {tts_ttfb * 1000:.0f}\tитого {total * 1000:.0f} мс"
            f"\t{'цифры/символы: ' + ''.join(found) if found else ''}"
        )
        print(f"  {reply.text}")
        rows.append(
            [
                item["id"],
                f"{reply.ttft * 1000:.0f}",
                f"{reply.first_sentence * 1000:.0f}",
                f"{accent_s * 1000:.1f}",
                f"{tts_ttfb * 1000:.0f}",
                f"{total * 1000:.0f}",
                "".join(found),
                item["user"],
                reply.text,
                placed,
            ]
        )
        if int(item["id"]) <= CLIPS:
            full, _ = place(accent, reply.text)
            frame, _, _ = await synthesize(tts, full)
            write_wav(OUT_DIR / "clips" / f"{item['id']}.wav", frame)
            (OUT_DIR / "clips" / f"{item['id']}.txt").write_text(
                f"{item['user']}\n{full}\n", encoding="utf-8"
            )
    await tts.aclose()
    write_tsv(
        OUT_DIR / "run.tsv",
        [
            "id",
            "ttft_ms",
            "first_sentence_ms",
            "accent_ms",
            "tts_ttfb_ms",
            "total_ms",
            "leftover",
            "user",
            "reply",
            "first_sentence_tts",
        ],
        rows,
    )
    print(f"\nреплик {len(corpus)}, ответов с цифрами или символами {leftovers}")
    for name, values in links.items():
        print(
            f"{name}: p50 {percentile(values, 50) * 1000:.0f} мс,"
            f" p95 {percentile(values, 95) * 1000:.0f} мс"
        )
    print(
        f"до первого звука: p50 {percentile(totals, 50) * 1000:.0f} мс,"
        f" p95 {percentile(totals, 95) * 1000:.0f} мс"
    )
    return 0


async def cmd_poezd(args: argparse.Namespace) -> int:
    tts = make_tts(args.base_url, VOICE, "ru")
    rows = []
    async with httpx.AsyncClient(timeout=120) as client:
        for name, text in POEZD.items():
            frame, _, _ = await synthesize(tts, text)
            path = OUT_DIR / "poezd" / f"{name}.wav"
            write_wav(path, frame)
            response = await client.post(
                f"{args.base_url}/audio/transcriptions",
                files={"file": (path.name, path.read_bytes(), "audio/wav")},
                data={"model": "asr"},
            )
            response.raise_for_status()
            heard = response.json()["text"]
            print(f"{name}\t{text}\t→ {heard}")
            rows.append([name, text, heard])
    await tts.aclose()
    write_tsv(OUT_DIR / "poezd.tsv", ["name", "text", "asr"], rows)
    return 0


def play(path: Path) -> None:
    subprocess.run(["pw-play", str(path)], check=False)


def cmd_listen(args: argparse.Namespace) -> int:
    path = OUT_DIR / "listening.tsv"
    answers: dict[str, list[str]] = {}

    def save() -> None:
        write_tsv(
            path,
            ["clip", "clear", "note", "text"],
            [[clip, *rest] for clip, rest in answers.items()],
        )

    clips = [
        (wav.stem, wav, wav.with_suffix(".txt").read_text(encoding="utf-8").strip())
        for wav in sorted((OUT_DIR / "clips").glob("*.wav"))
    ]
    clips += [
        (name, OUT_DIR / "poezd" / f"{name}.wav", text) for name, text in POEZD.items()
    ]
    for clip, wav, text in clips:
        print(f"\n{clip}. {text}")
        while True:
            play(wav)
            answer = read_key(
                "  разборчиво, ударения верные? [y/n, p — повторить] ",
                ["y", "n", "p"],
            )
            if answer != "p":
                break
        flush_input()
        note = read_line("  заметка (Enter — пусто): ").strip()
        answers[clip] = [answer, note, text.replace("\n", " / ")]
        save()
    print(f"ответы: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сквозной тракт: ответ LLM → ударения → синтез"
    )
    parser.add_argument("--base-url", default=AUDIO_URL)
    sub = parser.add_subparsers(dest="command", required=True)
    accent = sub.add_parser("accent")
    accent.add_argument("--omograph", default="tiny2.1,turbo3.1")
    run = sub.add_parser("run")
    run.add_argument("--omograph", default="turbo3.1")
    sub.add_parser("poezd")
    sub.add_parser("listen")
    args = parser.parse_args()
    if args.command == "accent":
        return cmd_accent(args)
    if args.command == "listen":
        return cmd_listen(args)
    health(args.base_url)
    commands = {"run": cmd_run, "poezd": cmd_poezd}
    try:
        return asyncio.run(commands[args.command](args))
    except (APIError, httpx.HTTPError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
