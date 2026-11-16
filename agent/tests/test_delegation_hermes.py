import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from voca.delegation.hermes import HermesBackend, map_run
from voca.delegation.model import TaskStatus
from voca.delegation.ports import (
    ApprovalNotActive,
    OutcomeUnknown,
    Rejected,
    RunNotFound,
    RunState,
)

Handler = Callable[[httpx.Request], httpx.Response]


def backend(
    handler: Handler, url: str = "https://h.example/"
) -> tuple[HermesBackend, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return HermesBackend(url, "k", transport=httpx.MockTransport(record)), requests


def reply(status: int, body: Any = None) -> Handler:
    return lambda _: httpx.Response(status, json=body)


def fail(error: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("boom", request=request)

    return handler


def error_body(code: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": code}}


async def closing[T](client: HermesBackend, call: Any) -> T:
    try:
        result: T = await call
        return result
    finally:
        await client.aclose()


def test_create_run_request() -> None:
    client, requests = backend(
        reply(202, {"run_id": "run_1", "status": "started", "replayed": False})
    )
    run_id = asyncio.run(closing(client, client.create_run("key-1", "сделай")))
    assert run_id == "run_1"
    (request,) = requests
    assert request.method == "POST"
    assert request.url.path == "/v1/runs"
    assert str(request.url) == "https://h.example/v1/runs"
    assert request.headers["Authorization"] == "Bearer k"
    assert request.headers["Idempotency-Key"] == "key-1"
    assert request.headers["X-Hermes-Session-Key"] == "voca"
    assert json.loads(request.content) == {"input": "сделай", "session_id": "key-1"}


@pytest.mark.parametrize(
    "handler",
    [
        reply(400, error_body("invalid_request")),
        reply(409, error_body("idempotency_key_conflict")),
        reply(429, error_body("rate_limit_exceeded")),
        reply(503, error_body("gateway_draining")),
        fail(httpx.ConnectError),
    ],
)
def test_create_run_rejected(handler: Handler) -> None:
    client, _ = backend(handler)
    with pytest.raises(Rejected):
        asyncio.run(closing(client, client.create_run("k", "t")))


def test_create_run_rejected_reason() -> None:
    client, _ = backend(reply(429, error_body("rate_limit_exceeded")))
    with pytest.raises(Rejected) as caught:
        asyncio.run(closing(client, client.create_run("k", "t")))
    assert "rate_limit_exceeded" in caught.value.reason
    assert caught.value.reason.startswith("429")


@pytest.mark.parametrize(
    "handler",
    [
        reply(500),
        reply(502),
        reply(504),
        fail(httpx.ReadTimeout),
        fail(httpx.RemoteProtocolError),
        reply(202, {"status": "started"}),
    ],
)
def test_create_run_outcome_unknown(handler: Handler) -> None:
    client, _ = backend(handler)
    with pytest.raises(OutcomeUnknown):
        asyncio.run(closing(client, client.create_run("k", "t")))


APPROVAL = {
    "description": "d",
    "command": "rm -r x",
    "choices": ["once", "deny"],
    "request_id": "r1",
}


@pytest.mark.parametrize(
    ("body", "stop_requested", "expected"),
    [
        ({"status": "started"}, False, RunState("r", TaskStatus.RUNNING)),
        ({"status": "queued"}, False, RunState("r", TaskStatus.RUNNING)),
        ({"status": "running"}, False, RunState("r", TaskStatus.RUNNING)),
        ({"status": "stopping"}, True, RunState("r", TaskStatus.RUNNING)),
        (
            {"status": "waiting_for_approval", "approval": APPROVAL},
            False,
            RunState("r", TaskStatus.WAITING_APPROVAL, approval=APPROVAL),
        ),
        (
            {"status": "completed", "output": "готово"},
            False,
            RunState("r", TaskStatus.SUCCEEDED, result="готово"),
        ),
        (
            {"status": "failed", "error": "boom"},
            False,
            RunState("r", TaskStatus.FAILED, error="boom"),
        ),
        (
            {"status": "cancelled"},
            True,
            RunState("r", TaskStatus.FAILED, error="timeout"),
        ),
        (
            {"status": "cancelled"},
            False,
            RunState("r", TaskStatus.FAILED, error="interrupted by gateway restart"),
        ),
        (
            {"status": "interrupted", "error": "owner died"},
            False,
            RunState("r", TaskStatus.FAILED, error="owner died"),
        ),
        (
            {"status": "interrupted"},
            False,
            RunState("r", TaskStatus.FAILED, error="interrupted"),
        ),
        (
            {"status": "unknown", "admission_id": "a1", "execution_generation": 2},
            False,
            RunState(
                "r",
                TaskStatus.UNKNOWN,
                admission={"admission_id": "a1", "execution_generation": 2},
            ),
        ),
    ],
)
def test_map_run_all_statuses(
    body: dict[str, Any], stop_requested: bool, expected: RunState
) -> None:
    assert map_run({"run_id": "r", **body}, stop_requested) == expected


def test_map_run_unknown_status() -> None:
    with pytest.raises(OutcomeUnknown):
        map_run({"run_id": "r", "status": "teleported"}, False)


def test_get_run_not_found() -> None:
    client, requests = backend(reply(404, error_body("run_not_found")))
    with pytest.raises(RunNotFound):
        asyncio.run(closing(client, client.get_run("run_1", False)))
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/runs/run_1"
    client, _ = backend(reply(500))
    with pytest.raises(OutcomeUnknown):
        asyncio.run(closing(client, client.get_run("run_1", False)))
    client, _ = backend(reply(200, {"run_id": "run_1", "status": "running"}))
    state = asyncio.run(closing(client, client.get_run("run_1", False)))
    assert state == RunState("run_1", TaskStatus.RUNNING)


def test_stop_responses() -> None:
    client, requests = backend(reply(200, {"run_id": "run_1", "status": "stopping"}))
    assert asyncio.run(closing(client, client.stop("run_1"))) is None
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v1/runs/run_1/stop"
    assert requests[0].content == b""
    client, _ = backend(
        reply(200, {"run_id": "run_1", "status": "completed", "output": "успел"})
    )
    assert asyncio.run(closing(client, client.stop("run_1"))) == RunState(
        "run_1", TaskStatus.SUCCEEDED, result="успел"
    )
    client, _ = backend(reply(409, error_body("run_not_active")))
    assert asyncio.run(closing(client, client.stop("run_1"))) is None
    client, _ = backend(reply(200, {"run_id": "run_1", "status": "cancelled"}))
    assert asyncio.run(closing(client, client.stop("run_1"))) == RunState(
        "run_1", TaskStatus.FAILED, error="timeout"
    )


def test_approve_requests() -> None:
    client, requests = backend(reply(200, {"ok": True}))

    async def main() -> None:
        await client.approve("run_1", "once", "r1")
        await client.approve("run_1", "deny", None)
        await client.aclose()

    asyncio.run(main())
    assert [request.url.path for request in requests] == [
        "/v1/runs/run_1/approval",
        "/v1/runs/run_1/approval",
    ]
    assert json.loads(requests[0].content) == {"choice": "once", "request_id": "r1"}
    assert json.loads(requests[1].content) == {"choice": "deny"}
    client, _ = backend(reply(409, error_body("approval_not_pending")))
    with pytest.raises(ApprovalNotActive):
        asyncio.run(closing(client, client.approve("run_1", "once", "r1")))


def test_resolve_unknown_body() -> None:
    admission = {"admission_id": "a1", "execution_generation": 2}
    client, requests = backend(reply(200, {"outcome": "interrupted"}))
    asyncio.run(closing(client, client.resolve_unknown("run_1", admission)))
    assert requests[0].url.path == "/v1/runs/run_1/resolve-unknown"
    assert json.loads(requests[0].content) == admission
