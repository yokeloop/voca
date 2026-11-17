from typing import Any

import pytest

from voca.conversation.verdict import (
    VERDICTS,
    Decision,
    DecisionFailure,
    Prompt,
    Situation,
    read_answer,
    request_body,
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
