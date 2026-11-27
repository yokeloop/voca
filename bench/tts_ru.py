import argparse
import asyncio
import csv
import difflib
import io
import random
import re
import statistics
import subprocess
import sys
import wave
from pathlib import Path

import httpx
from audio_stand import (
    API_KEY,
    AUDIO_URL,
    BENCH_DIR,
    ROOT,
    TTS_MODEL,
    TTS_SAMPLE_RATE,
    SpeechLanguageClient,
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
from livekit.plugins import openai

from voca.speech.stress import load_accent, place

TTS_DIR = BENCH_DIR / "tts"
CORPUS = ROOT / "bench" / "corpus" / "tts_ru.txt"
VARIANTS: dict[str, str | None] = {"default": None, "ru": "ru"}
RENDER_HEADER = [
    "variant",
    "n",
    "chars",
    "ttfb_s",
    "total_s",
    "audio_s",
    "rtf",
    "voice",
]
NORM_CORPUS = ROOT / "bench" / "corpus" / "tts_ru_norm.txt"
AB_DIR = TTS_DIR / "ab"
AB_NUMBERS = [1, 2, 4]
AB_VARIANTS: dict[str, tuple[str, str, int]] = {
    "a": (TTS_MODEL, "F1", 8),
    "b": ("tts-f16", "F1", 16),
    "c": ("tts-f16", "M1", 16),
    "d": ("tts-f16", "F4", 16),
}
AB2_DIR = TTS_DIR / "ab2"
AB2_SEED = 2
AB2_VARIANTS: dict[str, tuple[str, str, int]] = {
    "e1": (TTS_MODEL, "F4", 8),
    "e2": (TTS_MODEL, "F4", 16),
    "e3": ("tts-f16", "F4", 8),
    "e4": ("tts-f16", "F4", 16),
}
STRESS_CORPUS = ROOT / "bench" / "corpus" / "tts_ru_stress.tsv"
STRESS_VARIANT = ("s", TTS_MODEL, "F4", 8)
CORPORA = {"orig": CORPUS, "norm": NORM_CORPUS, "stress": STRESS_CORPUS}


def load_texts(name: str) -> list[tuple[int, str]]:
    lines = CORPORA[name].read_text(encoding="utf-8").splitlines()
    if name == "stress":
        return [(int(n), text) for n, text in (line.split("\t") for line in lines)]
    return list(enumerate(lines, start=1))


def load_corpus() -> list[str]:
    return [text for _, text in load_texts("orig")]


def out_dir(args: argparse.Namespace) -> Path:
    return TTS_DIR / args.tag if args.tag else TTS_DIR


def selected_variants(args: argparse.Namespace) -> dict[str, str | None]:
    names = args.variants.split(",")
    unknown = [name for name in names if name not in VARIANTS]
    if unknown:
        raise SystemExit(
            f"неизвестный вариант {', '.join(unknown)}: есть {', '.join(VARIANTS)}"
        )
    return {name: VARIANTS[name] for name in names}


def bench_tts(args: argparse.Namespace, language: str | None) -> openai.TTS:
    return make_tts(
        args.base_url,
        args.voice,
        language,
        model=args.tts_model,
        sample_rate=args.sample_rate,
    )


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
    tts = bench_tts(args, "ru")
    frame, _, _ = await synthesize(tts, text)
    await tts.aclose()
    body = {
        "model": args.tts_model,
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
        verdict("wav sample rate", wav_rate == args.sample_rate, f"{wav_rate} Гц"),
        verdict(
            "plugin sample rate",
            frame.sample_rate == args.sample_rate,
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
    variants = selected_variants(args)
    stress = load_accent() if args.accent else None
    for variant, language in variants.items():
        tts = bench_tts(args, language)
        for number, text in load_texts(args.corpus):
            if stress is not None:
                text = place(stress, text)
            frame, ttfb, total = await synthesize(tts, text)
            write_wav(out_dir(args) / variant / f"{number:02d}.wav", frame)
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
                    args.voice,
                ]
            )
        await tts.aclose()
    write_tsv(out_dir(args) / "render.tsv", RENDER_HEADER, rows)
    for variant in variants:
        ttfbs = [float(r[3]) for r in rows if r[0] == variant]
        totals = [float(r[4]) for r in rows if r[0] == variant]
        audio = [float(r[5]) for r in rows if r[0] == variant]
        print(
            f"{variant}: TTFB p50 {percentile(ttfbs, 50):.3f} p95 {percentile(ttfbs, 95):.3f},"
            f" всего p50 {percentile(totals, 50):.3f} p95 {percentile(totals, 95):.3f},"
            f" RTF {sum(totals) / sum(audio):.3f}"
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
    path = out_dir(args) / "listening.tsv"
    answers = load_listening(path)
    start = int(args.start or 1)
    variants = selected_variants(args)
    for number, text in load_texts(args.corpus):
        if number < start:
            continue
        print(f"\n{number:02d}. {text}")
        for variant in variants:
            wav = out_dir(args) / variant / f"{number:02d}.wav"
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
    if list(variants) == list(VARIANTS):
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


def make_ab_tts(base_url: str, model: str, voice: str, steps: int) -> openai.TTS:
    return openai.TTS(
        model=model,
        voice=voice,
        response_format="pcm",
        sample_rate=TTS_SAMPLE_RATE,
        client=SpeechLanguageClient(
            speech_language="ru",
            speech_options={"num_inference_steps": steps},
            base_url=base_url,
            api_key=API_KEY,
            max_retries=0,
        ),
    )


def parse_numbers(value: str) -> list[int]:
    return [int(part) for part in value.split(",")]


async def cmd_ab_render(args: argparse.Namespace) -> int:
    corpus = NORM_CORPUS.read_text(encoding="utf-8").splitlines()
    rows = []
    for variant, (model, voice, steps) in AB_VARIANTS.items():
        tts = make_ab_tts(args.base_url, model, voice, steps)
        for number in parse_numbers(args.numbers):
            text = corpus[number - 1]
            frame, ttfb, total = await synthesize(tts, text)
            write_wav(AB_DIR / variant / f"{number:02d}.wav", frame)
            seconds = duration(frame)
            print(
                f"{variant} {model} {voice} {steps}\t{number:02d}\tTTFB {ttfb:.3f} с"
                f"\tвсего {total:.3f} с\tаудио {seconds:.2f} с\tRTF {total / seconds:.3f}"
            )
            rows.append(
                [
                    variant,
                    model,
                    voice,
                    str(steps),
                    f"{number:02d}",
                    f"{ttfb:.3f}",
                    f"{total:.3f}",
                    f"{seconds:.3f}",
                    f"{total / seconds:.4f}",
                ]
            )
        await tts.aclose()
    write_tsv(
        AB_DIR / "render.tsv",
        [
            "variant",
            "model",
            "voice",
            "steps",
            "n",
            "ttfb_s",
            "total_s",
            "audio_s",
            "rtf",
        ],
        rows,
    )
    for variant in AB_VARIANTS:
        totals = [float(r[6]) for r in rows if r[0] == variant]
        audio = [float(r[7]) for r in rows if r[0] == variant]
        print(
            f"{variant}: полное время p50 {percentile(totals, 50):.3f} p95"
            f" {percentile(totals, 95):.3f}, RTF {sum(totals) / sum(audio):.3f}"
        )
    return 0


def cmd_ab(args: argparse.Namespace) -> int:
    corpus = NORM_CORPUS.read_text(encoding="utf-8").splitlines()
    path = TTS_DIR / "ab.tsv"
    answers = load_listening(path)
    for number in parse_numbers(args.numbers):
        text = corpus[number - 1]
        print(f"\n{number:02d}. {text}")
        for variant in AB_VARIANTS:
            wav = AB_DIR / variant / f"{number:02d}.wav"
            while True:
                print(f"  {variant}")
                play(wav)
                answer = read_key(
                    "  разборчиво, числа понятны? [y/n, p — повторить] ",
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
    best = read_key("\nкакой вариант лучше? [a/b/c/d] ", list(AB_VARIANTS))
    answers.pop(("summary", "best"), None)
    answers[("summary", "best")] = [best, "", ""]
    save_listening(path, answers)
    print(f"ответы: {path}")
    return 0


def load_stress() -> dict[int, str]:
    return dict(load_texts("stress"))


async def cmd_ab2_render(args: argparse.Namespace) -> int:
    corpus = NORM_CORPUS.read_text(encoding="utf-8").splitlines()
    stress = load_stress()
    jobs = [
        (variant, model, voice, steps, corpus)
        for variant, (model, voice, steps) in AB2_VARIANTS.items()
    ]
    rows = []
    for variant, model, voice, steps, texts in jobs + [(*STRESS_VARIANT, [])]:
        tts = make_ab_tts(args.base_url, model, voice, steps)
        for number in AB_NUMBERS:
            text = stress[number] if variant == "s" else texts[number - 1]
            frame, _, total = await synthesize(tts, text)
            write_wav(AB2_DIR / variant / f"{number:02d}.wav", frame)
            seconds = duration(frame)
            print(
                f"{variant} {model} {voice} {steps}\t{number:02d}\tвсего {total:.3f} с"
                f"\tаудио {seconds:.2f} с\tRTF {total / seconds:.3f}"
            )
            rows.append(
                [
                    variant,
                    model,
                    voice,
                    str(steps),
                    f"{number:02d}",
                    f"{total:.3f}",
                    f"{seconds:.3f}",
                    f"{total / seconds:.4f}",
                ]
            )
        await tts.aclose()
    write_tsv(
        AB2_DIR / "render.tsv",
        ["variant", "model", "voice", "steps", "n", "total_s", "audio_s", "rtf"],
        rows,
    )
    return 0


def ab2_labels() -> tuple[dict[str, str], dict[int, list[str]]]:
    rng = random.Random(AB2_SEED)
    variants = list(AB2_VARIANTS)
    shuffled = variants[:]
    rng.shuffle(shuffled)
    labels = {variant: str(i) for i, variant in enumerate(shuffled, start=1)}
    orders = {}
    for number in AB_NUMBERS:
        order = variants[:]
        rng.shuffle(order)
        orders[number] = order
    return labels, orders


def cmd_ab2(args: argparse.Namespace) -> int:
    corpus = NORM_CORPUS.read_text(encoding="utf-8").splitlines()
    path = TTS_DIR / "ab2.tsv"
    header = ["n", "label", "variant", "model", "steps", "clear", "note", "text"]
    answers: dict[tuple[str, str], list[str]] = {}

    def save() -> None:
        write_tsv(path, header, [[n, *rest] for (n, _), rest in answers.items()])

    labels, orders = ab2_labels()
    for number in AB_NUMBERS:
        text = corpus[number - 1]
        print(f"\n{number:02d}. {text}")
        for variant in orders[number]:
            label = labels[variant]
            model, _, steps = AB2_VARIANTS[variant]
            wav = AB2_DIR / variant / f"{number:02d}.wav"
            while True:
                print(f"  вариант {label}")
                play(wav)
                answer = read_key(
                    "  разборчиво? [y/n, p — повторить] ", ["y", "n", "p"]
                )
                if answer != "p":
                    break
            flush_input()
            note = read_line("  заметка (Enter — пусто): ").strip()
            answers[(f"{number:02d}", variant)] = [
                label,
                variant,
                model,
                str(steps),
                answer,
                note,
                text,
            ]
            save()
    differ = read_key(
        "\nотличаются ли варианты по произношению/ударениям? [y/n] ", ["y", "n"]
    )
    answers[("summary", "differ")] = ["", "differ", "", "", differ, "", ""]
    if differ == "y":
        best = read_key("если да — лучший [1-4] ", ["1", "2", "3", "4"])
        revealed = next(v for v, label in labels.items() if label == best)
        answers[("summary", "best")] = [best, revealed, "", "", "", "", ""]
    save()
    stress = load_stress()
    print("\nдополнительно: ударения расставлены вручную (F4, q8_0, 8 шагов)")
    for number in AB_NUMBERS:
        variant, model, _, steps = STRESS_VARIANT
        print(f"\n{number:02d}. {stress[number]}")
        wav = AB2_DIR / variant / f"{number:02d}.wav"
        while True:
            play(wav)
            answer = read_key(
                "  ударения правильные, разборчиво? [y/n, p — повторить] ",
                ["y", "n", "p"],
            )
            if answer != "p":
                break
        flush_input()
        note = read_line("  заметка (Enter — пусто): ").strip()
        answers[(f"{number:02d}", variant)] = [
            "s",
            variant,
            model,
            str(steps),
            answer,
            note,
            stress[number],
        ]
        save()
    print(f"ответы: {path}")
    return 0


VOICES = [f"{gender}{n}" for gender in "FM" for n in range(1, 6)]


def plain_words(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е"))
    return " ".join(text.split())


def asr_score(reference: str, recognized: str) -> float:
    plain = reference.replace("\u0301", "")
    return difflib.SequenceMatcher(
        None, plain_words(plain), plain_words(recognized)
    ).ratio()


async def cmd_voiceprobe(args: argparse.Namespace) -> int:
    corpus = NORM_CORPUS.read_text(encoding="utf-8").splitlines()
    rows = []
    summary = []
    async with httpx.AsyncClient(timeout=300) as client:
        for voice in VOICES:
            scores = []
            for number in AB_NUMBERS:
                text = corpus[number - 1]
                speech = await client.post(
                    f"{args.base_url}/audio/speech",
                    json={
                        "model": args.model,
                        "input": text,
                        "voice": voice,
                        "language": "ru",
                        "speed": 1.0,
                        "num_inference_steps": args.steps,
                    },
                )
                speech.raise_for_status()
                heard = await client.post(
                    f"{args.base_url}/audio/transcriptions",
                    files={"file": ("speech.wav", speech.content, "audio/wav")},
                    data={"model": "asr"},
                )
                heard.raise_for_status()
                recognized = heard.json()["text"]
                score = asr_score(text, recognized)
                scores.append(score)
                print(f"{voice}\t{number:02d}\t{score:.3f}\t{recognized}")
                rows.append([voice, f"{number:02d}", f"{score:.3f}", recognized])
            mean = sum(scores) / len(scores)
            summary.append((mean, voice))
            print(f"== {voice} {mean:.3f}")
    write_tsv(TTS_DIR / "voiceprobe.tsv", ["voice", "n", "score", "asr"], rows)
    best = max(summary)
    print(f"лучший round-trip: {best[1]} {best[0]:.3f}")
    return 0


def render_rates(path: Path, variant: str, voice: str) -> dict[int, float]:
    rates: dict[int, float] = {}
    if not path.exists():
        return rates
    with path.open(encoding="utf-8") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            if row["variant"] == variant and row.get("voice") == voice:
                rates[int(row["n"])] = float(row["audio_s"]) / int(row["chars"])
    return rates


async def cmd_soak(args: argparse.Namespace) -> int:
    corpus = load_texts(args.corpus)
    rates = render_rates(out_dir(args) / "render.tsv", args.variant, args.voice)
    if not rates:
        print(
            f"нет строк render для варианта {args.variant} и голоса {args.voice}:"
            f" tts_ru.py --voice {args.voice} render",
            file=sys.stderr,
        )
        return 1
    tts = bench_tts(args, VARIANTS[args.variant])
    rows = []
    errors = 0
    flags = 0
    ttfbs: list[float] = []
    totals: list[float] = []
    for index in range(args.count):
        number, text = corpus[index % len(corpus)]
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
        out_dir(args) / f"soak-{args.variant}.tsv",
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


async def cmd_asr(args: argparse.Namespace) -> int:
    variants = selected_variants(args)
    texts = load_texts(args.corpus)
    for variant in variants:
        for number, _ in texts:
            wav = out_dir(args) / variant / f"{number:02d}.wav"
            if not wav.exists():
                print(
                    f"нет {wav}: tts_ru.py --tag {args.tag} --corpus {args.corpus}"
                    f" --variants {args.variants} render",
                    file=sys.stderr,
                )
                return 1
    rows = []
    async with httpx.AsyncClient(timeout=300) as client:
        for variant in variants:
            scores = []
            for number, text in texts:
                wav = out_dir(args) / variant / f"{number:02d}.wav"
                heard = await client.post(
                    f"{args.asr_url}/audio/transcriptions",
                    files={"file": ("speech.wav", wav.read_bytes(), "audio/wav")},
                    data={"model": "asr"},
                )
                heard.raise_for_status()
                recognized = heard.json()["text"]
                score = asr_score(text, recognized)
                scores.append(score)
                print(f"{variant}\t{number:02d}\t{score:.3f}\t{recognized}")
                rows.append(
                    [variant, f"{number:02d}", f"{score:.3f}", recognized, text]
                )
            print(f"== {variant} ASR {sum(scores) / len(scores):.3f}")
    write_tsv(out_dir(args) / "asr.tsv", ["variant", "n", "score", "asr", "text"], rows)
    return 0


def blind_labels(
    tags: list[str], numbers: list[int]
) -> tuple[dict[str, str], dict[int, list[str]]]:
    rng = random.Random(AB2_SEED)
    shuffled = tags[:]
    rng.shuffle(shuffled)
    labels = {tag: str(i) for i, tag in enumerate(shuffled, start=1)}
    orders = {}
    for number in numbers:
        order = tags[:]
        rng.shuffle(order)
        orders[number] = order
    return labels, orders


def cmd_blind(args: argparse.Namespace) -> int:
    texts = dict(load_texts(args.corpus))
    tags = args.tags.split(",")
    numbers = parse_numbers(args.numbers)
    path = TTS_DIR / f"{args.name}.tsv"
    header = ["n", "label", "tag", "clear", "note", "text"]
    answers: dict[tuple[str, str], list[str]] = {}

    def save() -> None:
        write_tsv(path, header, [[n, *rest] for (n, _), rest in answers.items()])

    labels, orders = blind_labels(tags, numbers)
    for number in numbers:
        text = texts[number]
        print(f"\n{number:02d}. {text}")
        for tag in orders[number]:
            label = labels[tag]
            wav = TTS_DIR / tag / "ru" / f"{number:02d}.wav"
            while True:
                print(f"  вариант {label}")
                play(wav)
                answer = read_key(
                    "  разборчиво? [y/n, p — повторить] ", ["y", "n", "p"]
                )
                if answer != "p":
                    break
            flush_input()
            note = read_line("  заметка (Enter — пусто): ").strip()
            answers[(f"{number:02d}", tag)] = [label, tag, answer, note, text]
            save()
    choices = [str(i) for i in range(1, len(tags) + 1)]
    best = read_key(f"\nлучший [1-{len(tags)}] ", choices)
    revealed = next(tag for tag, label in labels.items() if label == best)
    answers[("summary", "best")] = [best, revealed, "", "", ""]
    save()
    print(f"лучший: {best} — {revealed}")
    print(f"ответы: {path}")
    return 0


def parse_groups(value: str) -> list[list[int]]:
    groups = []
    for part in value.split(","):
        first, last = part.split("-")
        groups.append(list(range(int(first), int(last) + 1)))
    return groups


async def cmd_replies(args: argparse.Namespace) -> int:
    texts = dict(load_texts(args.corpus))
    tts = bench_tts(args, "ru")
    for k, group in enumerate(parse_groups(args.groups), start=1):
        frames = []
        parts = [texts[number] for number in group]
        for text in parts:
            frame, ttfb, total = await synthesize(tts, text)
            frames.append(frame)
            print(f"{k:02d}\tTTFB {ttfb:.3f} с\tвсего {total:.3f} с\t{text}")
        path = out_dir(args) / "replies" / f"{k:02d}.wav"
        write_wav(path, rtc.combine_audio_frames(frames))
        path.with_suffix(".txt").write_text(" ".join(parts) + "\n", encoding="utf-8")
        print(f"ответ {k:02d}: {path}")
    await tts.aclose()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="G2: русский TTS через audiocpp_server"
    )
    parser.add_argument("--base-url", default=AUDIO_URL)
    parser.add_argument("--voice", default="F1")
    parser.add_argument("--model", dest="tts_model", default=TTS_MODEL)
    parser.add_argument("--sample-rate", type=int, default=TTS_SAMPLE_RATE)
    parser.add_argument("--tag", default="")
    parser.add_argument("--variants", default="default,ru")
    parser.add_argument("--corpus", choices=list(CORPORA), default="orig")
    parser.add_argument("--asr-url", default=AUDIO_URL)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    render = sub.add_parser("render")
    render.add_argument("--accent", action="store_true")
    listen = sub.add_parser("listen")
    listen.add_argument("--from", dest="start")
    soak = sub.add_parser("soak")
    soak.add_argument("--variant", choices=list(VARIANTS), required=True)
    soak.add_argument("--count", type=int, default=200)
    ab_render = sub.add_parser("ab-render")
    ab_render.add_argument("--numbers", default="1,2,4")
    ab = sub.add_parser("ab")
    ab.add_argument("--numbers", default="1,2,4")
    voiceprobe = sub.add_parser("voiceprobe")
    voiceprobe.add_argument("--model", default="tts-f16")
    voiceprobe.add_argument("--steps", type=int, default=16)
    sub.add_parser("ab2-render")
    sub.add_parser("ab2")
    sub.add_parser("asr")
    blind = sub.add_parser("blind")
    blind.add_argument("--tags", required=True)
    blind.add_argument("--numbers", default="1,2,4")
    blind.add_argument("--name", default="blind")
    replies = sub.add_parser("replies")
    replies.add_argument("--groups", default="1-3,4-6,7-9")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "ab2":
        return cmd_ab2(args)
    if args.command == "listen":
        return cmd_listen(args)
    if args.command == "ab":
        return cmd_ab(args)
    if args.command == "blind":
        return cmd_blind(args)
    health(args.asr_url if args.command == "asr" else args.base_url)
    commands = {
        "check": cmd_check,
        "render": cmd_render,
        "soak": cmd_soak,
        "ab-render": cmd_ab_render,
        "ab2-render": cmd_ab2_render,
        "voiceprobe": cmd_voiceprobe,
        "asr": cmd_asr,
        "replies": cmd_replies,
    }
    try:
        return asyncio.run(commands[args.command](args))
    except (APIError, httpx.HTTPError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
