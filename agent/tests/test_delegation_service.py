import asyncio
from dataclasses import replace
from typing import Any, Literal

import pytest

from voca.delegation.model import Task, TaskStatus
from voca.delegation.ports import (
    ApprovalNotActive,
    NotSteerable,
    OutcomeUnknown,
    Rejected,
    RunNotFound,
    RunState,
    TaskNotFound,
    TaskNotOpen,
    Unavailable,
)
from voca.delegation.service import DelegationService

APPROVAL = {
    "description": "d",
    "command": "rm -r x",
    "choices": ["once", "deny"],
    "request_id": "r1",
}
AUTHORITY_APPROVAL = {**APPROVAL, "request_id": "p1", "execution_generation": 3}


class FakeRepo:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    async def insert(self, task: Task) -> tuple[Task, bool]:
        if task.tool_call_id in self.tasks:
            return self.tasks[task.tool_call_id], False
        self.tasks[task.tool_call_id] = task
        return task, True

    async def save(self, task: Task) -> None:
        self.tasks[task.tool_call_id] = task

    async def get(self, key: str) -> Task | None:
        return self.tasks.get(key)

    async def list_open(self) -> list[Task]:
        found = [task for task in self.tasks.values() if task.is_open]
        return sorted(found, key=lambda task: task.created_at)

    async def list_undelivered(self) -> list[Task]:
        found = [
            task
            for task in self.tasks.values()
            if not task.is_open and task.delivered_at is None
        ]
        return sorted(found, key=lambda task: task.created_at)

    async def list_all(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda task: -task.created_at)


def take(queue: list[Any]) -> Any:
    item = queue.pop(0) if len(queue) > 1 else queue[0]
    if isinstance(item, Exception):
        raise item
    return item


class FakeBackend:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.runs: dict[str, list[Any]] = {}
        self.stops: list[Any] = [None]
        self.approvals: list[Any] = [None]
        self.posts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str | None]] = []
        self.stopped: list[tuple[str, str]] = []
        self.approved: list[tuple[str, str, dict[str, Any]]] = []
        self.resolved: list[tuple[str, dict[str, Any]]] = []
        self.steers: list[tuple[str, str]] = []
        self.steer_error: Exception | None = None

    async def create_run(self, key: str, text: str) -> str:
        self.posts.append((key, text))
        run_id: str = take(self.created)
        return run_id

    async def get_run(self, run_id: str, stop_reason: str | None) -> RunState:
        self.gets.append((run_id, stop_reason))
        state: RunState = take(self.runs[run_id])
        return state

    async def stop(self, run_id: str, reason: str) -> RunState | None:
        self.stopped.append((run_id, reason))
        state: RunState | None = take(self.stops)
        return state

    async def approve(
        self, run_id: str, choice: Literal["once", "deny"], approval: dict[str, Any]
    ) -> None:
        self.approved.append((run_id, choice, approval))
        take(self.approvals)

    async def resolve_unknown(self, run_id: str, admission: dict[str, Any]) -> None:
        self.resolved.append((run_id, admission))

    async def steer(self, run_id: str, text: str) -> None:
        self.steers.append((run_id, text))
        if self.steer_error is not None:
            raise self.steer_error

    async def aclose(self) -> None:
        pass


class FakeClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def state(run_id: str, status: TaskStatus, **fields: Any) -> RunState:
    return RunState(run_id, status, **fields)


def make(
    run_timeout: float = 900,
) -> tuple[DelegationService, FakeRepo, FakeBackend, FakeClock, list[Task]]:
    repo = FakeRepo()
    backend = FakeBackend()
    clock = FakeClock()
    seen: list[Task] = []
    service = DelegationService(
        repo,
        backend,
        run_timeout=run_timeout,
        clock=clock,
        sleep=clock.sleep,
        on_change=seen.append,
    )
    return service, repo, backend, clock, seen


