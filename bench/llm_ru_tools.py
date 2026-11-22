import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from livekit.agents import llm
from livekit.plugins import openai
from llm_common import (
    BASE_URL,
    MARKERS,
    SYSTEM_RU,
    THINKING,
    ChatResult,
    delegate,
    make_llm,
    result_dict,
    run_chat,
    write_json,
)

from voca.app.wiring import make_llm as make_agent_llm
from voca.clientio.cards import CardState
from voca.config import Settings
from voca.conversation.agent import VocaAgent
from voca.conversation.instructions import InstructionLoader
from voca.telemetry.jsonl import JsonlLog

CORPUS = Path(__file__).resolve().parent / "corpus"
CYRILLIC_MIN = 0.8
INSTRUCTIONS = Path(__file__).resolve().parents[1] / "instructions.example"
CLAIMS = re.compile(
    r"(?i)отправил|отменил|отменено|изменю|уточнил|подтвердил|подтверждено|отклонил|отклонено"
)
LOOKUPS = {
    "c05": ["list_tasks", "list_notifications"],
    "c08": ["list_tasks", "list_notifications"],
    "c09": ["list_tasks"],
}


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


async def dialogs(model: openai.LLM, name: str) -> bool:
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
    write_json(name, {"passed": passed, "total": len(corpus), "dialogs": log})
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


def history_items(history: list[dict[str, Any]]) -> list[llm.ChatItem]:
    items: list[llm.ChatItem] = []
    for n, entry in enumerate(history):
        if "call" not in entry:
            items.append(
                llm.ChatMessage(role=entry["role"], content=[entry["content"]])
            )
            continue
        call_id = f"bench_{n}"
        output = entry["output"]
        items.append(
            llm.FunctionCall(
                call_id=call_id,
                name=entry["call"],
                arguments=json.dumps(entry["arguments"], ensure_ascii=False),
            )
        )
        items.append(
            llm.FunctionCallOutput(
                call_id=call_id,
                name=entry["call"],
                output=output
                if isinstance(output, str)
                else json.dumps(output, ensure_ascii=False),
                is_error=False,
            )
        )
    return items


def tool_ok(item: dict[str, Any], result: ChatResult) -> bool:
    names = [call.name for call in result.tool_calls]
    if item["expect"] != "none":
        return bool(names) and names[0] == item["expect"]
    allowed = LOOKUPS.get(item["id"], [])
    if any(name not in allowed for name in names):
        return False
    if names:
        return True
    return (
        russian(result.text)
        and not CLAIMS.search(result.text)
        and "просит подтверждения" not in result.text
    )


async def tool_case(
    model: openai.LLM, agent: VocaAgent, item: dict[str, Any]
) -> dict[str, Any]:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=agent.instructions)
    ctx.items.extend(history_items(item["history"]))
    ctx.add_message(role="user", content=item["user"])
    result = await run_chat(model, ctx, agent.tools)
    ok = tool_ok(item, result)
    return {
        "id": item["id"],
        "user": item["user"],
        "expect": item["expect"],
        "status": "PASS" if ok else "FAIL",
        "calls": [
            {"name": call.name, "arguments": call.arguments}
            for call in result.tool_calls
        ],
        "answer": answer(result),
    }


async def tool_cases(name: str) -> bool:
    corpus = json.loads((CORPUS / "llm_tools.json").read_text())
    loader = InstructionLoader(INSTRUCTIONS)
    instructions = loader.load()
    model = make_agent_llm(Settings(llm_url=BASE_URL))
    log: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as state:
        events = JsonlLog(Path(state) / "events.jsonl", session="bench")
        agent = VocaAgent(
            loader=loader,
            instructions=instructions,
            log=events,
            cards=CardState(instructions),
            ctx_high=9830,
            ctx_low=6553,
        )
        for item in corpus:
            entry = await tool_case(model, agent, item)
            log.append(entry)
            print(f"{entry['status']} {item['id']} {item['expect']}", flush=True)
        events.close()
    passed = sum(e["status"] == "PASS" for e in log)
    print(f"tools {passed}/{len(corpus)}", flush=True)
    write_json(name, {"passed": passed, "total": len(corpus), "cases": log})
    return passed == len(corpus)


async def run(command: str) -> int:
    model = make_llm()
    ok = True
    if command in ("dialogs", "all"):
        ok = await dialogs(model, "dialogs") and ok
    if command in ("delegate", "all"):
        ok = await scenarios(model, "delegate") and ok
    if command in ("tools", "all"):
        ok = await tool_cases("tools") and ok
    if command == "dialogs-thinking":
        ok = await dialogs(make_llm(THINKING), "dialogs_thinking")
    if command == "delegate-thinking":
        thinking = make_llm(THINKING)
        ok = await scenarios(thinking, "delegate_thinking")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="G3: Russian dialogs and delegate")
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=[
            "dialogs",
            "delegate",
            "tools",
            "dialogs-thinking",
            "delegate-thinking",
            "all",
        ],
    )
    args = parser.parse_args()
    return asyncio.run(run(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
