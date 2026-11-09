import argparse
import asyncio
import json
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livekit.agents import llm
from livekit.plugins import openai
from llm_common import MARKERS, SYSTEM_RU, delegate, make_llm, write_json
from llm_ru_tools import cyrillic, russian
from llm_speed import llm_pid, rss_mb, vram_mb

CORPUS = Path(__file__).resolve().parent / "corpus"
THINKING = {"enable_thinking": True, "conversational_mode": True}
SYSTEM_A = (
    "Ты — VOCA, голосовой ассистент. Скажи пользователю одну короткую нейтральную "
    "фразу-подтверждение по-русски, например «Сейчас» или «Секунду, смотрю». "
    "Не отвечай на вопрос и не называй никаких фактов."
)
NOTE_SAID = (
    "Ты уже произнёс короткую фразу выше. Теперь продолжи ответ по существу, "
    "не повторяя её."
)
NOTE_PARALLEL = (
    "Пока ты думаешь, пользователь слышит короткую фразу-подтверждение. Ответь по "
    "существу, не начиная с подтверждения."
)
TOOL_OK = "Поручение принято, выполняется."


@dataclass
class Timed:
    text: str = ""
    tool_calls: list[llm.FunctionToolCall] = field(default_factory=list)
    first_content_ms: float | None = None
    first_output_ms: float | None = None
    total_ms: float = 0.0
    completion_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [c.model_dump() for c in self.tool_calls],
            "first_content_ms": self.first_content_ms,
            "first_output_ms": self.first_output_ms,
            "total_ms": self.total_ms,
            "completion_tokens": self.completion_tokens,
        }


async def timed_chat(
    model: openai.LLM, ctx: llm.ChatContext, tools: list[llm.Tool], start: float
) -> Timed:
    result = Timed()
    async with model.chat(chat_ctx=ctx, tools=tools) as stream:
        async for chunk in stream:
            now = round((time.perf_counter() - start) * 1000, 1)
            if chunk.delta is not None:
                if chunk.delta.content:
                    result.text += chunk.delta.content
                    if result.first_content_ms is None:
                        result.first_content_ms = now
                result.tool_calls.extend(chunk.delta.tool_calls)
                if (chunk.delta.content or chunk.delta.tool_calls) and (
                    result.first_output_ms is None
                ):
                    result.first_output_ms = now
            if chunk.usage is not None:
                result.completion_tokens = chunk.usage.completion_tokens
    result.total_ms = round((time.perf_counter() - start) * 1000, 1)
    return result


def ctx_of(system: str, history: list[tuple[str, str]], user: str) -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=system)
    for role, content in history:
        ctx.add_message(role=role, content=content)
    ctx.add_message(role="user", content=user)
    return ctx


def norm(text: str) -> str:
    return re.sub(r"[^\w ]", "", text.lower()).strip()


@dataclass
class Turn:
    a: Timed
    b: Timed
    after_tool: Timed | None
    b_first_content_ms: float | None
    tool_output: str | None

    @property
    def spoken_b(self) -> str:
        return " ".join(
            t
            for t in (self.b.text.strip(), (self.after_tool or Timed()).text.strip())
            if t
        )

    @property
    def answer(self) -> str:
        return " ".join(t for t in (self.a.text.strip(), self.spoken_b) if t)

    def as_dict(self) -> dict[str, Any]:
        spoken_b = self.spoken_b
        return {
            "a": self.a.as_dict(),
            "b": self.b.as_dict(),
            "after_tool": self.after_tool.as_dict() if self.after_tool else None,
            "tool_output": self.tool_output,
            "b_first_content_ms": self.b_first_content_ms,
            "answer": self.answer,
            "markers": MARKERS.findall(self.answer),
            "cyrillic": round(cyrillic(self.answer), 3),
            "chars": len(self.answer),
            "b_repeats_a": bool(norm(self.a.text))
            and norm(self.a.text) in norm(spoken_b),
        }


async def two_turn(
    fast: openai.LLM,
    slow: openai.LLM,
    history: list[tuple[str, str]],
    user: str,
    parallel: bool,
    tool_output: str | None = TOOL_OK,
) -> Turn:
    start_a = time.perf_counter()
    a_task = asyncio.create_task(
        timed_chat(fast, ctx_of(SYSTEM_A, history, user), [], start_a)
    )
    if parallel:
        ctx_b = ctx_of(SYSTEM_RU, history, user)
        ctx_b.add_message(role="system", content=NOTE_PARALLEL)
        start_b = time.perf_counter()
        b = await timed_chat(slow, ctx_b, [delegate], start_b)
        a = await a_task
    else:
        a = await a_task
        ctx_b = ctx_of(SYSTEM_RU, history, user)
        ctx_b.add_message(role="assistant", content=a.text)
        ctx_b.add_message(role="system", content=NOTE_SAID)
        start_b = time.perf_counter()
        b = await timed_chat(slow, ctx_b, [delegate], start_b)
    after_tool = None
    first_content = b.first_content_ms
    output = None
    if b.tool_calls and tool_output is not None:
        call = b.tool_calls[0]
        output = tool_output
        ctx_b.insert(
            [
                llm.FunctionCall(
                    call_id=call.call_id, name=call.name, arguments=call.arguments
                ),
                llm.FunctionCallOutput(
                    call_id=call.call_id,
                    name=call.name,
                    output=output,
                    is_error=output.startswith("Ошибка"),
                ),
            ]
        )
        after_tool = await timed_chat(slow, ctx_b, [delegate], start_b)
        if first_content is None:
            first_content = after_tool.first_content_ms
    return Turn(a, b, after_tool, first_content, output)