def test_delegate_runs_to_success() -> None:
    service, repo, backend, _, seen = make()
    backend.created = ["run_1"]
    backend.runs["run_1"] = [
        state("run_1", TaskStatus.RUNNING),
        state("run_1", TaskStatus.SUCCEEDED, result="готово"),
    ]

    async def main() -> Task:
        task = await service.delegate("k", "сделай")
        assert task.status == TaskStatus.RUNNING
        assert task.run_id == "run_1"
        return await service.watch("k")

    task = asyncio.run(main())
    assert task.status == TaskStatus.SUCCEEDED
    assert task.result == "готово"
    assert repo.tasks["k"] == task
    assert [item.status for item in seen] == ["queued", "running", "succeeded"]
    assert backend.posts == [("k", "сделай")]


def test_repeated_key_one_task_one_post() -> None:
    service, repo, backend, _, _ = make()
    backend.created = ["run_1"]

    async def main() -> tuple[Task, Task]:
        return await service.delegate("k", "a"), await service.delegate("k", "b")

    first, second = asyncio.run(main())
    assert first == second
    assert list(repo.tasks) == ["k"]
    assert backend.posts == [("k", "a")]


def test_rejected_post_fails_unavailable() -> None:
    service, repo, backend, _, _ = make()
    backend.created = [Rejected("429 rate_limit_exceeded")]
    with pytest.raises(Unavailable, match="rate_limit_exceeded"):
        asyncio.run(service.delegate("k", "a"))
    assert repo.tasks["k"].status == TaskStatus.FAILED
    assert repo.tasks["k"].error == "unavailable"
    assert len(backend.posts) == 1


def test_unknown_outcome_retries_once() -> None:
    service, repo, backend, _, _ = make()
    backend.created = [OutcomeUnknown("502"), "run_1"]
    task = asyncio.run(service.delegate("k", "a"))
    assert task.status == TaskStatus.RUNNING
    assert task.run_id == "run_1"
    assert backend.posts == [("k", "a"), ("k", "a")]

    service, repo, backend, _, _ = make()
    backend.created = [OutcomeUnknown("502"), OutcomeUnknown("504")]
    with pytest.raises(Unavailable):
        asyncio.run(service.delegate("k", "a"))
    assert repo.tasks["k"].status == TaskStatus.QUEUED
    assert repo.tasks["k"].error == "unavailable"
    assert backend.posts == [("k", "a"), ("k", "a")]


def test_waiting_approval_round_trip() -> None:
    service, _repo, backend, _, seen = make()
    backend.created = ["run_1"]
    backend.runs["run_1"] = [
        state("run_1", TaskStatus.WAITING_APPROVAL, approval=APPROVAL),
        state("run_1", TaskStatus.RUNNING),
        state("run_1", TaskStatus.SUCCEEDED, result="ok"),
    ]

    async def main() -> Task:
        await service.delegate("k", "a")
        return await service.watch("k")

    task = asyncio.run(main())
    assert task.status == TaskStatus.SUCCEEDED
    assert task.approval is None
    waiting = [item for item in seen if item.status == TaskStatus.WAITING_APPROVAL]
    assert len(waiting) == 1
    assert waiting[0].approval == APPROVAL
    assert [item.status for item in seen] == [
        "queued",
        "running",
        "waiting_approval",
        "running",
        "succeeded",
    ]


def test_deadline_stops_and_times_out() -> None:
    service, _repo, backend, clock, _ = make(run_timeout=10)
    backend.created = ["run_1"]
    backend.runs["run_1"] = [state("run_1", TaskStatus.RUNNING)]

    async def main() -> Task:
        await service.delegate("k", "a")
        return await service.watch("k")

    task = asyncio.run(main())
    assert backend.stopped == [("run_1", "timeout")]
    assert task.stop_requested_at is not None
    assert task.stop_requested_at > 10
    assert task.status == TaskStatus.FAILED
    assert task.error == "timeout"
    assert clock.now - task.stop_requested_at <= 30
    assert ("run_1", "timeout") in backend.gets


