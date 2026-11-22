import json
from collections.abc import Awaitable, Callable
from typing import Any

from livekit import rtc

from voca.delegation.model import Choice

APPROVE = "voca.approve"
CANCEL = "voca.cancel_task"
BAD_REQUEST = "неверный запрос"
CHOICES: tuple[Choice, ...] = ("once", "deny")

Approve = Callable[[str, str, Choice], Awaitable[dict[str, Any]]]
Cancel = Callable[[str], Awaitable[dict[str, Any]]]


async def dispatch(method: str, payload: str, approve: Approve, cancel: Cancel) -> str:
    bad = json.dumps({"ok": False, "error": BAD_REQUEST}, ensure_ascii=False)
    try:
        request = json.loads(payload)
    except ValueError:
        return bad
    if not isinstance(request, dict):
        return bad
    key = request.get("tool_call_id")
    if not isinstance(key, str) or not key:
        return bad
    if method == APPROVE:
        request_id = request.get("request_id")
        choice = request.get("choice")
        if not isinstance(request_id, str) or choice not in CHOICES:
            return bad
        return json.dumps(await approve(key, request_id, choice), ensure_ascii=False)
    if method == CANCEL:
        return json.dumps(await cancel(key), ensure_ascii=False)
    return bad


def bind_actions(room: rtc.Room, approve: Approve, cancel: Cancel) -> None:
    for name in (APPROVE, CANCEL):

        async def handler(data: rtc.RpcInvocationData, name: str = name) -> str:
            return await dispatch(name, data.payload, approve, cancel)

        room.local_participant.register_rpc_method(name, handler)
