import re
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

Kind = Literal["noise", "reply"]
KINDS: tuple[Kind, ...] = ("noise", "reply")
Relation = Literal["answers", "asks", "other"]
RELATIONS: tuple[Relation, ...] = ("answers", "asks", "other")
Reply = Literal["approve", "deny"]
REPLIES: tuple[Reply, ...] = ("approve", "deny")


@dataclass(frozen=True)
class DecisionFailure:
    reason: str
    ms: int


@dataclass(frozen=True)
class SorterPrompt:
    instructions: str
    criteria: dict[str, str]
    keep_words: frozenset[str]
    max_words: int
    min_words: int
    relation_instructions: str
    relation_criteria: dict[str, str]
    answer_instructions: str
    answer_criteria: dict[str, str]
    target_instructions: str
    target_request: str
    target_unclear: str


@dataclass(frozen=True)
class Sorted:
    kind: Kind
    confidence: float
    ms: int
    relation: Relation | None = None
    relation_confidence: float = 0.0
    answer: Reply | None = None
    answer_confidence: float = 0.0
    target: int | None = None
    target_confidence: float = 0.0


class Sorter(Protocol):
    async def sort(
        self, state: dict[str, str], requests: int = 0
    ) -> Sorted | DecisionFailure: ...


def _share(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)


def sorter_state(
    agent_last: str, requests: list[tuple[str, str | None]], phrase: str
) -> dict[str, str]:
    state = {"последняя фраза агента": agent_last}
    if len(requests) == 1:
        description, command = requests[0]
        state["запрос"] = description
        if command is not None:
            state["команда"] = command
    else:
        for number, (description, command) in enumerate(requests, 1):
            state[f"запрос {number}"] = description
            if command is not None:
                state[f"команда {number}"] = command
    state["фраза пользователя"] = phrase
    return state


def sorter_body(
    prompt: SorterPrompt, state: dict[str, str], model: str, requests: int = 0
) -> dict[str, Any]:
    questions: dict[str, Any] = {
        "kind": {
            "type": "choice",
            "instructions": prompt.instructions,
            "criteria": {name: prompt.criteria[name] for name in KINDS},
        },
    }
    if requests >= 1:
        questions["relation"] = {
            "type": "choice",
            "instructions": prompt.relation_instructions,
            "criteria": {name: prompt.relation_criteria[name] for name in RELATIONS},
        }
        questions["answer"] = {
            "type": "choice",
            "instructions": prompt.answer_instructions,
            "criteria": {name: prompt.answer_criteria[name] for name in REPLIES},
        }
    if requests >= 2:
        questions["target"] = {
            "type": "choice",
            "instructions": prompt.target_instructions,
            "criteria": {
                **{
                    f"request_{n}": prompt.target_request.format(n=n)
                    for n in range(1, requests + 1)
                },
                "unclear": prompt.target_unclear,
            },
        }
    return {"model": model, "state": state, "questions": questions}


def _pick(
    answers: dict[str, Any], name: str, names: tuple[str, ...]
) -> tuple[str, float] | None:
    answer = answers.get(name)
    if not isinstance(answer, dict):
        return None
    picked = answer.get("choice")
    confidence = _share(answer.get("confidence"))
    if confidence is None or picked not in names:
        return None
    return picked, confidence


def read_sort(data: object, ms: int, requests: int = 0) -> Sorted | DecisionFailure:
    failure = DecisionFailure("bad_answer", ms)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return failure
    kind = _pick(answers, "kind", KINDS)
    if kind is None:
        return failure
    result = Sorted(cast(Kind, kind[0]), kind[1], ms)
    if requests >= 1:
        relation = _pick(answers, "relation", RELATIONS)
        answer = _pick(answers, "answer", REPLIES)
        if relation is None or answer is None:
            return failure
        result = replace(
            result,
            relation=cast(Relation, relation[0]),
            relation_confidence=relation[1],
            answer=cast(Reply, answer[0]),
            answer_confidence=answer[1],
        )
    if requests >= 2:
        names = (*(f"request_{n}" for n in range(1, requests + 1)), "unclear")
        target = _pick(answers, "target", names)
        if target is None:
            return failure
        number = (
            None if target[0] == "unclear" else int(target[0].removeprefix("request_"))
        )
        result = replace(result, target=number, target_confidence=target[1])
    return result


def words(text: str) -> list[str]:
    return re.findall(r"\w+(?:-\w+)*", text.lower().replace("ё", "е"))


def drop(
    text: str,
    result: Sorted | DecisionFailure,
    prompt: SorterPrompt,
    *,
    speaking: bool,
) -> bool:
    found = words(text)
    if prompt.keep_words.intersection(found):
        return False
    if isinstance(result, Sorted):
        return result.kind == "noise" and len(found) <= prompt.max_words
    return speaking and len(found) < prompt.min_words
