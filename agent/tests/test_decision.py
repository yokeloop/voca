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
    request_body,
)

PROMPT = DecisionConfig().prompt()
SITUATION = Situation(
    task="Удали каталог /tmp/x",
    request="удаление в корневом каталоге",
    command="rm -r /tmp/x",
    announcement="Hermes просит подтверждения",
    phrase="Подтверждаю.",
)

Handler = Callable[[httpx.Request], httpx.Response]


def answer(
    choice: str = "approve", confidence: float = 0.9, noul: float = 0.1
) -> dict[str, object]:
    return {
        "answers": {
            "verdict": {"type": "choice", "choice": choice, "confidence": confidence},
            "extra": {"type": "noul", "noul": noul},
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
    result = decide(lambda _: httpx.Response(200, json=answer("deny", 0.8, 0.7)))
    assert isinstance(result, Decision)
    assert (result.verdict, result.picked, result.confidence, result.extra) == (
        "deny",
        "deny",
        0.8,
        True,
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
