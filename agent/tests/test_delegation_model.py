import pytest

from voca.delegation.model import Task, TaskStatus


def task(status: TaskStatus) -> Task:
    return Task("k", "t", status, created_at=1.0)


def test_allowed_transitions() -> None:
    running = task(TaskStatus.QUEUED).transition(TaskStatus.RUNNING, run_id="run_1")
    assert running.status == TaskStatus.RUNNING
    assert running.run_id == "run_1"
    assert task(TaskStatus.QUEUED).transition(TaskStatus.FAILED).status == (
        TaskStatus.FAILED
    )
    approval = {"request_id": "r1"}
    waiting = running.transition(TaskStatus.WAITING_APPROVAL, approval=approval)
    assert waiting.status == TaskStatus.WAITING_APPROVAL
    assert waiting.approval == approval
    assert waiting.run_id == "run_1"
    assert waiting.transition(TaskStatus.RUNNING).status == TaskStatus.RUNNING
    unknown = running.transition(TaskStatus.UNKNOWN)
    assert unknown.status == TaskStatus.UNKNOWN
    assert unknown.transition(TaskStatus.SUCCEEDED).status == TaskStatus.SUCCEEDED
    assert waiting.transition(TaskStatus.SUCCEEDED).status == TaskStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (TaskStatus.QUEUED, TaskStatus.SUCCEEDED),
        (TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL),
        (TaskStatus.RUNNING, TaskStatus.QUEUED),
        (TaskStatus.SUCCEEDED, TaskStatus.RUNNING),
        (TaskStatus.FAILED, TaskStatus.RUNNING),
        (TaskStatus.SUCCEEDED, TaskStatus.FAILED),
    ],
)
def test_forbidden_transitions(source: TaskStatus, target: TaskStatus) -> None:
    with pytest.raises(ValueError):
        task(source).transition(target)


def test_is_open() -> None:
    for status in (
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.UNKNOWN,
    ):
        assert task(status).is_open
    for status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
        assert not task(status).is_open
