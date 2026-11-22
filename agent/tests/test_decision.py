import asyncio
import json
from collections.abc import Callable

import httpx

from voca.config import DecisionConfig
from voca.conversation.decision import DecisionClient
from voca.conversation.verdict import (
    DecisionFailure,
    Sorted,
    sorter_body,
)

SORTER = DecisionConfig().sorter_prompt()
STATE = {"последняя фраза агента": "Привет.", "фраза пользователя": "Угу."}

Handler = Callable[[httpx.Request], httpx.Response]


def sort_answer(choice: str = "noise", confidence: float = 0.9) -> dict[str, object]:
    return {
        "answers": {
            "kind": {"type": "choice", "choice": choice, "confidence": confidence},
        }
    }


def sort(
    transport: httpx.AsyncBaseTransport, timeout_s: float = 0.3, requests: int = 0
) -> Sorted | DecisionFailure:
    async def main() -> Sorted | DecisionFailure:
        client = DecisionClient(
            httpx.AsyncClient(transport=transport, base_url="http://d"),
            model="d1-2B",
            sorter=SORTER,
            sorter_timeout_s=timeout_s,
        )
        try:
            return await client.sort(STATE, requests)
        finally:
            await client.aclose()

    return asyncio.run(main())


def test_sort_posts_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=sort_answer())

    sort(httpx.MockTransport(handler))
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/systemone"
    assert json.loads(seen[0].content) == sorter_body(SORTER, STATE, "d1-2B")


def test_sort_with_requests_posts_questions() -> None:
    seen: list[httpx.Request] = []
    data = sort_answer("reply", 0.9)
    answers = data["answers"]
    assert isinstance(answers, dict)
    answers["relation"] = {"type": "choice", "choice": "answers", "confidence": 0.8}
    answers["answer"] = {"type": "choice", "choice": "approve", "confidence": 0.7}
    answers["target"] = {"type": "choice", "choice": "request_2", "confidence": 0.6}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=data)

    result = sort(httpx.MockTransport(handler), requests=2)
    body = json.loads(seen[0].content)
    assert list(body["questions"]) == ["kind", "relation", "answer", "target"]
    assert body == sorter_body(SORTER, STATE, "d1-2B", 2)
    assert result == Sorted(
        "reply",
        0.9,
        result.ms if isinstance(result, Sorted) else -1,
        relation="answers",
        relation_confidence=0.8,
        answer="approve",
        answer_confidence=0.7,
        target=2,
        target_confidence=0.6,
    )


def test_sort_ok() -> None:
    result = sort(
        httpx.MockTransport(lambda _: httpx.Response(200, json=sort_answer("reply")))
    )
    assert isinstance(result, Sorted)
    assert (result.kind, result.confidence) == ("reply", 0.9)


def test_sort_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = sort(httpx.MockTransport(handler))
    assert isinstance(result, DecisionFailure)
    assert result.reason == "timeout"


def test_sort_deadline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=sort_answer())

    result = sort(httpx.MockTransport(handler), timeout_s=0.05)
    assert isinstance(result, DecisionFailure)
    assert result.reason == "timeout"
    assert result.ms < 1000


def test_sort_disabled() -> None:
    async def main() -> list[Sorted | DecisionFailure]:
        off = DecisionClient(None, model="d1-2B", sorter=SORTER)
        bare = DecisionClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(500)),
                base_url="http://d",
            ),
            model="d1-2B",
        )
        try:
            return [await off.sort(STATE), await bare.sort(STATE)]
        finally:
            await bare.aclose()

    assert asyncio.run(main()) == [DecisionFailure("disabled", 0)] * 2
