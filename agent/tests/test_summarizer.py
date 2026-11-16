import asyncio
from typing import Any

import openai as openai_sdk
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from voca.conversation.summarizer import Failure, Summarizer, Summary


def completion(
    content: str | None,
    finish_reason: str = "stop",
    refusal: str | None = None,
    usage: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "glm-5.3-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": refusal,
                },
            }
        ],
    }
    if usage:
        body["usage"] = {
            "prompt_tokens": 3,
            "completion_tokens": 7,
            "total_tokens": 10,
        }
    return body


def ask(
    reply: dict[str, Any] | None,
    *,
    status: int = 200,
    delay: float = 0,
    requests: list[tuple[str | None, dict[str, Any]]] | None = None,
) -> Summary | Failure:
    async def chat(request: web.Request) -> web.Response:
        if requests is not None:
            requests.append(
                (request.headers.get("Authorization"), await request.json())
            )
        if delay:
            await asyncio.sleep(delay)
        if status != 200:
            return web.json_response({"error": {"message": "x"}}, status=status)
        return web.json_response(reply)

    async def main() -> Summary | Failure:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat)
        async with TestServer(app, host="127.0.0.1") as server:
            client = openai_sdk.AsyncOpenAI(
                api_key="k",
                base_url=str(server.make_url("/v1")),
                timeout=0.5,
                max_retries=0,
            )
            summarizer = Summarizer(client, model="glm-5.3-flash")
            result = await summarizer.summarize("P")
            await summarizer.close()
            return result

    return asyncio.run(main())


def test_summary_request_and_body() -> None:
    requests: list[tuple[str | None, dict[str, Any]]] = []
    result = ask(
        completion("<think>план</think>\n## О чём говорили\nТело"), requests=requests
    )
    assert isinstance(result, Summary)
    assert result.body == "## О чём говорили\nТело"
    assert result.tokens == 7
    assert result.ms >= 0
    assert len(requests) == 1
    authorization, body = requests[0]
    assert authorization == "Bearer k"
    assert body["model"] == "glm-5.3-flash"
    assert body["messages"] == [{"role": "user", "content": "P"}]
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body


def test_summary_tokens_without_usage() -> None:
    result = ask(completion("абвгде", usage=False))
    assert isinstance(result, Summary)
    assert result.tokens == 2


@pytest.mark.parametrize(
    ("reply", "status", "delay", "reason"),
    [
        (completion("тело", finish_reason="length"), 200, 0, "length"),
        (completion(""), 200, 0, "empty"),
        (completion("<think>x</think>"), 200, 0, "empty"),
        (completion(None, refusal="нет"), 200, 0, "refusal"),
        (completion("тело", finish_reason="content_filter"), 200, 0, "refusal"),
        (None, 500, 0, "http_500"),
        (None, 401, 0, "http_401"),
        (completion("тело"), 200, 2, "timeout"),
    ],
)
def test_summary_failures(
    reply: dict[str, Any] | None, status: int, delay: float, reason: str
) -> None:
    result = ask(reply, status=status, delay=delay)
    assert isinstance(result, Failure)
    assert result.reason == reason
    assert result.ms >= 0


def test_summary_connection_refused() -> None:
    async def main() -> Summary | Failure:
        client = openai_sdk.AsyncOpenAI(
            api_key="k",
            base_url="http://127.0.0.1:9/v1",
            timeout=0.5,
            max_retries=0,
        )
        summarizer = Summarizer(client, model="glm-5.3-flash")
        result = await summarizer.summarize("P")
        await summarizer.close()
        return result

    result = asyncio.run(main())
    assert isinstance(result, Failure)
    assert result.reason == "connection"


def test_no_client() -> None:
    async def main() -> None:
        summarizer = Summarizer(None, model="m")
        assert summarizer.ready is False
        assert await summarizer.summarize("P") == Failure("no_key", 0)
        await summarizer.close()

    asyncio.run(main())
