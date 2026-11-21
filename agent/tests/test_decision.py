import asyncio
import json
from collections.abc import Callable

import httpx

from voca.config import DecisionConfig
from voca.conversation.decision import DecisionClient
from voca.conversation.verdict import (
    Decision,
    DecisionFailure,
    Situation,
    Sorted,
    SorterPrompt,
    request_body,
    sorter_body,
)

PROMPT = DecisionConfig().prompt()
SITUATION = Situation(
    task="Удали каталог /tmp/x",
    request="удаление в корневом каталоге",
    command="rm -r /tmp/x",
    announcement="Hermes просит подтверждения",
    phrase="Подтверждаю.",
)

SORTER = SorterPrompt(
    instructions="Что это за фраза пользователя?",
    criteria={"noise": "шум", "reply": "реплика"},
    keep_words=frozenset({"нет"}),
    max_words=4,
    min_words=2,
)
STATE = {"последняя фраза агента": "Привет.", "фраза пользователя": "Угу."}

Handler = Callable[[httpx.Request], httpx.Response]


def answer(choice: str = "approve", confidence: float = 0.9) -> dict[str, object]:
    return {
        "answers": {
            "verdict": {"type": "choice", "choice": choice, "confidence": confidence},
        }
    }


def decide(handler: Handler) -> Decision | DecisionFailure:
    async def main() -> Decision | DecisionFailure:
        client = DecisionClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://d"
            ),
            model="d1-2B",
            prompt=PROMPT,
        )
        try:
            return await client.decide(SITUATION)
        finally:
            await client.aclose()

    return asyncio.run(main())


def test_decide_posts_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer())

    decide(handler)
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/systemone"
    assert json.loads(seen[0].content) == request_body(PROMPT, SITUATION, "d1-2B")


def test_decide_ok() -> None:
    result = decide(lambda _: httpx.Response(200, json=answer("deny_more", 0.8)))
    assert isinstance(result, Decision)
    assert (result.verdict, result.picked, result.confidence) == (
        "deny_more",
        "deny_more",
        0.8,
    )


def test_decide_low_confidence_unclear() -> None:
    result = decide(lambda _: httpx.Response(200, json=answer("approve", 0.3)))
    assert isinstance(result, Decision)
    assert result.verdict == "unclear"
    assert result.picked == "approve"


def test_decide_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = decide(handler)
    assert isinstance(result, DecisionFailure)
    assert result.reason == "timeout"


def test_decide_connection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    result = decide(handler)
    assert isinstance(result, DecisionFailure)
    assert result.reason == "connection"


def test_decide_http_error() -> None:
    result = decide(lambda _: httpx.Response(500, text="boom"))
    assert isinstance(result, DecisionFailure)
    assert result.reason == "http_500"


def test_decide_bad_json() -> None:
    result = decide(lambda _: httpx.Response(200, text="not json"))
    assert isinstance(result, DecisionFailure)
    assert result.reason == "bad_answer"
    result = decide(lambda _: httpx.Response(200, json={"answers": {}}))
    assert isinstance(result, DecisionFailure)
    assert result.reason == "bad_answer"


def test_decide_disabled() -> None:
    async def main() -> Decision | DecisionFailure:
        client = DecisionClient(None, model="d1-2B", prompt=PROMPT)
        result = await client.decide(SITUATION)
        await client.aclose()
        return result

    assert asyncio.run(main()) == DecisionFailure("disabled", 0)


def sort_answer(choice: str = "noise", confidence: float = 0.9) -> dict[str, object]:
    return {
        "answers": {
            "kind": {"type": "choice", "choice": choice, "confidence": confidence},
        }
    }


def sort(
    transport: httpx.AsyncBaseTransport, timeout_s: float = 0.3
) -> Sorted | DecisionFailure:
    async def main() -> Sorted | DecisionFailure:
        client = DecisionClient(
            httpx.AsyncClient(transport=transport, base_url="http://d"),
            model="d1-2B",
            prompt=PROMPT,
            sorter=SORTER,
            sorter_timeout_s=timeout_s,
        )
        try:
            return await client.sort(STATE)
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
        off = DecisionClient(None, model="d1-2B", prompt=PROMPT, sorter=SORTER)
        bare = DecisionClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(500)),
                base_url="http://d",
            ),
            model="d1-2B",
            prompt=PROMPT,
        )
        try:
            return [await off.sort(STATE), await bare.sort(STATE)]
        finally:
            await bare.aclose()

    assert asyncio.run(main()) == [DecisionFailure("disabled", 0)] * 2
