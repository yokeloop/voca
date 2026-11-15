from datetime import date

from voca.conversation.compaction import (
    END_MARKER,
    Limits,
    build_prompt,
    plan,
    summary_budget,
    unwrap,
    wrap,
)
from voca.conversation.context import Item


def message(id: str, role: str, tokens: int) -> Item:
    return Item(id=id, kind="message", role=role, call_id=None, tokens=tokens, text=id)


def call(id: str, kind: str, call_id: str, tokens: int) -> Item:
    return Item(id=id, kind=kind, role=None, call_id=call_id, tokens=tokens, text=id)


def turns(count: int, tokens: int, first: int = 1) -> list[Item]:
    items: list[Item] = []
    for n in range(first, first + count):
        items.append(message(f"u{n}", "user", tokens // 2))
        items.append(message(f"a{n}", "assistant", tokens - tokens // 2))
    return items


def test_plan_keeps_pairs_and_min_turns() -> None:
    items = [
        message("s", "system", 10),
        message("u1", "user", 50),
        call("fc1", "function_call", "c1", 50),
        message("u2", "user", 50),
        call("fo1", "function_call_output", "c1", 50),
        message("a1", "assistant", 50),
        *turns(4, 100, first=3),
    ]
    found = plan(items, Limits(threshold=1, tail_budget=10, tail_turns_min=2))
    assert found is not None
    assert not {"u5", "a5", "u6", "a6"} & found.folded
    assert ("fc1" in found.folded) == ("fo1" in found.folded)
    assert found.folded_turns == 3
    assert "s" not in found.folded


def test_plan_tail_by_budget() -> None:
    found = plan(turns(6, 100), Limits(threshold=1, tail_budget=350, tail_turns_min=2))
    assert found is not None
    assert found.folded_turns == 3


def test_plan_nothing_to_fold() -> None:
    limits = Limits(threshold=1, tail_budget=10, tail_turns_min=2)
    assert plan(turns(2, 100), limits) is None
    limits = Limits(threshold=1, tail_budget=10, tail_turns_min=0)
    assert plan(turns(1, 5000), limits) is None


def test_plan_finds_previous_summary() -> None:
    previous = Item(
        id="sum",
        kind="message",
        role="assistant",
        call_id=None,
        tokens=30,
        text=wrap("Старый пересказ"),
        summary=True,
    )
    items = [previous, *turns(5, 100)]
    found = plan(items, Limits(threshold=1, tail_budget=10, tail_turns_min=2))
    assert found is not None
    assert found.previous_id == "sum"
    assert found.previous_body == "Старый пересказ"
    assert "sum" not in found.folded
    assert found.estimate_before == 530


def test_build_prompt_fresh_and_iterative() -> None:
    middle = [
        Item(
            id="u1",
            kind="message",
            role="user",
            call_id=None,
            tokens=5,
            text="Меня зовут Арсений",
        ),
        Item(
            id="fc1",
            kind="function_call",
            role=None,
            call_id="c1",
            tokens=5,
            text='delegate({"task": "x"})',
        ),
        Item(
            id="fo1",
            kind="function_call_output",
            role=None,
            call_id="c1",
            tokens=5,
            text="готово",
        ),
    ]
    today = date(2026, 11, 15)
    fresh = build_prompt(middle, None, today=today, budget=400)
    for part in (
        "РЕПЛИКИ ДЛЯ ПЕРЕСКАЗА:",
        "[ПОЛЬЗОВАТЕЛЬ]: Меня зовут Арсений",
        '[ВЫЗОВ c1]: delegate({"task": "x"})',
        "[РЕЗУЛЬТАТ c1]: готово",
        "[СКРЫТО]",
        "сегодня 2026-11-15",
        "Целевой объём ~400 токенов",
        "## О чём говорили",
        "## Факты и предпочтения пользователя",
        "## Договорённости и решения",
        "## Поручения и их итог",
        "## Открытые вопросы",
        "## Последняя незакрытая просьба",
    ):
        assert part in fresh
    assert "ПРЕДЫДУЩИЙ ПЕРЕСКАЗ" not in fresh
    iterative = build_prompt(middle, "Старый пересказ", today=today, budget=400)
    assert "ПРЕДЫДУЩИЙ ПЕРЕСКАЗ:\nСтарый пересказ" in iterative
    assert "НОВЫЕ РЕПЛИКИ:" in iterative
    assert "РЕПЛИКИ ДЛЯ ПЕРЕСКАЗА" not in iterative
    for prompt in (fresh, iterative):
        for field in (
            "{budget}",
            "{today}",
            "{turns}",
            "{previous}",
            "{sections}",
            "{preamble}",
        ):
            assert field not in prompt


def test_summary_budget() -> None:
    assert summary_budget(100) == 400
    assert summary_budget(5000) == 1000
    assert summary_budget(100000) == 1500


def test_wrap_unwrap() -> None:
    wrapped = wrap("Тело\nвторая строка")
    assert unwrap(wrapped) == "Тело\nвторая строка"
    assert wrapped.startswith("[СЖАТИЕ КОНТЕКСТА — ТОЛЬКО СПРАВКА]")
    assert wrapped.endswith(END_MARKER)
