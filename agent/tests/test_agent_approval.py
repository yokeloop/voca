from dataclasses import replace

from voca.conversation.agent import announcement_text
from voca.delegation.model import Task, TaskStatus

APPROVAL = {
    "description": "delete in root path",
    "command": "rm -r /tmp/x",
    "choices": ["once", "deny", "session", "always"],
    "request_id": "p1",
    "execution_generation": 3,
}


def waiting(
    key: str, approval: dict[str, object] = APPROVAL, created_at: float = 1.0
) -> Task:
    return Task(
        key,
        "Удали каталог /tmp/x",
        TaskStatus.WAITING_APPROVAL,
        created_at=created_at,
        run_id=f"run_{key}",
        approval=approval,
    )


def test_announcement_text() -> None:
    text = announcement_text(waiting("a"))
    assert text == (
        "Hermes просит подтверждения по поручению «Удали каталог /tmp/x»: "
        "delete in root path. Скажите „Подтверждаю“ или „Отклоняю“."
    )
    assert "rm -r" not in text
    long = ("слово " * 20)[:100]
    assert len(long) == 100
    quoted = announcement_text(replace(waiting("b"), text=long))
    assert 0 < len(quoted.split("«")[1].split("»")[0]) <= 60
    bare = waiting("c", {"command": "rm -r /tmp/x", "request_id": "p1"})
    assert announcement_text(bare) == (
        "Hermes просит подтверждения по поручению «Удали каталог /tmp/x». "
        "Скажите „Подтверждаю“ или „Отклоняю“."
    )
