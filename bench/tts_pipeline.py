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
from llm_common import SERVER, SYSTEM_RU, make_llm, model_dir

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
CAPS = [
    ("По\u0301езд", "ПО\u0301езд", "прибывает утром."),
    ("Ра\u0301дио", "РА\u0301дио", "играет тихо."),
    ("Го\u0301род", "ГО\u0301род", "спит под снегом."),
    ("Ле\u0301том", "ЛЕ\u0301том", "здесь жарко."),
    ("Во\u0301здух", "ВО\u0301здух", "стал свежее."),
    ("Де\u0301ньги", "ДЕ\u0301ньги", "лежат на столе."),
]
THINK_MODES: dict[str, tuple[bool, int | None]] = {
    "off": (False, None),
    "on": (True, None),
    "b128": (True, 128),
    "b64": (True, 64),
}
FRAGMENT_BREAK = re.compile(r",|\s[—–-]\s")
FRAGMENT_WORDS = 3
POEZD = {
    "p1": "По́езд прибывает утром.",
    "p2": "по́езд прибывает утром.",
    "p3": "Наш по́езд прибывает утром.",
    "p4": "ПО́езд прибывает утром.",
}


def load_accent(omograph: str) -> RUAccent:
    if not (pathlib.Path(ruaccent.__file__).parent / "koziev").exists():
        raise SystemExit("нет ruaccent/koziev: scripts/ruaccent-setup.sh")
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


def fragment_of(text: str) -> str | None:
    for match in FRAGMENT_BREAK.finditer(text):
        head = text[: match.end()].strip()
        if len(re.findall(r"\w+", head)) >= FRAGMENT_WORDS:
            return head
    return None


def place(accent: RUAccent, text: str) -> tuple[str, float]:
    start = time.perf_counter()
    marked = accent.process_all(text)
    elapsed = time.perf_counter() - start
    return to_tts(marked), elapsed


