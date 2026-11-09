import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from livekit.agents import llm
from livekit.plugins import openai
from llm_common import (
    MARKERS,
    SYSTEM_RU,
    ChatResult,
    delegate,
    make_llm,
    result_dict,
    run_chat,
    write_json,
)

CORPUS = Path(__file__).resolve().parent / "corpus"
CYRILLIC_MIN = 0.8


def cyrillic(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters)


def russian(text: str) -> bool:
    return (
        bool(text.strip())
        and not MARKERS.search(text)
        and cyrillic(text) >= CYRILLIC_MIN
    )


def answer(result: ChatResult) -> dict[str, Any]:
    return {
        **result_dict(result),
        "markers": MARKERS.findall(result.text),
        "cyrillic": round(cyrillic(result.text), 3),
        "chars": len(result.text),
        "tool_called": bool(result.tool_calls),
    }


def base_ctx() -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=SYSTEM_RU)
    return ctx


async def dialogs(model: openai.LLM) -> bool:
    corpus = json.loads((CORPUS / "llm_dialogs.json").read_text())
    log: list[dict[str, Any]] = []
    passed = 0
    for dialog in corpus:
        ctx = base_ctx()
        answers: list[dict[str, Any]] = []
        for turn in dialog["turns"]:
            ctx.add_message(role="user", content=turn)
            result = await run_chat(model, ctx, [delegate])
            ctx.add_message(role="assistant", content=result.text)
            answers.append({"user": turn, **answer(result)})
        ok = all(russian(a["text"]) and not a["tool_called"] for a in answers)
        passed += ok
        log.append(
            {"id": dialog["id"], "status": "PASS" if ok else "FAIL", "answers": answers}
        )
        print(f"{'PASS' if ok else 'FAIL'} {dialog['id']}", flush=True)
    write_json("dialogs", {"passed": passed, "total": len(corpus), "dialogs": log})
    return passed == len(corpus)


def call_ok(result: ChatResult) -> bool:
    if len(result.tool_calls) != 1 or result.tool_calls[0].name != "delegate":
        return False
    arguments = result.tool_calls[0].arguments
    try:
        args = json.loads(arguments)
    except ValueError:
        return False
    task = args.get("task") if isinstance(args, dict) else None
    return (
        isinstance(task, str)
        and russian(task)
        and not MARKERS.search(result.text)
        and not MARKERS.search(arguments)
    )


async def scenario(model: openai.LLM, item: dict[str, Any]) -> dict[str, Any]:
    ctx = base_ctx()
    ctx.add_message(role="user", content=item["user"])
    first = await run_chat(model, ctx, [delegate])
    entry: dict[str, Any] = {**item, "first": answer(first)}
    kind = item["kind"]
    if kind == "refuse":
        ok = not first.tool_calls and russian(first.text)
    elif kind == "incomplete":
        ok = not first.tool_calls and "?" in first.text
    else:
        ok = call_ok(first)
        if ok:
            call = first.tool_calls[0]
            output = item["tool_output"]
            ctx.insert(
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
            second = await run_chat(model, ctx, [delegate])
            entry["second"] = answer(second)
            ok = russian(second.text) and not second.tool_calls
    entry["status"] = "PASS" if ok else "FAIL"
    return entry


async def scenarios(model: openai.LLM, name: str) -> bool:
    corpus = json.loads((CORPUS / "llm_delegate.json").read_text())
    log: list[dict[str, Any]] = []
    for item in corpus:
        entry = await scenario(model, item)
        log.append(entry)
        print(f"{entry['status']} {item['id']} {item['kind']}", flush=True)
    passed = sum(e["status"] == "PASS" for e in log)
    print(f"delegate {passed}/{len(corpus)}", flush=True)
    write_json(name, {"passed": passed, "total": len(corpus), "scenarios": log})
    return passed == len(corpus)


async def run(command: str) -> int:
    model = make_llm()
    ok = True
    if command in ("dialogs", "all"):
        ok = await dialogs(model) and ok
    if command in ("delegate", "all"):
        ok = await scenarios(model, "delegate") and ok
    if command == "delegate-thinking":
        thinking = make_llm({"enable_thinking": True, "conversational_mode": True})
        ok = await scenarios(thinking, "delegate_thinking")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="G3: Russian dialogs and delegate")
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["dialogs", "delegate", "delegate-thinking", "all"],
    )
    args = parser.parse_args()
    return asyncio.run(run(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
