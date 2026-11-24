from dataclasses import dataclass, replace
from enum import StrEnum

from voca.delegation.danger import Danger, level
from voca.delegation.model import STOP_USER, Task, TaskStatus


class EventKind(StrEnum):
    APPROVAL = "approval"
    RESULT = "result"
    FAILURE = "failure"


class EventState(StrEnum):
    ARRIVED = "arrived"
    MENTIONED = "mentioned"
    ESSENCE = "essence"
    CLOSED = "closed"


NEXT: dict[EventState, frozenset[EventState]] = {
    EventState.ARRIVED: frozenset({EventState.MENTIONED, EventState.CLOSED}),
    EventState.MENTIONED: frozenset({EventState.ESSENCE, EventState.CLOSED}),
    EventState.ESSENCE: frozenset({EventState.CLOSED}),
    EventState.CLOSED: frozenset(),
}

DELIVERED = "delivered"
CANCELLED_BY_USER = "cancelled"
RESOLVED = "resolved"
DENIED = "denied"


@dataclass(frozen=True)
class Event:
    tool_call_id: str
    kind: EventKind
    request_id: str
    state: EventState
    created_at: float
    updated_at: float
    danger: Danger | None = None
    closed_reason: str | None = None
    id: int | None = None


def advance(
    event: Event, state: EventState, at: float, reason: str | None = None
) -> Event:
    if state not in NEXT[event.state]:
        raise ValueError(f"переход {event.state} → {state} недопустим")
    return replace(event, state=state, updated_at=at, closed_reason=reason)


def request_of(task: Task) -> str:
    if task.approval:
        return str(task.approval.get("request_id") or "")
    return ""


def closed_reason(task: Task) -> str:
    return CANCELLED_BY_USER if task.stop_reason == STOP_USER else DELIVERED


def open_request(task: Task) -> str | None:
    if task.status == TaskStatus.WAITING_APPROVAL and task.approval is not None:
        return request_of(task)
    return None


def arrival(task: Task, now: float) -> Event | None:
    if task.status == TaskStatus.WAITING_APPROVAL and task.approval is not None:
        return Event(
            tool_call_id=task.tool_call_id,
            kind=EventKind.APPROVAL,
            request_id=request_of(task),
            state=EventState.ARRIVED,
            created_at=now,
            updated_at=now,
            danger=level(task.approval.get("description")),
        )
    if task.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
        kind = (
            EventKind.RESULT
            if task.status == TaskStatus.SUCCEEDED
            else EventKind.FAILURE
        )
        if task.delivered_at is not None:
            return Event(
                tool_call_id=task.tool_call_id,
                kind=kind,
                request_id="",
                state=EventState.CLOSED,
                created_at=now,
                updated_at=now,
                closed_reason=closed_reason(task),
            )
        return Event(
            tool_call_id=task.tool_call_id,
            kind=kind,
            request_id="",
            state=EventState.ARRIVED,
            created_at=now,
            updated_at=now,
        )
    return None
