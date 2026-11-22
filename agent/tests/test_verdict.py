from typing import Any

import pytest

from voca.conversation.verdict import (
    KINDS,
    RELATIONS,
    REPLIES,
    DecisionFailure,
    Sorted,
    SorterPrompt,
    drop,
    read_sort,
    sorter_body,
    sorter_state,
)

SORTER = SorterPrompt(
    instructions="Что это за фраза пользователя?",
    criteria={"noise": "шум", "reply": "реплика"},
    keep_words=frozenset({"нет", "не", "ни", "стоп", "да", "давай"}),
    max_words=4,
    min_words=2,
    relation_instructions="Как фраза связана с запросом?",
    relation_criteria={name: f"связь {name}" for name in RELATIONS},
    answer_instructions="Что отвечает пользователь?",
    answer_criteria={name: f"ответ {name}" for name in REPLIES},
    target_instructions="К какому запросу?",
    target_request="только к запросу {n}",
    target_unclear="непонятно",
)
NOISE = Sorted("noise", 0.9, 30)
REPLY = Sorted("reply", 0.9, 30)
FAILED = DecisionFailure("timeout", 300)


def sort_answer(choice: Any = "noise", confidence: Any = 0.8) -> Any:
    return {
        "answers": {
            "kind": {"type": "choice", "choice": choice, "confidence": confidence},
        }
    }


def full_answer(
    relation: Any = "answers",
    reply: Any = "approve",
    target: Any = None,
    relation_confidence: Any = 0.9,
) -> Any:
    data = sort_answer("reply", 0.95)
    data["answers"]["relation"] = {
        "type": "choice",
        "choice": relation,
        "confidence": relation_confidence,
    }
    data["answers"]["answer"] = {"type": "choice", "choice": reply, "confidence": 0.7}
    if target is not None:
        data["answers"]["target"] = {
            "type": "choice",
            "choice": target,
            "confidence": 0.6,
        }
    return data


def test_sorter_body_shape() -> None:
    state = {"последняя фраза агента": "", "фраза пользователя": "Угу."}
    body = sorter_body(SORTER, state, "d1-2B")
    assert body == {
        "model": "d1-2B",
        "state": state,
        "questions": {
            "kind": {
                "type": "choice",
                "instructions": "Что это за фраза пользователя?",
                "criteria": {"noise": "шум", "reply": "реплика"},
            },
        },
    }
    assert list(body["questions"]["kind"]["criteria"]) == list(KINDS)
    assert sorter_body(SORTER, state, "d1-2B", 0) == body


def test_sorter_body_one_request() -> None:
    body = sorter_body(SORTER, {"фраза пользователя": "Да."}, "d1-2B", 1)
    assert list(body["questions"]) == ["kind", "relation", "answer"]
    assert body["questions"]["relation"] == {
        "type": "choice",
        "instructions": "Как фраза связана с запросом?",
        "criteria": {name: f"связь {name}" for name in RELATIONS},
    }
    assert body["questions"]["answer"] == {
        "type": "choice",
        "instructions": "Что отвечает пользователь?",
        "criteria": {name: f"ответ {name}" for name in REPLIES},
    }


def test_sorter_body_two_requests() -> None:
    body = sorter_body(SORTER, {"фраза пользователя": "Да."}, "d1-2B", 2)
    assert list(body["questions"]) == ["kind", "relation", "answer", "target"]
    assert body["questions"]["target"] == {
        "type": "choice",
        "instructions": "К какому запросу?",
        "criteria": {
            "request_1": "только к запросу 1",
            "request_2": "только к запросу 2",
            "unclear": "непонятно",
        },
    }


def test_sorter_state_requests() -> None:
    assert sorter_state("Привет.", [], "Угу.") == {
        "последняя фраза агента": "Привет.",
        "фраза пользователя": "Угу.",
    }
    one = sorter_state("Привет.", [("удаление", "rm -r /tmp/x")], "Да.")
    assert list(one.items()) == [
        ("последняя фраза агента", "Привет."),
        ("запрос", "удаление"),
        ("команда", "rm -r /tmp/x"),
        ("фраза пользователя", "Да."),
    ]
    bare = sorter_state("Привет.", [("удаление", None)], "Да.")
    assert list(bare) == ["последняя фраза агента", "запрос", "фраза пользователя"]
    two = sorter_state("", [("удаление", "rm x"), ("запись", None)], "Да.")
    assert list(two.items()) == [
        ("последняя фраза агента", ""),
        ("запрос 1", "удаление"),
        ("команда 1", "rm x"),
        ("запрос 2", "запись"),
        ("фраза пользователя", "Да."),
    ]


def test_read_sort_ok() -> None:
    assert read_sort(sort_answer(), 40) == Sorted("noise", 0.8, 40)
    assert read_sort(sort_answer("reply", 0.1), 40) == Sorted("reply", 0.1, 40)


def test_read_sort_answers() -> None:
    assert read_sort(full_answer("asks", "deny"), 40, 1) == Sorted(
        "reply",
        0.95,
        40,
        relation="asks",
        relation_confidence=0.9,
        answer="deny",
        answer_confidence=0.7,
    )


def test_read_sort_target() -> None:
    picked = read_sort(full_answer(target="request_2"), 40, 2)
    assert isinstance(picked, Sorted)
    assert (picked.target, picked.target_confidence) == (2, 0.6)
    unclear = read_sort(full_answer(target="unclear"), 40, 2)
    assert isinstance(unclear, Sorted)
    assert (unclear.target, unclear.target_confidence) == (None, 0.6)


def test_read_sort_missing_answers() -> None:
    assert read_sort(sort_answer("reply"), 5, 1) == DecisionFailure("bad_answer", 5)
    assert read_sort(full_answer(target="request_3"), 5, 2) == DecisionFailure(
        "bad_answer", 5
    )
    assert read_sort(full_answer(), 5, 2) == DecisionFailure("bad_answer", 5)
    assert read_sort(full_answer(relation_confidence=1.5), 5, 1) == DecisionFailure(
        "bad_answer", 5
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        None,
        {"answers": {"kind": None}},
        sort_answer(choice="approve"),
        sort_answer(confidence=1.5),
        sort_answer(confidence=-0.1),
        sort_answer(confidence=True),
    ],
)
def test_read_sort_bad_shape(data: Any) -> None:
    assert read_sort(data, 9) == DecisionFailure("bad_answer", 9)


@pytest.mark.parametrize(
    "text", ["Угу.", "Yeah, yeah.", "Okay.", "Да-да.", "Ну вот я и."]
)
def test_drop_noise(text: str) -> None:
    assert drop(text, NOISE, SORTER, speaking=True)
    assert drop(text, NOISE, SORTER, speaking=False)


@pytest.mark.parametrize(
    "text", ["Стоп, не надо.", "Нет.", "Ага, давай.", "Ни в коем случае."]
)
def test_drop_keeps_anchored(text: str) -> None:
    assert not drop(text, NOISE, SORTER, speaking=True)


def test_drop_keeps_long_noise() -> None:
    assert not drop("Ну вот я и это.", NOISE, SORTER, speaking=True)


def test_drop_reply() -> None:
    assert not drop("Угу.", REPLY, SORTER, speaking=True)


def test_drop_failure_length_only_while_speaking() -> None:
    assert drop("Угу.", FAILED, SORTER, speaking=True)
    assert not drop("Угу.", FAILED, SORTER, speaking=False)
    assert not drop("Нет.", FAILED, SORTER, speaking=True)
    assert not drop("Расскажи подробнее", FAILED, SORTER, speaking=True)
