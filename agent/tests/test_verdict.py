from typing import Any

import pytest

from voca.conversation.verdict import (
    KINDS,
    VERDICTS,
    Decision,
    DecisionFailure,
    Prompt,
    Situation,
    Sorted,
    SorterPrompt,
    drop,
    read_answer,
    read_sort,
    request_body,
    sorter_body,
    sorter_state,
)

PROMPT = Prompt(
    instructions="Что отвечает пользователь?",
    verdicts={name: f"описание {name}" for name in VERDICTS},
    threshold=0.6,
)
SITUATION = Situation(
    task="Удали каталог /tmp/x",
    request="удаление в корневом каталоге",
    command="rm -r /tmp/x",
    announcement="Hermes просит подтверждения",
    phrase="Подтверждаю.",
)


def answer(choice: Any = "approve", confidence: Any = 0.9) -> Any:
    return {
        "answers": {
            "verdict": {"type": "choice", "choice": choice, "confidence": confidence},
        }
    }


def test_request_body_shape() -> None:
    body = request_body(PROMPT, SITUATION, "d1-2B")
    assert body["model"] == "d1-2B"
    assert body["state"] == {
        "поручение": "Удали каталог /tmp/x",
        "запрос": "удаление в корневом каталоге",
        "команда": "rm -r /tmp/x",
        "объявление": "Hermes просит подтверждения",
        "фраза пользователя": "Подтверждаю.",
    }
    verdict = body["questions"]["verdict"]
    assert verdict["type"] == "choice"
    assert verdict["instructions"] == "Что отвечает пользователь?"
    assert list(verdict["criteria"]) == list(VERDICTS)
    assert list(body["questions"]) == ["verdict"]
    bare = Situation("t", "r", None, "a", "p")
    assert "команда" not in request_body(PROMPT, bare, "d1-2B")["state"]


def test_read_answer_confident() -> None:
    assert read_answer(answer(), PROMPT, 120) == Decision(
        "approve", "approve", 0.9, 120
    )


def test_read_answer_below_threshold_is_unclear() -> None:
    decision = read_answer(answer(confidence=0.4), PROMPT, 5)
    assert decision == Decision("unclear", "approve", 0.4, 5)


def test_read_answer_more() -> None:
    decision = read_answer(answer("deny_more", 0.8), PROMPT, 5)
    assert decision == Decision("deny_more", "deny_more", 0.8, 5)


@pytest.mark.parametrize(
    "data",
    [
        {},
        None,
        answer(choice="maybe"),
        answer(confidence="0.9"),
        answer(confidence=1.5),
        {"answers": {"verdict": None}},
    ],
)
def test_read_answer_bad_shape(data: Any) -> None:
    assert read_answer(data, PROMPT, 7) == DecisionFailure("bad_answer", 7)


SORTER = SorterPrompt(
    instructions="Что это за фраза пользователя?",
    criteria={"noise": "шум", "reply": "реплика"},
    keep_words=frozenset({"нет", "не", "ни", "стоп", "да", "давай"}),
    max_words=4,
    min_words=2,
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
