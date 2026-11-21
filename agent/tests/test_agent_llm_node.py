import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from livekit.agents import RunContext, ToolError, llm
from livekit.agents.voice import Agent, ModelSettings

from voca.clientio.cards import CardState
from voca.config import ApprovalConfig
from voca.conversation.agent import VocaAgent
from voca.conversation.instructions import InstructionLoader
from voca.delegation.model import Task, TaskStatus
from voca.delegation.service import DelegationService
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"
USAGE = llm.CompletionUsage(completion_tokens=3, prompt_tokens=900, total_tokens=903)

VOCABULARY = ApprovalConfig().vocabulary()


def make_agent(tmp_path: Path, *, delegation: Any = None) -> VocaAgent:
    loader = InstructionLoader(EXAMPLE)
    instructions = loader.load()
    return VocaAgent(
        vocabulary=VOCABULARY,
        loader=loader,
        instructions=instructions,
        log=JsonlLog(tmp_path / "events.jsonl", session="t"),
        cards=CardState(instructions),
        ctx_high=9830,
        ctx_low=6553,
        delegation=cast(DelegationService | None, delegation),
    )


def content_chunk(text: str) -> llm.ChatChunk:
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))


def collect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    items: list[llm.ChatChunk | str],
) -> list[llm.ChatChunk | str]:
    async def fake(
        agent: Agent, chat_ctx: Any, tools: Any, model_settings: Any
    ) -> AsyncIterator[llm.ChatChunk | str]:
        for item in items:
            yield item

    monkeypatch.setattr(Agent.default, "llm_node", fake)

    async def main() -> list[llm.ChatChunk | str]:
        node = make_agent(tmp_path).llm_node(
            llm.ChatContext.empty(), [], ModelSettings()
        )
        assert isinstance(node, AsyncIterable)
        return [item async for item in node]

    return asyncio.run(main())


def text_of(items: list[Any]) -> str:
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif item.delta is not None and item.delta.content:
            parts.append(item.delta.content)
    return "".join(parts)


def test_tool_call_markup_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    usage_chunk = llm.ChatChunk(id="c", usage=USAGE)
    out = collect(
        monkeypatch,
        tmp_path,
        [
            content_chunk("Сейчас <|tool_ca"),
            content_chunk('ll_start|>[delegate(task="x")]<|tool_call_'),
            content_chunk("end|> посмотрю."),
            usage_chunk,
        ],
    )
    assert text_of(out) == "Сейчас  посмотрю."
    assert "<|" not in text_of(out)
    assert out[-1] is usage_chunk


def test_cut_stream_tail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = collect(monkeypatch, tmp_path, [content_chunk("Готово <|tool_c")])
    assert text_of(out) == "Готово <|tool_c"
    out = collect(
        monkeypatch,
        tmp_path,
        [content_chunk("Готово <|tool_call_start|>[delegate(task=")],
    )
    assert text_of(out) == "Готово "


def context(call_id: str) -> RunContext[Any]:
    return cast(
        RunContext[Any], SimpleNamespace(function_call=SimpleNamespace(call_id=call_id))
    )


def test_delegate_tool_without_service(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    tools = [tool for tool in agent.tools if isinstance(tool, llm.FunctionTool)]
    tool = next(tool for tool in tools if tool.id == "delegate")
    schema = llm.utils.build_legacy_openai_schema(tool)
    assert list(schema["function"]["parameters"]["properties"]) == ["task"]
    with pytest.raises(ToolError, match="Делегирование сейчас недоступно"):
        asyncio.run(agent.delegate(context("call-1"), "x"))


class StubService:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.never = asyncio.Event()

    async def delegate(self, key: str, text: str) -> Task:
        self.keys.append(key)
        return Task(key, text, TaskStatus.RUNNING, created_at=0.0, run_id="run_1")

    async def watch(self, key: str) -> Task:
        await self.never.wait()
        raise AssertionError(key)


def test_delegate_tool_accepts(tmp_path: Path) -> None:
    service = StubService()
    agent = make_agent(tmp_path, delegation=service)

    async def main() -> str:
        reply = await agent.delegate(context("call-1"), "x")
        assert service.keys == ["call-1"]
        assert len(agent.background) == 1
        await agent.stop_delegation()
        return str(reply.output)

    assert json.loads(asyncio.run(main())) == {"id": "call-1", "event": "submitted"}