def test_deadline_completed_in_stop_response() -> None:
    service, _repo, backend, _, _ = make(run_timeout=10)
    backend.created = ["run_1"]
    backend.runs["run_1"] = [state("run_1", TaskStatus.RUNNING)]
    backend.stops = [state("run_1", TaskStatus.SUCCEEDED, result="успел")]

    async def main() -> Task:
        await service.delegate("k", "a")
        return await service.watch("k")

    task = asyncio.run(main())
    assert task.status == TaskStatus.SUCCEEDED
    assert task.result == "успел"
    assert task.stop_requested_at is not None
    assert backend.stopped == [("run_1", "timeout")]


def test_deadline_completed_within_window() -> None:
    service, _repo, backend, clock, _ = make(run_timeout=10)
    backend.created = ["run_1"]
    backend.runs["run_1"] = [state("run_1", TaskStatus.RUNNING)]

    async def main() -> Task:
        await service.delegate("k", "a")
        backend.stops = [None]
        while clock.now <= 10:
            await clock.sleep(2)
        backend.runs["run_1"] = [
            state("run_1", TaskStatus.RUNNING),
            state("run_1", TaskStatus.SUCCEEDED, result="в окне"),
        ]
        return await service.watch("k")

    task = asyncio.run(main())
    assert task.status == TaskStatus.SUCCEEDED
    assert task.result == "в окне"
    assert backend.stopped == [("run_1", "timeout")]


def test_run_not_found_is_lost() -> None:
    service, _repo, backend, _, _ = make()
    backend.created = ["run_1"]
    backend.runs["run_1"] = [RunNotFound("404")]

    async def main() -> Task:
        await service.delegate("k", "a")
        return await service.watch("k")

    task = asyncio.run(main())
    assert task.status == TaskStatus.FAILED
    assert task.error == "lost"


def test_unknown_calls_resolve() -> None:
    service, _repo, backend, _, seen = make()
    admission = {"admission_id": "a1", "execution_generation": 3}
    backend.created = ["run_1"]
    backend.runs["run_1"] = [
        state("run_1", TaskStatus.UNKNOWN, admission=admission),
        state("run_1", TaskStatus.FAILED, error="interrupted"),
    ]

    async def main() -> Task:
        await service.delegate("k", "a")
        return await service.watch("k")

    task = asyncio.run(main())
    assert backend.resolved == [("run_1", admission)]
    assert [item.status for item in seen] == ["queued", "running", "unknown", "failed"]
    assert task.status == TaskStatus.FAILED
    assert task.error == "interrupted"


def waiting_task(repo: FakeRepo, approval: dict[str, Any] = APPROVAL) -> None:
    repo.tasks["k"] = Task(
        "k",
        "a",
        TaskStatus.WAITING_APPROVAL,
        created_at=0,
        run_id="run_1",
        approval=approval,
    )


def test_approve_once_and_deny() -> None:
    service, repo, backend, _, _ = make()
    waiting_task(repo)

    async def main() -> None:
        await service.approve("k", "once")
        await service.approve("k", "deny")

    asyncio.run(main())
    assert backend.approved == [
        ("run_1", "once", APPROVAL),
        ("run_1", "deny", APPROVAL),
    ]
    assert repo.tasks["k"].status == TaskStatus.WAITING_APPROVAL


def test_approve_authority_passes_generation() -> None:
    service, repo, backend, _, _ = make()
    waiting_task(repo, AUTHORITY_APPROVAL)
    asyncio.run(service.approve("k", "once"))
    assert backend.approved == [("run_1", "once", AUTHORITY_APPROVAL)]


def test_approve_conflict() -> None:
    service, repo, backend, _, _ = make()
    waiting_task(repo)
    backend.approvals = [ApprovalNotActive("409 approval_not_pending")]
    with pytest.raises(ApprovalNotActive):
        asyncio.run(service.approve("k", "once"))
    assert len(backend.approved) == 1
    repo.tasks["k"] = replace(repo.tasks["k"], status=TaskStatus.RUNNING)
    with pytest.raises(ApprovalNotActive):
        asyncio.run(service.approve("k", "once"))
    assert len(backend.approved) == 1
    with pytest.raises(TaskNotFound):
        asyncio.run(service.approve("missing", "once"))


