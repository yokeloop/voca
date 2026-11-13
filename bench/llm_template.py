import argparse
import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from aiohttp import web
from livekit.agents import APIError, llm
from llm_common import (
    BASE_URL,
    KWARGS,
    MARKERS,
    OUT_DIR,
    SYSTEM_RU,
    TOOL_SCHEMAS,
    ChatResult,
    delegate,
    make_llm,
    model_id,
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

USER_PLAIN = "Привет! Как дела?"
USER_TASK = "Исследуй структуру проекта mypi и пришли сводку"
TOOL_OUTPUT = "Поручение принято, выполняется."

Check = Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]


def raw_stream(body: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    with (
        httpx.Client(timeout=120.0) as client,
        client.stream("POST", f"{BASE_URL}/chat/completions", json=body) as resp,
    ):
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunks.append(json.loads(line[len("data: ") :]))
    return chunks


def raw_body(chat_ctx: llm.ChatContext, tools: bool) -> dict[str, Any]:
    messages, _ = chat_ctx.to_provider_format("openai")
    body: dict[str, Any] = {
        "model": model_id(),
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": KWARGS,
    }
    if tools:
        body["tools"] = TOOL_SCHEMAS
    return body


def deltas(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c["choices"][0].get("delta") or {} for c in chunks if c.get("choices")]


def task_ctx() -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=SYSTEM_RU)
    ctx.add_message(role="user", content=USER_TASK)
    return ctx


def task_of(arguments: str) -> str:
    try:
        args = json.loads(arguments)
    except ValueError:
        return ""
    task = args.get("task") if isinstance(args, dict) else None
    return task.strip() if isinstance(task, str) else ""


async def kwargs_body(log: dict[str, Any]) -> tuple[bool, str]:
    bodies: list[dict[str, Any]] = []

    async def completions(request: web.Request) -> web.StreamResponse:
        bodies.append(await request.json())
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {
            "id": "stub",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model_id(),
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "ок"},
                    "finish_reason": "stop",
                }
            ],
        }
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="user", content=USER_PLAIN)
        result = await run_chat(
            make_llm(base_url=f"http://127.0.0.1:{port}/v1"), ctx, []
        )
    finally:
        await runner.cleanup()
    log["bodies"] = bodies
    log["result"] = result_dict(result)
    if len(bodies) != 1:
        return False, f"{len(bodies)} запросов"
    body = bodies[0]
    ok = body.get("chat_template_kwargs") == KWARGS and body.get("stream") is True
    return (
        ok,
        f"chat_template_kwargs={body.get('chat_template_kwargs')} stream={body.get('stream')}",
    )


async def agent_body(log: dict[str, Any]) -> tuple[bool, str]:
    bodies: list[dict[str, Any]] = []
    profile = Settings().llm_profile

    async def completions(request: web.Request) -> web.StreamResponse:
        bodies.append(await request.json())
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {
            "id": "stub",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": profile,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "ок"},
                    "finish_reason": "stop",
                }
            ],
        }
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    async def models(request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": profile}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    app.router.add_get("/v1/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="user", content=USER_PLAIN)
        loader = InstructionLoader(
            Path(__file__).resolve().parents[1] / "instructions.example"
        )
        instructions = loader.load()
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
            result = await run_chat(
                make_agent_llm(Settings(llm_url=f"http://127.0.0.1:{port}/v1")),
                ctx,
                agent.tools,
            )
            events.close()
    finally:
        await runner.cleanup()
    log["bodies"] = bodies
    log["result"] = result_dict(result)
    if len(bodies) != 1:
        return False, f"{len(bodies)} запросов"
    body = bodies[0]
    tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
    ok = (
        body.get("thinking_budget_tokens") == 128
        and "delegate" in tools
        and "chat_template_kwargs" not in body
        and body.get("stream") is True
    )
    detail = (
        f"thinking_budget_tokens={body.get('thinking_budget_tokens')} tools={tools} "
        f"chat_template_kwargs={'chat_template_kwargs' in body} "
        f"stream={body.get('stream')}"
    )
    return ok, detail


