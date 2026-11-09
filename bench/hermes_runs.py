import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

SESSION_KEY = "voca"
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
STATE_DIR = Path(__file__).resolve().parents[1] / ".local" / "bench" / "hermes"
STATE_FILE = STATE_DIR / "state.json"
LONG_TASK = (
    "Выполни в терминале команду `sleep {n}` и после неё ответь одним словом: готово."
)
ENV_HINT = (
    "VOCA_HERMES_URL and VOCA_HERMES_KEY are required: "
    "cp .env.example .env, fill them, then set -a; . ./.env; set +a"
)


@dataclass(frozen=True)
class Config:
    url: str
    key: str

    @classmethod
    def from_env(cls) -> "Config":
        url = os.environ.get("VOCA_HERMES_URL", "").strip().rstrip("/")
        key = os.environ.get("VOCA_HERMES_KEY", "").strip()
        if not url or not key:
            print(ENV_HINT, file=sys.stderr)
            raise SystemExit(2)
        return cls(url=url, key=key)


class Fail(Exception):
    pass


def check(cond: bool, phase: str, msg: str) -> None:
    if not cond:
        raise Fail(f"{phase}: {msg}")


def make_client(cfg: Config) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.url,
        headers={"Authorization": f"Bearer {cfg.key}"},
        timeout=httpx.Timeout(30.0, connect=3.0),
    )


