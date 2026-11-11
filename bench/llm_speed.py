import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from llm_common import (
    BASE_URL,
    KWARGS,
    ROOT,
    SERVER,
    THINKING,
    TOOL_SCHEMAS,
    model_id,
    write_json,
)

CORPUS = Path(__file__).resolve().parent / "corpus" / "llm_speed.json"
PID_FILE = ROOT / ".local" / "run" / "llm.pid"
SIZES = (2048, 4096, 8192)
HISTORY_REPLY = "Понял."
WARM_TURNS = (
    "Хорошо, а что дальше?",
    "Повтори главное одной фразой.",
    "Спасибо, продолжай.",
)
TURN_COLUMNS = (
    "turn | context | cache_n | prompt_n | prompt_ms | ttft_ms | "
    "predicted_per_second | rss_mb | vram_mb"
)
POINT_COLUMNS = "size | request | context | cache_n | prompt_n | prompt_ms | ttft_ms"

Message = dict[str, str]


def llm_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def rss_mb(pid: int | None) -> float | None:
    if pid is None:
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None


def vram_mb(pid: int | None) -> int | None:
    if pid is None:
        return None
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for line in out.splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) == 2 and fields[0] == str(pid):
            return int(fields[1])
    return None


def request(
    client: httpx.Client, messages: list[Message], kwargs: dict[str, bool]
) -> dict[str, Any]:
    thinking = kwargs["enable_thinking"]
    body = {
        "model": model_id(),
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "stream": True,
        "stream_options": {"include_usage": True},
        "cache_prompt": True,
        "max_tokens": 1024 if thinking else 96,
        "chat_template_kwargs": kwargs,
    }
    text = ""
    first_output_ms: float | None = None
    first_content_ms: float | None = None
    timings: dict[str, Any] = {}
    start = time.perf_counter()
    with client.stream("POST", f"{BASE_URL}/chat/completions", json=body) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[len("data: ") :])
            if chunk.get("timings"):
                timings = chunk["timings"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if (content or delta.get("reasoning_content")) and (
                    first_output_ms is None
                ):
                    first_output_ms = round((time.perf_counter() - start) * 1000, 1)
                if content:
                    if first_content_ms is None:
                        first_content_ms = round(
                            (time.perf_counter() - start) * 1000, 1
                        )
                    text += content
    pid = llm_pid()
    cache_n = timings.get("cache_n", 0)
    prompt_n = timings.get("prompt_n", 0)
    return {
        "text": text,
        "ttft_ms": first_output_ms if thinking else first_content_ms,
        "first_output_ms": first_output_ms,
        "first_content_ms": first_content_ms,
        "context": cache_n + prompt_n,
        "cache_n": cache_n,
        "prompt_n": prompt_n,
        "prompt_ms": round(timings.get("prompt_ms", 0.0), 1),
        "prompt_per_second": round(timings.get("prompt_per_second", 0.0), 1),
        "predicted_n": timings.get("predicted_n", 0),
        "predicted_per_second": round(timings.get("predicted_per_second", 0.0), 1),
        "rss_mb": rss_mb(pid),
        "vram_mb": vram_mb(pid),
    }


def count_tokens(
    client: httpx.Client, messages: list[Message], kwargs: dict[str, bool]
) -> int:
    resp = client.post(
        f"{SERVER}/apply-template",
        json={
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "chat_template_kwargs": kwargs,
        },
    )
    resp.raise_for_status()
    tokens = client.post(f"{SERVER}/tokenize", json={"content": resp.json()["prompt"]})
    tokens.raise_for_status()
    return len(tokens.json()["tokens"])


def history(corpus: dict[str, Any], system: str, n: int) -> list[Message]:
    messages = [{"role": "system", "content": system}]
    for turn in corpus["turns"][:n]:
        messages.append({"role": "user", "content": turn})
        messages.append({"role": "assistant", "content": HISTORY_REPLY})
    return messages


def turns(
    client: httpx.Client, corpus: dict[str, Any], kwargs: dict[str, bool]
) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": corpus["system"]}]
    rows: list[dict[str, Any]] = []
    print(TURN_COLUMNS, flush=True)
    for number, turn in enumerate(corpus["turns"], start=1):
        messages.append({"role": "user", "content": turn})
        row = {"turn": number, **request(client, messages, kwargs)}
        messages.append({"role": "assistant", "content": row["text"]})
        rows.append(row)
        print(
            " | ".join(
                str(row[k])
                for k in (
                    "turn",
                    "context",
                    "cache_n",
                    "prompt_n",
                    "prompt_ms",
                    "ttft_ms",
                    "predicted_per_second",
                    "rss_mb",
                    "vram_mb",
                )
            ),
            flush=True,
        )
    return rows


def point_row(row: dict[str, Any]) -> None:
    print(
        " | ".join(
            str(row[k])
            for k in (
                "size",
                "request",
                "context",
                "cache_n",
                "prompt_n",
                "prompt_ms",
                "ttft_ms",
            )
        ),
        flush=True,
    )


def points(
    client: httpx.Client, corpus: dict[str, Any], kwargs: dict[str, bool]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    print(POINT_COLUMNS, flush=True)
    for size in SIZES:
        system = f"[{uuid4().hex}] {corpus['system']}"
        counts = [
            (
                abs(
                    count_tokens(client, history(corpus, system, n)[:-1], kwargs) - size
                ),
                n,
            )
            for n in range(1, len(corpus["turns"]) + 1)
        ]
        n = min(counts)[1]
        messages = history(corpus, system, n)[:-1]
        row = {
            "size": size,
            "request": "cold",
            "turns": n,
            **request(client, messages, kwargs),
        }
        rows.append(row)
        point_row(row)
        for k, extra in enumerate(WARM_TURNS, start=1):
            messages = [
                *messages,
                {"role": "assistant", "content": row["text"]},
                {"role": "user", "content": extra},
            ]
            row = {
                "size": size,
                "request": f"warm-{k}",
                "turns": n,
                **request(client, messages, kwargs),
            }
            rows.append(row)
            point_row(row)
    return rows


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def summary(
    turn_rows: list[dict[str, Any]], point_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    warm = [
        r for r in point_rows if r["size"] == 8192 and r["request"].startswith("warm")
    ]
    ttft = median([r["ttft_ms"] for r in warm if r["ttft_ms"] is not None])
    ratio = median([r["cache_n"] / r["context"] for r in warm if r["context"]])
    decode = median([r["predicted_per_second"] for r in turn_rows])
    result = {
        "warm8k_ttft_ms": ttft,
        "warm8k_cache_ratio": ratio,
        "decode_tok_s": decode,
    }
    if point_rows:
        status = "PASS" if ttft is not None and ttft <= 300 else "FAIL"
        print(f"G4 TTFT {status} {ttft} (≤ 300)", flush=True)
    if turn_rows:
        status = "PASS" if decode is not None and decode >= 15 else "FAIL"
        print(f"G4 decode {status} {decode} (≥ 15)", flush=True)
    if point_rows:
        print(f"cache-hit 8K {ratio}", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="G4: TTFT and decode on 2K-8K context")
    parser.add_argument(
        "command", nargs="?", default="all", choices=["turns", "points", "all"]
    )
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="enable_thinking on, max_tokens 1024, TTFT by the first output",
    )
    args = parser.parse_args()
    kwargs = THINKING if args.thinking else KWARGS
    corpus = json.loads(CORPUS.read_text())
    turn_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=300.0) as client:
        if args.command in ("turns", "all"):
            turn_rows = turns(client, corpus, kwargs)
        if args.command in ("points", "all"):
            point_rows = points(client, corpus, kwargs)
    result = summary(turn_rows, point_rows)
    write_json(
        "speed_thinking" if args.thinking else "speed",
        {"turns": turn_rows, "points": point_rows, "summary": result},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