async def kwargs_live(log: dict[str, Any]) -> tuple[bool, str]:
    variants = {
        "base": KWARGS,
        "conversational_off": {"enable_thinking": False, "conversational_mode": False},
        "thinking_on": {"enable_thinking": True, "conversational_mode": True},
    }
    tokens: dict[str, int] = {}
    runs: dict[str, Any] = {}
    for name, kwargs in variants.items():
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="user", content=USER_PLAIN)
        result = await run_chat(make_llm(kwargs), ctx, [])
        tokens[name] = result.prompt_tokens
        runs[name] = {"kwargs": kwargs, **result_dict(result)}
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="user", content=USER_PLAIN)
    body = raw_body(ctx, tools=False)
    chunks = await asyncio.to_thread(raw_stream, body)
    reasoning = [
        d["reasoning_content"] for d in deltas(chunks) if d.get("reasoning_content")
    ]
    log["runs"] = runs
    log["raw"] = {"body": body, "chunks": chunks}
    ok = (
        tokens["base"] > tokens["conversational_off"]
        and tokens["thinking_on"] > tokens["base"]
        and not reasoning
    )
    detail = (
        f"prompt_tokens conversational on/off {tokens['base']}/{tokens['conversational_off']}, "
        f"thinking on/off {tokens['thinking_on']}/{tokens['base']}, "
        f"reasoning_content {len(reasoning)}"
    )
    return ok, detail


def tool_ok(result: ChatResult) -> tuple[bool, str]:
    calls = result.tool_calls
    if len(calls) != 1 or calls[0].name != "delegate":
        return False, f"вызовы {[c.name for c in calls]}"
    task = task_of(calls[0].arguments)
    if not task:
        return False, f"arguments {calls[0].arguments!r}"
    if MARKERS.search(result.text) or MARKERS.search(calls[0].arguments):
        return False, "маркеры шаблона"
    return True, f"task={task!r}"


async def tool_stream(log: dict[str, Any]) -> tuple[bool, str]:
    result = await run_chat(make_llm(), task_ctx(), [delegate])
    log["result"] = result_dict(result)
    ok, detail = tool_ok(result)
    body = raw_body(task_ctx(), tools=True)
    chunks = await asyncio.to_thread(raw_stream, body)
    ds = deltas(chunks)
    raw_calls = [d["tool_calls"] for d in ds if d.get("tool_calls")]
    content = "".join(d.get("content") or "" for d in ds)
    log["raw"] = {"body": body, "chunks": chunks}
    raw_ok = bool(raw_calls) and "<|tool_call>" not in content
    return (
        ok and raw_ok,
        f"{detail}; raw delta.tool_calls {len(raw_calls)}, content {content!r}",
    )


async def tool_cycle(log: dict[str, Any]) -> tuple[bool, str]:
    ctx = task_ctx()
    first = await run_chat(make_llm(), ctx, [delegate])
    log["first"] = result_dict(first)
    ok, detail = tool_ok(first)
    if not ok:
        return False, f"первый ход: {detail}"
    call = first.tool_calls[0]
    ctx.insert(
        [
            llm.FunctionCall(
                call_id=call.call_id, name=call.name, arguments=call.arguments
            ),
            llm.FunctionCallOutput(
                call_id=call.call_id, name=call.name, output=TOOL_OUTPUT, is_error=False
            ),
        ]
    )
    try:
        second = await run_chat(make_llm(), ctx, [delegate])
    except APIError as exc:
        log["error"] = repr(exc)
        return False, f"ошибка {exc!r}"
    log["second"] = result_dict(second)
    text = second.text.strip()
    ok = bool(text) and not MARKERS.search(text) and not second.tool_calls
    return ok, f"ответ {text!r}, вызовов {len(second.tool_calls)}"


CHECKS: dict[str, Check] = {
    "kwargs-body": kwargs_body,
    "agent-body": agent_body,
    "kwargs-live": kwargs_live,
    "tool-stream": tool_stream,
    "tool-cycle": tool_cycle,
}


async def run(names: list[str]) -> int:
    log: dict[str, Any] = {}
    failed = False
    for name in names:
        entry: dict[str, Any] = {}
        try:
            ok, detail = await CHECKS[name](entry)
        except (APIError, httpx.HTTPError) as exc:
            ok, detail = False, f"ошибка {exc!r}"
        entry["status"] = "PASS" if ok else "FAIL"
        entry["detail"] = detail
        log[name] = entry
        failed = failed or not ok
        print(f"{entry['status']} {name} {detail}", flush=True)
    agent_log = log.pop("agent-body", None)
    if agent_log is not None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "agent-body.json").write_text(
            json.dumps(agent_log, ensure_ascii=False, indent=2) + "\n"
        )
    if log:
        write_json("template", log)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="first-hour checks of the LLM stand")
    parser.add_argument("command", nargs="?", default="all", choices=[*CHECKS, "all"])
    args = parser.parse_args()
    names = list(CHECKS) if args.command == "all" else [args.command]
    return asyncio.run(run(names))


if __name__ == "__main__":
    raise SystemExit(main())