def test_approve_expected() -> None:
    service, repo, backend, _, _ = make()
    waiting_task(repo)
    with pytest.raises(ApprovalNotActive):
        asyncio.run(
            service.approve("k", "once", expected={**APPROVAL, "request_id": "other"})
        )
    assert backend.approved == []
    asyncio.run(service.approve("k", "once", expected=APPROVAL))
    assert backend.approved == [("run_1", "once", APPROVAL)]


def test_recover_queued() -> None:
    service, repo, backend, clock, _ = make(run_timeout=100)
    clock.now = 150
    repo.tasks["fresh"] = Task("fresh", "a", TaskStatus.QUEUED, created_at=100)
    repo.tasks["old"] = Task("old", "b", TaskStatus.QUEUED, created_at=10)
    backend.created = ["run_1"]
    backend.runs["run_1"] = [state("run_1", TaskStatus.SUCCEEDED, result="ok")]
    undelivered = asyncio.run(service.recover())
    assert backend.posts == [("fresh", "a")]
    assert repo.tasks["fresh"].status == TaskStatus.SUCCEEDED
    assert repo.tasks["fresh"].run_id == "run_1"
    assert repo.tasks["old"].status == TaskStatus.FAILED
    assert repo.tasks["old"].error == "lost before launch"
    assert [task.tool_call_id for task in undelivered] == ["old", "fresh"]


def test_recover_queued_unavailable_stops() -> None:
    service, repo, backend, _, _ = make()
    repo.tasks["k"] = Task(
        "k", "a", TaskStatus.QUEUED, created_at=0, error="unavailable"
    )
    backend.created = ["run_9"]
    asyncio.run(service.recover())
    assert backend.posts == [("k", "a")]
    assert backend.stopped == [("run_9", "timeout")]
    task = repo.tasks["k"]
    assert task.status == TaskStatus.FAILED
    assert task.error == "unavailable"
    assert task.run_id == "run_9"


def test_recover_running_and_waiting() -> None:
    service, repo, backend, clock, _ = make(run_timeout=100)
    clock.now = 150
    repo.tasks["r"] = Task("r", "a", TaskStatus.RUNNING, created_at=140, run_id="run_r")
    repo.tasks["w"] = Task(
        "w",
        "b",
        TaskStatus.WAITING_APPROVAL,
        created_at=145,
        run_id="run_w",
        approval=APPROVAL,
    )
    repo.tasks["x"] = Task("x", "c", TaskStatus.RUNNING, created_at=0, run_id="run_x")
    backend.runs["run_r"] = [
        state("run_r", TaskStatus.RUNNING),
        state("run_r", TaskStatus.SUCCEEDED, result="r"),
    ]
    backend.runs["run_w"] = [
        state("run_w", TaskStatus.WAITING_APPROVAL, approval=APPROVAL),
        state("run_w", TaskStatus.SUCCEEDED, result="w"),
    ]
    backend.runs["run_x"] = [state("run_x", TaskStatus.RUNNING)]
    backend.stops = [state("run_x", TaskStatus.FAILED, error="timeout")]
    asyncio.run(service.recover())
    assert repo.tasks["r"].status == TaskStatus.SUCCEEDED
    assert repo.tasks["w"].status == TaskStatus.SUCCEEDED
    assert repo.tasks["w"].approval is None
    assert backend.stopped == [("run_x", "timeout")]
    assert ("run_x", None) not in backend.gets
    assert repo.tasks["x"].status == TaskStatus.FAILED
    assert repo.tasks["x"].error == "timeout"


def test_recover_returns_undelivered() -> None:
    service, repo, _backend, clock, _ = make()
    clock.now = 100
    repo.tasks["b"] = Task("b", "b", TaskStatus.FAILED, created_at=2, error="lost")
    repo.tasks["a"] = Task("a", "a", TaskStatus.SUCCEEDED, created_at=1, result="ok")
    repo.tasks["c"] = Task(
        "c", "c", TaskStatus.SUCCEEDED, created_at=3, result="ok", delivered_at=5
    )
    undelivered = asyncio.run(service.recover())
    assert [task.tool_call_id for task in undelivered] == ["a", "b"]
    task = asyncio.run(service.mark_delivered("a"))
    assert task.delivered_at == 100
    assert repo.tasks["a"].delivered_at == 100
    with pytest.raises(TaskNotFound):
        asyncio.run(service.mark_delivered("missing"))