def body_of(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def trace(record: dict[str, Any], resp: httpx.Response, body: Any = None) -> Any:
    data = body_of(resp)
    record.setdefault("requests", []).append(
        {
            "method": resp.request.method,
            "path": resp.request.url.path,
            "params": dict(resp.request.url.params),
            "headers": sorted(
                k for k in resp.request.headers if k.lower() != "authorization"
            ),
            "body": body,
            "status": resp.status_code,
            "response_headers": {
                k: v
                for k, v in resp.headers.items()
                if k.lower().startswith(("idempotency", "x-hermes"))
            },
            "response": data,
        }
    )
    return data


def save_log(phase: str, record: dict[str, Any]) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = STATE_DIR / f"{stamp}-{phase}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    data: dict[str, Any] = json.loads(STATE_FILE.read_text())
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def post_run(
    client: httpx.Client,
    body: dict[str, Any],
    *,
    idem_key: str | None,
    session_key: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    headers: dict[str, str] = {}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    if session_key:
        headers["X-Hermes-Session-Key"] = SESSION_KEY
    headers.update(extra_headers or {})
    return client.post("/v1/runs", json=body, headers=headers)


def get_run(client: httpx.Client, run_id: str) -> httpx.Response:
    return client.get(f"/v1/runs/{run_id}")


def wait_terminal(
    client: httpx.Client,
    run_id: str,
    deadline_s: float,
    poll_s: float = 2.0,
    allow_unknown: bool = False,
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    while True:
        resp = get_run(client, run_id)
        elapsed = time.monotonic() - started
        check(resp.status_code == 200, "wait", f"GET {run_id} -> {resp.status_code}")
        data: dict[str, Any] = resp.json()
        status = data.get("status")
        if status in TERMINAL or (allow_unknown and status == "unknown"):
            return data, elapsed
        if elapsed >= deadline_s:
            raise Fail(f"wait: {run_id} still {status} after {elapsed:.0f}s")
        time.sleep(poll_s)


def iter_events(
    client: httpx.Client, run_id: str, *, last_seq: int | None = None, deadline_s: float
) -> Iterator[dict[str, Any]]:
    params = {"last_seq": last_seq} if last_seq is not None else None
    started = time.monotonic()
    with client.stream(
        "GET",
        f"/v1/runs/{run_id}/events",
        params=params,
        timeout=httpx.Timeout(30.0, connect=3.0, read=None),
    ) as resp:
        if resp.status_code != 200:
            raise Fail(f"events: GET /v1/runs/{run_id}/events -> {resp.status_code}")
        data: list[str] = []
        for line in resp.iter_lines():
            if time.monotonic() - started > deadline_s:
                return
            if line == "":
                if data:
                    yield json.loads("\n".join(data))
                    data = []
                continue
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))


def runtime_of(data: dict[str, Any]) -> str:
    runtime = data.get("runtime") or {}
    return f"{runtime.get('provider')}/{runtime.get('model')}"


def admitted(
    phase: str, resp: httpx.Response, record: dict[str, Any], body: Any
) -> dict[str, Any]:
    data = trace(record, resp, body)
    check(resp.status_code == 202, phase, f"POST /v1/runs -> {resp.status_code} {data}")
    result: dict[str, Any] = data
    return result


def complete(
    client: httpx.Client,
    phase: str,
    record: dict[str, Any],
    body: dict[str, Any],
    *,
    session_key: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    resp = post_run(
        client,
        body,
        idem_key=uuid4().hex,
        session_key=session_key,
        extra_headers=extra_headers,
    )
    run_id = admitted(phase, resp, record, body)["run_id"]
    final, elapsed = wait_terminal(client, run_id, 300)
    record.setdefault("runs", []).append(
        {"run_id": run_id, "final": final, "seconds": elapsed}
    )
    check(
        final.get("status") == "completed",
        phase,
        f"{run_id} ended {final.get('status')}",
    )
    return final


def phase_preflight(client: httpx.Client, record: dict[str, Any]) -> str:
    resp = client.get("/health")
    health = trace(record, resp)
    check(resp.status_code == 200, "preflight", f"/health -> {resp.status_code}")
    resp = client.get("/v1/models")
    models = trace(record, resp)
    check(resp.status_code == 200, "preflight", f"/v1/models -> {resp.status_code}")
    resp = client.get("/v1/capabilities")
    caps = trace(record, resp)
    check(
        resp.status_code == 200, "preflight", f"/v1/capabilities -> {resp.status_code}"
    )
    features = caps.get("features") or {}
    idem = features.get("runs_idempotency") or {}
    check(
        features.get("run_submission") is True,
        "preflight",
        "run_submission is not true",
    )
    check(
        idem.get("supported") is True,
        "preflight",
        "runs_idempotency.supported is not true",
    )
    check(
        idem.get("durable") is True, "preflight", "runs_idempotency.durable is not true"
    )
    state = load_state()
    state["retention_seconds"] = idem.get("retention_seconds")
    state["run_unknown_resolution"] = features.get("run_unknown_resolution")
    save_state(state)
    ids = ",".join(m.get("id", "?") for m in models.get("data", []))
    return (
        f"version={health.get('version')} models={ids} "
        f"retention_seconds={state['retention_seconds']} "
        f"run_unknown_resolution={state['run_unknown_resolution']}"
    )


def phase_create(client: httpx.Client, record: dict[str, Any]) -> str:
    idem = uuid4().hex
    body = {
        "input": LONG_TASK.format(n=30),
        "session_id": f"voca-g7-{uuid4().hex[:12]}",
    }
    data = admitted("create", post_run(client, body, idem_key=idem), record, body)
    run_id = data.get("run_id", "")
    check(data.get("replayed") is False, "create", f"replayed={data.get('replayed')}")
    check(str(run_id).startswith("run_"), "create", f"run_id={run_id}")
    state = load_state()
    state["detach"] = {"idem": idem, "body": body, "run_id": run_id}
    save_state(state)
    for event in iter_events(client, run_id, deadline_s=90):
        record.setdefault("events", []).append(event)
        name = event.get("event")
        check(name != "approval.request", "create", f"approval requested: {event}")
        if name == "tool.started":
            state["detach"]["last_seq"] = event["seq"]
            save_state(state)
            return f"run_id={run_id} detached at seq={event['seq']} tool={event.get('tool')}"
    raise Fail("create: no tool.started within 90s")


def phase_resume(client: httpx.Client, record: dict[str, Any]) -> str:
    state = load_state()
    detach = state.get("detach") or {}
    check("last_seq" in detach, "resume", "no detached run in state: run create first")
    resp = post_run(client, detach["body"], idem_key=detach["idem"])
    data = admitted("resume", resp, record, detach["body"])
    check(data.get("replayed") is True, "resume", f"replayed={data.get('replayed')}")
    check(
        resp.headers.get("Idempotency-Replayed") == "true",
        "resume",
        "no Idempotency-Replayed: true header",
    )
    check(
        data.get("run_id") == detach["run_id"], "resume", f"run_id {data.get('run_id')}"
    )
    resp = get_run(client, detach["run_id"])
    first = trace(record, resp)
    check(resp.status_code == 200, "resume", f"GET run -> {resp.status_code}")
    if first.get("status") in TERMINAL:
        raise Fail("run finished before resume; raise sleep")
    seqs: list[int] = []
    for event in iter_events(
        client, detach["run_id"], last_seq=detach["last_seq"], deadline_s=300
    ):
        record.setdefault("events", []).append(event)
        if "seq" in event:
            seqs.append(event["seq"])
        if event.get("event") == "run.completed":
            break
    check(bool(seqs), "resume", "no events after last_seq")
    check(
        seqs[0] > detach["last_seq"],
        "resume",
        f"first seq {seqs[0]} <= {detach['last_seq']}",
    )
    check(
        all(a < b for a, b in pairwise(seqs)),
        "resume",
        f"seq {seqs}",
    )
    final, _ = wait_terminal(client, detach["run_id"], 300)
    record["final"] = final
    check(final.get("status") == "completed", "resume", f"ended {final.get('status')}")
    check(bool(final.get("output")), "resume", "empty output")
    state["completed_run_id"] = detach["run_id"]
    save_state(state)
    return (
        f"run_id={detach['run_id']} replayed=true status_on_return={first.get('status')} "
        f"events={len(seqs)} seq={seqs[0]}..{seqs[-1]} completed "
        f"runtime={runtime_of(final)} output={final.get('output')!r}"
    )


def phase_conflict(client: httpx.Client, record: dict[str, Any]) -> str:
    detach = load_state().get("detach") or {}
    check("idem" in detach, "conflict", "no detached run in state: run create first")
    body = {**detach["body"], "input": "Ответь одним словом: другое."}
    resp = post_run(client, body, idem_key=detach["idem"])
    data = trace(record, resp, body)
    check(resp.status_code == 409, "conflict", f"POST -> {resp.status_code}")
    error = data.get("error") if isinstance(data, dict) else None
    code = (error or {}).get("code") if isinstance(error, dict) else None
    code = code or (data.get("code") if isinstance(data, dict) else None)
    check(code == "idempotency_key_conflict", "conflict", f"code={code}")
    return f"409 {code}"


def phase_session(client: httpx.Client, record: dict[str, Any]) -> str:
    sid = f"voca-g7-{uuid4().hex[:12]}"
    word = uuid4().hex[:8]
    complete(
        client,
        "session",
        record,
        {
            "input": f"Запомни кодовое слово {word}. Ответь одним словом: ок.",
            "session_id": sid,
        },
    )
    final = complete(
        client,
        "session",
        record,
        {
            "input": "Какое кодовое слово я просил запомнить? Ответь только им.",
            "session_id": sid,
        },
    )
    check(
        word in str(final.get("output")),
        "session",
        f"{word} not in {final.get('output')!r}",
    )
    check(
        final.get("session_id") == sid,
        "session",
        f"session_id={final.get('session_id')}",
    )
    sid2 = f"voca-g7-{uuid4().hex[:12]}"
    other = complete(
        client,
        "session",
        record,
        {"input": "Ответь одним словом: да."},
        session_key=False,
        extra_headers={"X-Hermes-Session-Id": sid2},
    )
    honored = other.get("session_id") == sid2
    return (
        f"word recalled via body.session_id={sid} "
        f"header_honored={honored} (run C session_id={other.get('session_id')})"
    )


def phase_delegate(client: httpx.Client, record: dict[str, Any]) -> str:
    nonce = uuid4().hex[:12]
    body = {
        "input": (
            'Вызови инструмент note сервера mypi с аргументами: kind "journal", '
            f'scope {{"type": "task", "key": "VOCA-3"}}, '
            f'text {{"text": "VOCA-3 G7 delegate {nonce}"}}. '
            "После вызова ответь одним словом: записано."
        )
    }
    run_id = admitted(
        "delegate", post_run(client, body, idem_key=uuid4().hex), record, body
    )["run_id"]
    started: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    for event in iter_events(client, run_id, deadline_s=180):
        record.setdefault("events", []).append(event)
        tool = str(event.get("tool", ""))
        if event.get("event") == "tool.started" and "mypi" in tool and "note" in tool:
            started.append(event)
        if event.get("event") == "tool.completed" and "mypi" in tool and "note" in tool:
            completed.append(event)
        if event.get("event") == "run.completed":
            break
    check(bool(started), "delegate", "no tool.started of the mypi note tool")
    check(
        any(c.get("tool") == started[0].get("tool") for c in completed),
        "delegate",
        "no tool.completed of the same tool",
    )
    final, _ = wait_terminal(client, run_id, 180)
    record["final"] = final
    check(
        final.get("status") == "completed", "delegate", f"ended {final.get('status')}"
    )
    preview = str(completed[0].get("preview") or "")
    if len(preview) > 20:
        check(
            preview not in str(final.get("output")),
            "delegate",
            "tool preview leaked to output",
        )
    return f"nonce={nonce} run_id={run_id} tool={started[0].get('tool')} output={final.get('output')!r}"


def phase_restart_start(client: httpx.Client, record: dict[str, Any]) -> str:
    idem = uuid4().hex
    body = {
        "input": (
            "Выполни в терминале три команды по очереди, каждую отдельным вызовом: "
            "`sleep 240`, `sleep 240`, `sleep 240`. После них ответь одним словом: готово."
        ),
        "session_id": f"voca-g7-{uuid4().hex[:12]}",
    }
    run_id = admitted("restart", post_run(client, body, idem_key=idem), record, body)[
        "run_id"
    ]
    state = load_state()
    state["restart"] = {"idem": idem, "body": body, "run_id": run_id}
    save_state(state)
    for event in iter_events(client, run_id, deadline_s=90):
        record.setdefault("events", []).append(event)
        check(
            event.get("event") != "approval.request",
            "restart",
            f"approval requested: {event}",
        )
        if event.get("event") == "tool.started":
            return f"run_id={run_id} running tool={event.get('tool')}; now restart the gateway"
    raise Fail("restart: no tool.started within 90s")


def phase_restart_verify(client: httpx.Client, record: dict[str, Any]) -> str:
    state = load_state()
    restart = state.get("restart") or {}
    check(
        "run_id" in restart,
        "restart",
        "no restart run in state: run --phase start first",
    )
    started = time.monotonic()
    while True:
        try:
            if client.get("/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        check(
            time.monotonic() - started < 180, "restart", "/health not 200 within 180s"
        )
        time.sleep(2)
    run_id = restart["run_id"]
    resp = get_run(client, run_id)
    data = trace(record, resp)
    check(resp.status_code == 200, "restart", f"GET run -> {resp.status_code}")
    status = data.get("status")
    check(
        status in ("cancelled", "interrupted", "unknown"), "restart", f"status={status}"
    )
    facts = f"run_id={run_id} status={status} error={data.get('error')!r}"
    if status == "unknown":
        check(
            bool(state.get("run_unknown_resolution")),
            "restart",
            "unknown without resolution",
        )
        payload = {k: data.get(k) for k in ("admission_id", "execution_generation")}
        resp = client.post(f"/v1/runs/{run_id}/resolve-unknown", json=payload)
        resolved = trace(record, resp, payload)
        check(
            resp.status_code == 200, "restart", f"resolve-unknown -> {resp.status_code}"
        )
        check(
            resolved.get("outcome") == "interrupted", "restart", f"outcome={resolved}"
        )
        facts += " resolved=interrupted"
    resp = post_run(client, restart["body"], idem_key=restart["idem"])
    again = admitted("restart", resp, record, restart["body"])
    check(again.get("replayed") is True, "restart", f"replayed={again.get('replayed')}")
    check(again.get("run_id") == run_id, "restart", f"second run {again.get('run_id')}")
    with client.stream("GET", f"/v1/runs/{run_id}/events") as events:
        events_code = events.status_code
    record["events_status"] = events_code
    resp = get_run(client, state.get("completed_run_id", ""))
    done = trace(record, resp)
    check(
        resp.status_code == 200, "restart", f"completed run GET -> {resp.status_code}"
    )
    check(
        done.get("status") == "completed",
        "restart",
        f"completed run is {done.get('status')}",
    )
    check(bool(done.get("output")), "restart", "completed run lost its output")
    return (
        f"{facts} replay=same run_id events={events_code} "
        f"previous {state['completed_run_id']} completed output={done.get('output')!r}"
    )


def phase_backend_down(
    client: httpx.Client, record: dict[str, Any], deadline: float
) -> str:
    body = {
        "input": "Ответь одним словом: да.",
        "provider": "voca-unreachable",
        "model": "unreachable",
    }
    run_id = admitted(
        "backend-down", post_run(client, body, idem_key=uuid4().hex), record, body
    )["run_id"]
    started = time.monotonic()
    try:
        final, elapsed = wait_terminal(client, run_id, deadline)
    except Fail:
        elapsed = time.monotonic() - started
        resp = client.post(f"/v1/runs/{run_id}/stop")
        trace(record, resp)
        stopped, _ = wait_terminal(client, run_id, 60)
        record["final"] = stopped
        raise Fail(
            f"backend-down: not terminal after {elapsed:.0f}s; /stop -> {stopped.get('status')}"
        ) from None
    record["final"] = final
    status = final.get("status")
    check(status in ("completed", "failed"), "backend-down", f"status={status}")
    outcome = "completed via fallback" if status == "completed" else "failed"
    return (
        f"run_id={run_id} {outcome} after {elapsed:.0f}s "
        f"error={final.get('error')!r} output={final.get('output')!r}"
    )


def run_phase(
    cfg: Config, name: str, fn: Callable[[httpx.Client, dict[str, Any]], str]
) -> bool:
    record: dict[str, Any] = {"phase": name, "url": cfg.url}
    try:
        with make_client(cfg) as client:
            facts = fn(client, record)
    except (Fail, httpx.HTTPError) as exc:
        record["fail"] = str(exc)
        save_log(name, record)
        print(f"FAIL {name}: {exc}", flush=True)
        return False
    record["pass"] = facts
    save_log(name, record)
    print(f"PASS {name} {facts}", flush=True)
    return True


def run_all(cfg: Config) -> int:
    if not run_phase(cfg, "preflight", phase_preflight):
        return 1
    for sub in ("create", "resume"):
        if subprocess.run([sys.executable, __file__, sub], check=False).returncode != 0:
            return 1
    for name, fn in (("conflict", phase_conflict), ("session", phase_session)):
        if not run_phase(cfg, name, fn):
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="G7: Hermes Runs API preflight (VOCA-3)"
    )
    sub = parser.add_subparsers(dest="command")
    for name in (
        "preflight",
        "create",
        "resume",
        "conflict",
        "session",
        "all",
        "delegate",
    ):
        sub.add_parser(name)
    restart = sub.add_parser("restart")
    restart.add_argument("--phase", choices=("start", "verify"), required=True)
    down = sub.add_parser("backend-down")
    down.add_argument("--deadline", type=float, default=600.0)
    args = parser.parse_args(argv)
    cfg = Config.from_env()
    command = args.command or "all"
    phases: dict[str, Callable[[httpx.Client, dict[str, Any]], str]] = {
        "preflight": phase_preflight,
        "create": phase_create,
        "resume": phase_resume,
        "conflict": phase_conflict,
        "session": phase_session,
        "delegate": phase_delegate,
    }
    if command == "all":
        return run_all(cfg)
    if command == "restart":
        fn = phase_restart_start if args.phase == "start" else phase_restart_verify
        return 0 if run_phase(cfg, f"restart-{args.phase}", fn) else 1
    if command == "backend-down":
        deadline: float = args.deadline
        return (
            0
            if run_phase(cfg, command, lambda c, r: phase_backend_down(c, r, deadline))
            else 1
        )
    return 0 if run_phase(cfg, command, phases[command]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
