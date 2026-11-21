import pytest

from voca.delegation.danger import Danger
from voca.delegation.events import (
    CANCELLED_BY_USER,
    DELIVERED,
    Event,
    EventKind,
    EventState,
    advance,
    arrival,
)
from voca.delegation.model import Task, TaskStatus


def waiting(approval: dict[str, str] | None) -> Task:
    return Task(
        "k", "задача", TaskStatus.WAITING_APPROVAL, 1.0, run_id="r", approval=approval
    )


def test_arrival_approval() -> None:
    event = arrival(
        waiting({"request_id": "r1", "description": "package manager uninstall"}), 7.0
    )
    assert event == Event(
        tool_call_id="k",
        kind=EventKind.APPROVAL,
        request_id="r1",
        state=EventState.ARRIVED,
        created_at=7.0,
        updated_at=7.0,
        danger=Danger.MEDIUM,
    )


def test_arrival_approval_without_request_id() -> None:
    event = arrival(waiting({"description": "delete in root path"}), 7.0)
    assert event is not None
    assert event.request_id == ""
    assert event.danger == Danger.HIGH


def test_arrival_result_and_failure() -> None:
    result = arrival(Task("k", "a", TaskStatus.SUCCEEDED, 1.0, result="ok"), 3.0)
    failure = arrival(Task("k", "a", TaskStatus.FAILED, 1.0, error="lost"), 3.0)
    assert result == Event("k", EventKind.RESULT, "", EventState.ARRIVED, 3.0, 3.0)
    assert failure == Event("k", EventKind.FAILURE, "", EventState.ARRIVED, 3.0, 3.0)


def test_arrival_already_delivered_is_closed() -> None:
    plain = arrival(
        Task("k", "a", TaskStatus.FAILED, 1.0, error="unavailable", delivered_at=2.0),
        3.0,
    )
    cancelled = arrival(
        Task(
            "k",
            "a",
            TaskStatus.FAILED,
            1.0,
            error="cancelled",
            delivered_at=2.0,
            stop_reason="user",
        ),
        3.0,
    )
    assert plain is not None
    assert plain.state == EventState.CLOSED
    assert plain.closed_reason == DELIVERED
    assert cancelled is not None
    assert cancelled.state == EventState.CLOSED
    assert cancelled.closed_reason == CANCELLED_BY_USER


def test_no_event_for_open_statuses() -> None:
    for status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.UNKNOWN):
        assert arrival(Task("k", "a", status, 1.0), 3.0) is None
    assert arrival(waiting(None), 3.0) is None


def test_advance_follows_next() -> None:
    event = Event("k", EventKind.RESULT, "", EventState.ARRIVED, 1.0, 1.0)
    mentioned = advance(event, EventState.MENTIONED, 2.0)
    essence = advance(mentioned, EventState.ESSENCE, 3.0)
    closed = advance(essence, EventState.CLOSED, 4.0, DELIVERED)
    assert closed.state == EventState.CLOSED
    assert closed.updated_at == 4.0
    assert closed.closed_reason == DELIVERED
    assert advance(event, EventState.CLOSED, 2.0).state == EventState.CLOSED
    with pytest.raises(ValueError):
        advance(closed, EventState.ARRIVED, 5.0)
    with pytest.raises(ValueError):
        advance(event, EventState.ESSENCE, 5.0)
