import argparse
import asyncio
import csv
import statistics
import sys
import time

import httpx
from audio_stand import AUDIO_URL, BENCH_DIR, ROOT, health, make_tts, read_wav, trim
from livekit import rtc
from livekit.agents import APIError
from stt_ru import recognize_batch, rtf5_file

LLM_URL = "http://127.0.0.1:8081"
PARALLEL_DIR = BENCH_DIR / "parallel"
TTS_CORPUS = ROOT / "bench" / "corpus" / "tts_ru.txt"
SEGMENT_S = 5.0
PROMPT = (
    "Расскажи подробно, как устроен голосовой ассистент: распознавание речи, "
    "языковая модель и синтез речи. Ответь по-русски."
)


async def stt_latencies(
    base_url: str, segment: rtc.AudioFrame, count: int
) -> list[float]:
    times = []
    for _ in range(count):
        text, elapsed = await recognize_batch(base_url, segment)
        if not text.strip():
            raise RuntimeError("пустой текст STT")
        times.append(elapsed)
    return times


async def stt_during_tts(
    base_url: str, segment: rtc.AudioFrame
) -> tuple[list[tuple[float, bool]], float]:
    text = " ".join(TTS_CORPUS.read_text(encoding="utf-8").splitlines())
    tts = make_tts(base_url, "F1", "ru")
    first = asyncio.Event()
    tts_end = 0.0

    async def speak() -> None:
        nonlocal tts_end
        async with tts.synthesize(text) as stream:
            async for _ in stream:
                first.set()
        tts_end = time.perf_counter()

    task = asyncio.create_task(speak())
    await first.wait()
    results: list[tuple[float, float]] = []
    for _ in range(5):
        stt_text, elapsed = await recognize_batch(base_url, segment)
        if not stt_text.strip():
            raise RuntimeError("пустой текст STT")
        results.append((elapsed, time.perf_counter()))
    await task
    await tts.aclose()
    return [(elapsed, done < tts_end) for elapsed, done in results], tts_end


async def llm_alive(client: httpx.AsyncClient, llm_url: str) -> bool:
    try:
        response = await client.get(f"{llm_url}/health", timeout=5)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def decode_speed(client: httpx.AsyncClient, llm_url: str, model: str) -> float:
    response = await client.post(
        f"{llm_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 256,
            "temperature": 0,
            "stream": False,
        },
        timeout=300,
    )
    response.raise_for_status()
    speed: float = response.json()["timings"]["predicted_per_second"]
    return speed


async def audio_load(
    base_url: str, segment: rtc.AudioFrame, stop: asyncio.Event, rtfs: list[float]
) -> None:
    phrase = TTS_CORPUS.read_text(encoding="utf-8").splitlines()[0]
    tts = make_tts(base_url, "F1", "ru")
    while not stop.is_set():
        _, elapsed = await recognize_batch(base_url, segment)
        rtfs.append(elapsed / SEGMENT_S)
        async with tts.synthesize(phrase) as stream:
            async for _ in stream:
                pass
    await tts.aclose()


async def decode_phase(
    base_url: str, llm_url: str, segment: rtc.AudioFrame
) -> list[list[str]] | None:
    async with httpx.AsyncClient() as client:
        if not await llm_alive(client, llm_url):
            return None
        models = (await client.get(f"{llm_url}/v1/models", timeout=10)).json()
        model = models["data"][0]["id"]
        idle = [await decode_speed(client, llm_url, model) for _ in range(3)]
        stop = asyncio.Event()
        rtfs: list[float] = []
        load = asyncio.create_task(audio_load(base_url, segment, stop, rtfs))
        await asyncio.sleep(1.0)
        loaded = [await decode_speed(client, llm_url, model) for _ in range(3)]
        stop.set()
        await load
    print(f"decode без нагрузки, tok/s: {', '.join(f'{v:.1f}' for v in idle)}")
    print(f"decode под нагрузкой, tok/s: {', '.join(f'{v:.1f}' for v in loaded)}")
    print(
        f"RTF STT 5 с под нагрузкой: медиана {statistics.median(rtfs):.3f},"
        f" максимум {max(rtfs):.3f} ({len(rtfs)} запросов)"
    )
    return [
        ["decode", "idle_tok_s", f"{statistics.median(idle):.2f}", ""],
        ["decode", "loaded_tok_s", f"{statistics.median(loaded):.2f}", ""],
        ["decode", "loaded_stt_rtf", f"{statistics.median(rtfs):.4f}", ""],
        ["decode", "loaded_stt_rtf_max", f"{max(rtfs):.4f}", ""],
    ]


async def run(args: argparse.Namespace) -> int:
    path = rtf5_file(None)
    segment = trim(read_wav(path), SEGMENT_S)
    rows: list[list[str]] = []
    label = args.threads_label or "-"
    print(f"threads {label}, сегмент {path}")

    alone = await stt_latencies(args.base_url, segment, 5)
    alone_median = statistics.median(alone)
    print(f"stt-alone: медиана {alone_median:.3f} с")
    rows.append(["stt-alone", "median_s", f"{alone_median:.4f}", ""])

    during, _ = await stt_during_tts(args.base_url, segment)
    during_median = statistics.median(t for t, _ in during)
    passed = all(early for _, early in during) and during_median <= 2 * alone_median
    for index, (elapsed, early) in enumerate(during, start=1):
        print(f"  stt {index}: {elapsed:.3f} с, раньше конца TTS: {early}")
        rows.append(["stt-during-tts", f"stt{index}_s", f"{elapsed:.4f}", str(early)])
    print(
        f"stt-during-tts: {'PASS' if passed else 'FAIL'}, медиана {during_median:.3f} с"
        f" (≤ {2 * alone_median:.3f})"
    )
    rows.append(
        [
            "stt-during-tts",
            "median_s",
            f"{during_median:.4f}",
            "PASS" if passed else "FAIL",
        ]
    )

    if args.skip_llm:
        print("decode: пропущен (--skip-llm)")
    else:
        decode = await decode_phase(args.base_url, args.llm_url, segment)
        if decode is None:
            print(
                f"decode: llama-server не отвечает на {args.llm_url}, замер перенесён"
            )
        else:
            rows.extend(decode)

    PARALLEL_DIR.mkdir(parents=True, exist_ok=True)
    out = PARALLEL_DIR / f"{label}.tsv"
    with out.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target, delimiter="\t", lineterminator="\n")
        writer.writerow(["phase", "metric", "value", "note"])
        writer.writerows(rows)
    print(f"результат: {out}")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="STT во время TTS и потоки при decode")
    parser.add_argument("--base-url", default=AUDIO_URL)
    parser.add_argument("--llm-url", default=LLM_URL)
    parser.add_argument("--threads-label")
    parser.add_argument("--skip-llm", action="store_true")
    args = parser.parse_args()
    health(args.base_url)
    try:
        return asyncio.run(run(args))
    except (APIError, httpx.HTTPError, RuntimeError) as exc:
        print(f"ошибка: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
