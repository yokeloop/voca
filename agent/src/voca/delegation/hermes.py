from typing import Any, Literal

import httpx

from voca.delegation import model
from voca.delegation.model import GATEWAY_RESTART, TaskStatus
from voca.delegation.ports import (
    ApprovalNotActive,
    OutcomeUnknown,
    Rejected,
    RunNotFound,
    RunState,
)

SESSION_KEY = "voca"
HERMES_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
TIMEOUT = httpx.Timeout(10.0, connect=3.0)
RUNNING = frozenset({"started", "queued", "running", "stopping"})


def map_run(body: dict[str, Any], stop_requested: bool) -> RunState:
    run_id = str(body.get("run_id", ""))
    status = body.get("status")
    if status in RUNNING:
        return RunState(run_id, TaskStatus.RUNNING)
    if status == "waiting_for_approval":
        approval = body.get("approval")
        return RunState(
            run_id,
            TaskStatus.WAITING_APPROVAL,
            approval=approval if isinstance(approval, dict) else None,
        )
    if status == "completed":
        return RunState(run_id, TaskStatus.SUCCEEDED, result=body.get("output") or "")
    if status in ("failed", "interrupted"):
        error = str(body["error"]) if body.get("error") else status
        return RunState(run_id, TaskStatus.FAILED, error=error)
    if status == "cancelled":
        error = model.TIMEOUT if stop_requested else GATEWAY_RESTART
        return RunState(run_id, TaskStatus.FAILED, error=error)
    if status == "unknown":
        admission = {
            "admission_id": body.get("admission_id"),
            "execution_generation": body.get("execution_generation"),
        }
        return RunState(run_id, TaskStatus.UNKNOWN, admission=admission)
    raise OutcomeUnknown(f"статус {status!r}")


def error_code(response: httpx.Response) -> str:
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return str(response.status_code)
    return f"{response.status_code} {code}"


def body_of(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise OutcomeUnknown(f"{response.status_code}: тело не JSON") from error
    if not isinstance(body, dict):
        raise OutcomeUnknown(f"{response.status_code}: тело не объект")
    return body


class HermesBackend:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client = httpx.AsyncClient(
            base_url=url.strip().rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT,
            transport=transport,
        )

    async def _send(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return await self.client.request(method, path, json=json)
        except httpx.TransportError as error:
            raise OutcomeUnknown(type(error).__name__) from error

    async def create_run(self, key: str, text: str) -> str:
        try:
            response = await self.client.post(
                "/v1/runs",
                json={"input": text, "session_id": key},
                headers={"Idempotency-Key": key, "X-Hermes-Session-Key": SESSION_KEY},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            raise Rejected("connect") from error
        except httpx.TransportError as error:
            raise OutcomeUnknown(type(error).__name__) from error
        if response.is_success:
            run_id = body_of(response).get("run_id")
            if not isinstance(run_id, str):
                raise OutcomeUnknown(f"{response.status_code}: нет run_id")
            return run_id
        if response.is_client_error or response.status_code == 503:
            raise Rejected(error_code(response))
        raise OutcomeUnknown(error_code(response))

    async def get_run(self, run_id: str, stop_requested: bool) -> RunState:
        response = await self._send("GET", f"/v1/runs/{run_id}")
        if response.status_code == 200:
            return map_run(body_of(response), stop_requested)
        if response.status_code == 404:
            raise RunNotFound(error_code(response))
        raise OutcomeUnknown(error_code(response))

    async def stop(self, run_id: str) -> RunState | None:
        response = await self._send("POST", f"/v1/runs/{run_id}/stop")
        if response.status_code == 200:
            body = body_of(response)
            if body.get("status") in HERMES_TERMINAL:
                return map_run(body, stop_requested=True)
            return None
        if response.status_code == 409:
            return None
        if response.status_code == 404:
            raise RunNotFound(error_code(response))
        raise OutcomeUnknown(error_code(response))

    async def approve(
        self, run_id: str, choice: Literal["once", "deny"], request_id: str | None
    ) -> None:
        body: dict[str, Any] = {"choice": choice}
        if request_id is not None:
            body["request_id"] = request_id
        response = await self._send("POST", f"/v1/runs/{run_id}/approval", body)
        if response.status_code == 200:
            return
        if response.status_code == 409:
            raise ApprovalNotActive(error_code(response))
        if response.is_client_error:
            raise Rejected(error_code(response))
        raise OutcomeUnknown(error_code(response))

    async def resolve_unknown(self, run_id: str, admission: dict[str, Any]) -> None:
        response = await self._send(
            "POST", f"/v1/runs/{run_id}/resolve-unknown", admission
        )
        if response.status_code != 200:
            raise OutcomeUnknown(error_code(response))

    async def aclose(self) -> None:
        await self.client.aclose()