def running(key: str = "k", run_id: str = "run_1") -> Task:
    return Task(key, "a", TaskStatus.RUNNING, created_at=0, run_id=run_id)


def test_cancel_running() -> None:
    service, repo, backend, clock, _ = make()
    clock.now = 5
    repo.tasks["k"] = running()
    backend.stops = [state("run_1", TaskStatus.FAILED, error="cancelled")]
    task = asyncio.run(service.cancel("k"))
    assert task.status == TaskStatus.FAILED
    assert task.error == "cancelled"
    assert task.stop_reason == "user"
    assert task.stop_requested_at == 5
    assert task.delivered_at is not None
    assert repo.tasks["k"] == task
    assert backend.stopped == [("run_1", "user")]


def test_cancel_waits_for_terminal() -> None:
    service, repo, backend, _, _ = make()
    repo.tasks["k"] = running()
    backend.stops = [None]
    backend.runs["run_1"] = [
        state("run_1", TaskStatus.RUNNING),
        state("run_1", TaskStatus.FAILED, error="cancelled"),
    ]
    task = asyncio.run(service.cancel("k"))
    assert task.status == TaskStatus.FAILED
    assert task.error == "cancelled"
    assert backend.gets == [("run_1", "user"), ("run_1", "user")]


def test_cancel_queued_without_run() -> None:
    service, repo, backend, _, _ = make()
    repo.tasks["k"] = Task("k", "a", TaskStatus.QUEUED, created_at=0)
    task = asyncio.run(service.cancel("k"))
    assert task.status == TaskStatus.FAILED
    assert task.error == "cancelled"
    assert task.stop_reason == "user"
    assert task.delivered_at is not None
    assert backend.stopped == []


def test_cancel_closed_raises() -> None:
    service, repo, backend, _, _ = make()
    repo.tasks["k"] = Task("k", "a", TaskStatus.SUCCEEDED, created_at=0, result="ok")
    with pytest.raises(TaskNotOpen):
        asyncio.run(service.cancel("k"))
    assert backend.stopped == []


def test_apply_keeps_user_cancel() -> None:
    service, repo, _, _, seen = make()
    old = running()
    current = replace(old, stop_requested_at=1.0, stop_reason="user")
    repo.tasks["k"] = current
    gone = state("run_1", TaskStatus.FAILED, error="interrupted by gateway restart")
    task = asyncio.run(service._apply(old, gone))
    assert task == current
    assert repo.tasks["k"] == current
    assert seen == []


def test_apply_keeps_terminal() -> None:
    service, repo, _, _, seen = make()
    old = running()
    current = old.transition(TaskStatus.FAILED, error="cancelled")
    repo.tasks["k"] = current
    task = asyncio.run(service._apply(old, state("run_1", TaskStatus.RUNNING)))
    assert task == current
    assert repo.tasks["k"] == current
    assert seen == []


def test_expire_sets_timeout_reason() -> None:
    service, repo, backend, _, _ = make(run_timeout=10)
    repo.tasks["k"] = running()
    backend.stops = [state("run_1", TaskStatus.FAILED, error="timeout")]
    task = asyncio.run(service._expire(repo.tasks["k"]))
    assert task.stop_reason == "timeout"
    assert repo.tasks["k"].stop_reason == "timeout"


def test_steer_running() -> None:
    service, repo, backend, _, _ = make()
    repo.tasks["k"] = running()
    asyncio.run(service.steer("k", "добавь b.txt"))
    assert backend.steers == [("run_1", "добавь b.txt")]
    assert repo.tasks["k"] == running()


def test_steer_not_running_raises() -> None:
    service, repo, backend, _, _ = make()
    repo.tasks["k"] = replace(
        running(), status=TaskStatus.WAITING_APPROVAL, approval=APPROVAL
    )
    with pytest.raises(NotSteerable):
        asyncio.run(service.steer("k", "добавь b.txt"))
    assert backend.steers == []
