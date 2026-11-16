from dataclasses import dataclass
from typing import Any, Literal, Protocol

from voca.delegation.model import Task, TaskStatus


class DelegationError(Exception):
    pass


class Rejected(DelegationError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class OutcomeUnknown(DelegationError):
    pass


class RunNotFound(DelegationError):
    pass


class ApprovalNotActive(DelegationError):
    pass


class Unavailable(DelegationError):
    pass


class TaskNotFound(DelegationError):
    pass


@dataclass(frozen=True)
class RunState:
    run_id: str
    status: TaskStatus
    result: str | None = None
    error: str | None = None
    approval: dict[str, Any] | None = None
    admission: dict[str, Any] | None = None


class TaskRepository(Protocol):
    async def insert(self, task: Task) -> tuple[Task, bool]: ...

    async def save(self, task: Task) -> None: ...

    async def get(self, key: str) -> Task | None: ...

    async def list_open(self) -> list[Task]: ...

    async def list_undelivered(self) -> list[Task]: ...

    async def list_all(self) -> list[Task]: ...


class DelegationBackend(Protocol):
    async def create_run(self, key: str, text: str) -> str: ...

    async def get_run(self, run_id: str, stop_requested: bool) -> RunState: ...

    async def stop(self, run_id: str) -> RunState | None: ...

    async def approve(
        self, run_id: str, choice: Literal["once", "deny"], approval: dict[str, Any]
    ) -> None: ...

    async def resolve_unknown(self, run_id: str, admission: dict[str, Any]) -> None: ...

    async def aclose(self) -> None: ...