def percentile(values: list[float], q: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def llm_dir() -> Path:
    return model_dir(OUT_DIR)


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
    first_fragment: float
    fragment: str


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
    fragment: tuple[float, str] | None = None
    async with model.chat(chat_ctx=ctx) as stream:
        async for chunk in stream:
            delta = chunk.delta.content if chunk.delta else None
            if delta:
                if not text:
                    ttft = time.perf_counter() - start
                text += delta
                sentences.push_text(delta)
                if fragment is None and not first:
                    head = fragment_of(text)
                    if head is not None:
                        fragment = (time.perf_counter() - start, head)
    sentences.end_input()
    await reader
    await sentences.aclose()
    elapsed, sentence = first[0] if first else (time.perf_counter() - start, text)
    if fragment is None or len(fragment[1]) >= len(sentence.strip()):
        fragment = (elapsed, sentence)
    return Reply(text, ttft, elapsed, sentence, fragment[0], fragment[1])


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
            write_wav(llm_dir() / "clips" / f"{item['id']}.wav", frame)
            (llm_dir() / "clips" / f"{item['id']}.txt").write_text(
                f"{item['user']}\n{full}\n", encoding="utf-8"
            )
    await tts.aclose()
    write_tsv(
        llm_dir() / "run.tsv",
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


async def cmd_chunks(args: argparse.Namespace) -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    accent = load_accent(args.omograph)
    model = make_llm()
    tts = make_tts(args.base_url, VOICE, "ru")
    await ask_llm(model, "Сколько будет два плюс два?")
    warm, _ = place(accent, "Прогревочная фраза номер один.")
    await synthesize(tts, warm)
    rows = []
    links: dict[str, list[float]] = {
        "sentence_llm": [],
        "sentence_tts": [],
        "sentence_total": [],
        "fragment_llm": [],
        "fragment_tts": [],
        "fragment_total": [],
    }
    for item in corpus:
        reply = await ask_llm(model, item["user"])
        result = [item["id"]]
        for kind, ready, text in [
            ("sentence", reply.first_sentence, reply.sentence.strip()),
            ("fragment", reply.first_fragment, reply.fragment.strip()),
        ]:
            placed, accent_s = place(accent, text)
            _, tts_ttfb, _ = await synthesize(tts, placed)
            total = ready + accent_s + tts_ttfb
            links[f"{kind}_llm"].append(ready)
            links[f"{kind}_tts"].append(tts_ttfb)
            links[f"{kind}_total"].append(total)
            result += [
                f"{ready * 1000:.0f}",
                f"{accent_s * 1000:.1f}",
                f"{tts_ttfb * 1000:.0f}",
                f"{total * 1000:.0f}",
                text,
            ]
        print(
            f"{item['id']}\tпредложение {result[4]} мс\tфрагмент {result[9]} мс"
            f"\t{reply.fragment.strip()}"
        )
        rows.append(result)
    await tts.aclose()
    header = ["id"]
    for kind in ("sentence", "fragment"):
        header += [f"{kind}_llm_ms", f"{kind}_accent_ms", f"{kind}_tts_ms"]
        header += [f"{kind}_total_ms", f"{kind}_text"]
    write_tsv(llm_dir() / "chunks.tsv", header, rows)
    split = sum(1 for row in rows if row[10] != row[5])
    print(f"\nреплик {len(rows)}, первый фрагмент короче предложения в {split}")
    for name, values in links.items():
        print(
            f"{name}: p50 {percentile(values, 50) * 1000:.0f} мс,"
            f" p95 {percentile(values, 95) * 1000:.0f} мс"
        )
    return 0


def normalize(text: str) -> str:
    text = re.sub(r"[^\w\s]|_", " ", text.lower().replace("ё", "е"))
    return " ".join(text.split())


def correct(keys: list[list[str]] | None, text: str) -> str:
    if keys is None:
        return "-"
    plain = f" {normalize(text)}"
    found = any(all(f" {part}" in plain for part in alt) for alt in keys)
    return "y" if found else "n"


@dataclass
class Thought:
    text: str
    reasoning_tokens: int
    first_token: float
    first_content: float
    first_sentence: float
    sentence: str


async def ask_thinking(
    client: httpx.AsyncClient,
    user: str,
    thinking: bool,
    budget: int | None,
    system: str,
) -> Thought:
    body: dict[str, object] = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "max_tokens": 1500,
        "chat_template_kwargs": {
            "enable_thinking": thinking,
            "conversational_mode": True,
        },
    }
    if budget is not None:
        body["thinking_budget_tokens"] = budget
    sentences = tokenize.blingfire.SentenceTokenizer(retain_format=True).stream()
    first: list[tuple[float, str]] = []
    start = time.perf_counter()

    async def first_sentence() -> None:
        async for token in sentences:
            if not first:
                first.append((time.perf_counter() - start, token.token))

    reader = asyncio.create_task(first_sentence())
    text = ""
    reasoning = 0
    first_token = 0.0
    first_content = 0.0
    async with client.stream(
        "POST", f"{SERVER}/v1/chat/completions", json=body, timeout=300
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            for choice in json.loads(line[6:]).get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("reasoning_content"):
                    reasoning += 1
                    if not first_token:
                        first_token = time.perf_counter() - start
                if delta.get("content"):
                    if not first_content:
                        first_content = time.perf_counter() - start
                        first_token = first_token or first_content
                    text += delta["content"]
                    sentences.push_text(delta["content"])
    sentences.end_input()
    await reader
    await sentences.aclose()
    elapsed, sentence = first[0] if first else (time.perf_counter() - start, text)
    return Thought(text, reasoning, first_token, first_content, elapsed, sentence)


async def cmd_think(args: argparse.Namespace) -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    accent = load_accent(args.omograph)
    tts = make_tts(args.base_url, VOICE, "ru")
    warm, _ = place(accent, "Прогревочная фраза номер один.")
    await synthesize(tts, warm)
    system = SYSTEM_RU if args.digits else SYSTEM
    suffix = "-digits" if args.digits else ""
    summary = []
    async with httpx.AsyncClient() as client:
        for mode in args.modes.split(","):
            thinking, budget = THINK_MODES[mode]
            await ask_thinking(
                client, "Сколько будет два плюс два?", thinking, budget, system
            )
            rows = []
            stats: dict[str, list[float]] = {
                "reasoning_tokens": [],
                "thinking": [],
                "ttft_content": [],
                "sentence": [],
                "accent": [],
                "tts": [],
                "total": [],
            }
            right = 0
            scored = 0
            leftovers = 0
            empty = 0
            for item in corpus:
                reply = await ask_thinking(
                    client, item["user"], thinking, budget, system
                )
                placed, accent_s = place(accent, reply.sentence.strip())
                tts_ttfb = 0.0
                if placed.strip():
                    _, tts_ttfb, _ = await synthesize(tts, placed)
                total = reply.first_sentence + accent_s + tts_ttfb
                verdict = correct(item["keys"], reply.text)
                if verdict != "-":
                    scored += 1
                    right += verdict == "y"
                found = LEFTOVER.findall(reply.text)
                leftovers += bool(found)
                stats["reasoning_tokens"].append(reply.reasoning_tokens)
                if placed.strip():
                    stats["thinking"].append(reply.first_content - reply.first_token)
                    stats["ttft_content"].append(reply.first_content)
                    stats["sentence"].append(reply.first_sentence - reply.first_content)
                    stats["accent"].append(accent_s)
                    stats["tts"].append(tts_ttfb)
                    stats["total"].append(total)
                else:
                    empty += 1
                print(
                    f"{mode}\t{item['id']}\t{verdict}\tразмышление"
                    f" {reply.reasoning_tokens} ток\tcontent {reply.first_content * 1000:.0f}"
                    f"\tитого {total * 1000:.0f} мс\t{reply.text.strip()[:120]}"
                )
                rows.append(
                    [
                        item["id"],
                        verdict,
                        str(reply.reasoning_tokens),
                        f"{reply.first_token * 1000:.0f}",
                        f"{reply.first_content * 1000:.0f}",
                        f"{reply.first_sentence * 1000:.0f}",
                        f"{accent_s * 1000:.1f}",
                        f"{tts_ttfb * 1000:.0f}",
                        f"{total * 1000:.0f}",
                        "".join(found),
                        item["user"],
                        reply.text.strip(),
                    ]
                )
            write_tsv(
                llm_dir() / f"think-{mode}{suffix}.tsv",
                [
                    "id",
                    "correct",
                    "reasoning_tokens",
                    "first_token_ms",
                    "first_content_ms",
                    "first_sentence_ms",
                    "accent_ms",
                    "tts_ttfb_ms",
                    "total_ms",
                    "leftover",
                    "user",
                    "reply",
                ],
                rows,
            )
            line = [mode, f"{right}/{scored}", str(leftovers)]
            for name, values in stats.items():
                scale = 1 if name == "reasoning_tokens" else 1000
                line += [
                    f"{percentile(values, 50) * scale:.0f}",
                    f"{percentile(values, 95) * scale:.0f}",
                ]
            summary.append(line)
            print(
                f"{mode}: верно {right}/{scored}, с цифрами {leftovers}/{len(corpus)},"
                f" без ответа {empty}/{len(corpus)}"
            )
    await tts.aclose()
    header = ["mode", "correct", "leftover"]
    for name in stats:
        header += [f"{name}_p50", f"{name}_p95"]
    write_tsv(llm_dir() / f"think{suffix}.tsv", header, summary)
    for line in summary:
        print("\t".join(line))
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


async def cmd_caps(args: argparse.Namespace) -> int:
    tts = make_tts(args.base_url, VOICE, "ru")
    rows = []
    async with httpx.AsyncClient(timeout=120) as client:
        for index, (lower, upper, tail) in enumerate(CAPS, start=1):
            for kind, word in [("lower", lower), ("upper", upper)]:
                text = f"{word} {tail}"
                frame, _, _ = await synthesize(tts, text)
                path = OUT_DIR / "caps" / f"{index:02d}-{kind}.wav"
                write_wav(path, frame)
                response = await client.post(
                    f"{args.base_url}/audio/transcriptions",
                    files={"file": (path.name, path.read_bytes(), "audio/wav")},
                    data={"model": "asr"},
                )
                response.raise_for_status()
                heard = response.json()["text"]
                print(f"{index:02d}-{kind}\t{text}\t→ {heard}")
                rows.append([f"{index:02d}-{kind}", text, heard])
    await tts.aclose()
    write_tsv(OUT_DIR / "caps.tsv", ["clip", "text", "asr"], rows)
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


def cmd_caps_listen(args: argparse.Namespace) -> int:
    path = OUT_DIR / "caps-listening.tsv"
    answers: dict[str, list[str]] = {}

    def save() -> None:
        write_tsv(
            path,
            ["clip", "clear", "note", "text"],
            [[clip, *rest] for clip, rest in answers.items()],
        )

    for index, (lower, upper, tail) in enumerate(CAPS, start=1):
        for kind, word in [("lower", lower), ("upper", upper)]:
            clip = f"{index:02d}-{kind}"
            text = f"{word} {tail}"
            print(f"\n{clip}. {text}")
            while True:
                play(OUT_DIR / "caps" / f"{clip}.wav")
                answer = read_key(
                    "  ударение на первом слоге, разборчиво? [y/n, p — повторить] ",
                    ["y", "n", "p"],
                )
                if answer != "p":
                    break
            flush_input()
            note = read_line("  заметка (Enter — пусто): ").strip()
            answers[clip] = [answer, note, text]
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
    chunks = sub.add_parser("chunks")
    chunks.add_argument("--omograph", default="turbo3.1")
    think = sub.add_parser("think")
    think.add_argument("--modes", default="off,on,b128,b64")
    think.add_argument("--omograph", default="turbo3.1")
    think.add_argument(
        "--digits",
        action="store_true",
        help="drop the numbers-as-words line from the system prompt",
    )
    sub.add_parser("poezd")
    sub.add_parser("caps")
    sub.add_parser("caps-listen")
    sub.add_parser("listen")
    args = parser.parse_args()
    if args.command == "accent":
        return cmd_accent(args)
    if args.command == "listen":
        return cmd_listen(args)
    if args.command == "caps-listen":
        return cmd_caps_listen(args)
    health(args.base_url)
    commands = {
        "run": cmd_run,
        "chunks": cmd_chunks,
        "think": cmd_think,
        "poezd": cmd_poezd,
        "caps": cmd_caps,
    }
    try:
        return asyncio.run(commands[args.command](args))
    except (APIError, httpx.HTTPError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
