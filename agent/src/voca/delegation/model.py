import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

Choice = Literal["once", "deny"]


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED})
ACTIVE = frozenset(
    {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL, TaskStatus.UNKNOWN}
)
ALLOWED: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.UNKNOWN,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.UNKNOWN,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.UNKNOWN: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}

UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"
LOST_BEFORE_LAUNCH = "lost before launch"
LOST = "lost"
GATEWAY_RESTART = "interrupted by gateway restart"
CANCELLED = "cancelled"
STOP_USER = "user"
STOP_TIMEOUT = "timeout"


@dataclass(frozen=True)
class Task:
    tool_call_id: str
    text: str
    status: TaskStatus
    created_at: float
    run_id: str | None = None
    result: str | None = None
    error: str | None = None
    delivered_at: float | None = None
    stop_requested_at: float | None = None
    stop_reason: str | None = None
    approval: dict[str, Any] | None = None

    def transition(self, status: TaskStatus, **changes: Any) -> "Task":
        if status not in ALLOWED[self.status]:
            raise ValueError(f"переход {self.status} → {status} недопустим")
        return dataclasses.replace(self, status=status, **changes)

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL
