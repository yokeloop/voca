import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from livekit.agents import function_tool, llm
from livekit.plugins import openai

BASE_URL = "http://127.0.0.1:8081/v1"
SERVER = BASE_URL.removesuffix("/v1")
DEFAULT_MODEL = "bolt-e4b"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".local" / "bench" / "llm"
KWARGS = {"enable_thinking": False, "conversational_mode": True}
THINKING = {"enable_thinking": True, "conversational_mode": True}
SYSTEM_RU = (
    "Ты — VOCA, голосовой ассистент. Отвечай по-русски. Поручения, которые требуют "
    "работы с проектами, файлами, поиском или долгой работы, передавай инструментом "
    "delegate. Если неясно, что поручить, задай уточняющий вопрос."
)
MARKERS = re.compile(r"<\||\|>|<channel|<turn|<tool_call|<think|</think")


@functools.cache
def model_id() -> str:
    return str(httpx.get(f"{BASE_URL}/models", timeout=10).json()["data"][0]["id"])


def model_dir(base: Path) -> Path:
    return base if model_id() == DEFAULT_MODEL else base / model_id()


@function_tool
async def delegate(task: str) -> str:
    """Поручить задачу основному агенту Hermes: работа с проектами, файлами, поиском, заметками и всё, что требует времени. task — полная формулировка поручения по-русски."""
    raise NotImplementedError


TOOL_SCHEMAS = llm.ToolContext([delegate]).parse_function_tools("openai", strict=True)


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[llm.FunctionToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    prompt_cached_tokens: int = 0
    completion_tokens: int = 0


def make_llm(kwargs: dict[str, bool] = KWARGS, base_url: str = BASE_URL) -> openai.LLM:
    return openai.LLM(
        model=model_id(),
        base_url=base_url,
        api_key="none",
        extra_body={"chat_template_kwargs": kwargs},
    )


async def run_chat(
    model: openai.LLM, chat_ctx: llm.ChatContext, tools: list[llm.Tool]
) -> ChatResult:
    result = ChatResult()
    async with model.chat(chat_ctx=chat_ctx, tools=tools) as stream:
        async for chunk in stream:
            if chunk.delta is not None:
                result.text += chunk.delta.content or ""
                result.tool_calls.extend(chunk.delta.tool_calls)
            if chunk.usage is not None:
                result.prompt_tokens = chunk.usage.prompt_tokens
                result.prompt_cached_tokens = chunk.usage.prompt_cached_tokens
                result.completion_tokens = chunk.usage.completion_tokens
    return result


def result_dict(result: ChatResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "tool_calls": [c.model_dump() for c in result.tool_calls],
        "prompt_tokens": result.prompt_tokens,
        "prompt_cached_tokens": result.prompt_cached_tokens,
        "completion_tokens": result.completion_tokens,
    }


def write_json(name: str, data: object) -> Path:
    out_dir = model_dir(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return path
