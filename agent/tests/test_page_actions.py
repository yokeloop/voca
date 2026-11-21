import asyncio
import json
from typing import Any

from voca.clientio.actions import APPROVE, CANCEL, dispatch
from voca.conversation.approval import Choice


class Handlers:
    def __init__(self) -> None:
        self.approved: list[tuple[str, str, Choice]] = []
        self.cancelled: list[str] = []

    async def approve(
        self, key: str, request_id: str, choice: Choice
    ) -> dict[str, Any]:
        self.approved.append((key, request_id, choice))
        return {"ok": True}

    async def cancel(self, key: str) -> dict[str, Any]:
        self.cancelled.append(key)
        return {"ok": False, "error": "Поручение уже завершено"}


def run(method: str, payload: str, handlers: Handlers) -> Any:
    return json.loads(
        asyncio.run(dispatch(method, payload, handlers.approve, handlers.cancel))
    )


def test_dispatch_approve() -> None:
    handlers = Handlers()
    payload = '{"tool_call_id":"a","request_id":"p1","choice":"once"}'
    assert run(APPROVE, payload, handlers) == {"ok": True}
    assert handlers.approved == [("a", "p1", "once")]
    assert handlers.cancelled == []


def test_dispatch_cancel() -> None:
    handlers = Handlers()
    text = asyncio.run(
        dispatch(CANCEL, '{"tool_call_id":"a"}', handlers.approve, handlers.cancel)
    )
    assert json.loads(text) == {"ok": False, "error": "Поручение уже завершено"}
    assert "\\u" not in text
    assert handlers.cancelled == ["a"]


def test_dispatch_bad_payload() -> None:
    handlers = Handlers()
    for method, payload in (
        (APPROVE, "не json"),
        (APPROVE, "[]"),
        (APPROVE, '{"request_id":"p1","choice":"once"}'),
        (APPROVE, '{"tool_call_id":"","request_id":"p1","choice":"once"}'),
        (APPROVE, '{"tool_call_id":"a","request_id":"p1","choice":"always"}'),
        (APPROVE, '{"tool_call_id":"a","choice":"once"}'),
        (CANCEL, '{"tool_call_id":1}'),
        ("voca.other", '{"tool_call_id":"a"}'),
    ):
        assert run(method, payload, handlers) == {
            "ok": False,
            "error": "неверный запрос",
        }, payload
    assert handlers.approved == []
    assert handlers.cancelled == []