def scenarios() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(
        (CORPUS / "llm_delegate.json").read_text()
    )
    return loaded


def median(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 1) if present else None


async def timing(label: str) -> int:
    fast, slow = make_llm(), make_llm(THINKING)
    rows: list[dict[str, Any]] = []
    print(
        "mode | id | a_ttft | a_total | b_first_output | b_first_content | b_total",
        flush=True,
    )
    for parallel in (True, False):
        mode = "parallel" if parallel else "serial"
        for item in scenarios():
            turn = await two_turn(
                fast, slow, [], item["user"], parallel, item["tool_output"]
            )
            last = turn.after_tool or turn.b
            row = {
                "mode": mode,
                "id": item["id"],
                "a_ttft_ms": turn.a.first_content_ms,
                "a_total_ms": turn.a.total_ms,
                "b_first_output_ms": turn.b.first_output_ms,
                "b_first_content_ms": turn.b_first_content_ms,
                "b_total_ms": last.total_ms,
                **turn.as_dict(),
            }
            rows.append(row)
            print(
                f"{mode} | {item['id']} | {row['a_ttft_ms']} | {row['a_total_ms']} | "
                f"{row['b_first_output_ms']} | {row['b_first_content_ms']} | {row['b_total_ms']}",
                flush=True,
            )
    pid = llm_pid()
    summary: dict[str, Any] = {
        "label": label,
        "vram_mb": vram_mb(pid),
        "rss_mb": rss_mb(pid),
    }
    for mode in ("parallel", "serial"):
        part = [r for r in rows if r["mode"] == mode]
        summary[mode] = {
            k: median([r[k] for r in part])
            for k in (
                "a_ttft_ms",
                "a_total_ms",
                "b_first_output_ms",
                "b_first_content_ms",
                "b_total_ms",
            )
        }
        print(f"{label} {mode} medians {summary[mode]}", flush=True)
    print(
        f"{label} vram_mb {summary['vram_mb']} rss_mb {summary['rss_mb']}", flush=True
    )
    write_json(f"two_turn_timing_{label}", {"summary": summary, "rows": rows})
    return 0


async def dialogs(fast: openai.LLM, slow: openai.LLM) -> int:
    corpus = json.loads((CORPUS / "llm_dialogs.json").read_text())
    log: list[dict[str, Any]] = []
    passed = 0
    for dialog in corpus:
        history: list[tuple[str, str]] = []
        turns: list[dict[str, Any]] = []
        ok = True
        for user in dialog["turns"]:
            turn = await two_turn(
                fast, slow, history, user, parallel=False, tool_output=None
            )
            history += [("user", user), ("assistant", turn.answer)]
            entry = {"user": user, **turn.as_dict()}
            turns.append(entry)
            ok = ok and russian(turn.answer) and not turn.b.tool_calls
        passed += ok
        log.append(
            {"id": dialog["id"], "status": "PASS" if ok else "FAIL", "turns": turns}
        )
        print(f"{'PASS' if ok else 'FAIL'} {dialog['id']}", flush=True)
    print(f"dialogs {passed}/{len(corpus)}", flush=True)
    write_json(
        "two_turn_dialogs", {"passed": passed, "total": len(corpus), "dialogs": log}
    )
    return passed


def task_ok(turn: Turn) -> bool:
    calls = turn.b.tool_calls
    if len(calls) != 1 or calls[0].name != "delegate":
        return False
    try:
        args = json.loads(calls[0].arguments)
    except ValueError:
        return False
    task = args.get("task") if isinstance(args, dict) else None
    return (
        isinstance(task, str)
        and russian(task)
        and not MARKERS.search(calls[0].arguments)
    )


async def delegate_scenarios(fast: openai.LLM, slow: openai.LLM) -> int:
    log: list[dict[str, Any]] = []
    for item in scenarios():
        turn = await two_turn(fast, slow, [], item["user"], False, item["tool_output"])
        kind = item["kind"]
        if kind == "refuse":
            ok = not turn.b.tool_calls and russian(turn.answer)
        elif kind == "incomplete":
            ok = not turn.b.tool_calls and "?" in turn.answer
        else:
            after = turn.after_tool
            ok = (
                task_ok(turn)
                and after is not None
                and russian(after.text)
                and not after.tool_calls
                and not MARKERS.search(turn.answer)
            )
        status = "PASS" if ok else "FAIL"
        log.append({**item, "status": status, **turn.as_dict()})
        print(f"{status} {item['id']} {kind}", flush=True)
    passed = sum(e["status"] == "PASS" for e in log)
    print(f"delegate {passed}/{len(log)}", flush=True)
    write_json(
        "two_turn_delegate", {"passed": passed, "total": len(log), "scenarios": log}
    )
    return passed


async def run(command: str, label: str) -> int:
    if command == "timing":
        return await timing(label)
    fast, slow = make_llm(), make_llm(THINKING)
    if command in ("dialogs", "g3"):
        await dialogs(fast, slow)
    if command in ("delegate", "g3"):
        await delegate_scenarios(fast, slow)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="two-turn mechanic: fast phrase A, then B"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="g3",
        choices=["timing", "dialogs", "delegate", "g3"],
    )
    parser.add_argument("--label", default="np1")
    args = parser.parse_args()
    return asyncio.run(run(args.command, args.label))


if __name__ == "__main__":
    raise SystemExit(main())
