import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Verdict = Literal[
    "approve", "approve_more", "deny", "deny_more", "repeat", "unrelated", "unclear"
]
VERDICTS: tuple[Verdict, ...] = (
    "approve",
    "approve_more",
    "deny",
    "deny_more",
    "repeat",
    "unrelated",
    "unclear",
)


Kind = Literal["noise", "reply"]
KINDS: tuple[Kind, ...] = ("noise", "reply")


@dataclass(frozen=True)
class Prompt:
    instructions: str
    verdicts: dict[str, str]
    threshold: float


@dataclass(frozen=True)
class Situation:
    task: str
    request: str
    command: str | None
    announcement: str
    phrase: str


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    picked: str
    confidence: float
    ms: int


@dataclass(frozen=True)
class DecisionFailure:
    reason: str
    ms: int


class Decider(Protocol):
    async def decide(self, situation: Situation) -> Decision | DecisionFailure: ...


@dataclass(frozen=True)
class SorterPrompt:
    instructions: str
    criteria: dict[str, str]
    keep_words: frozenset[str]
    max_words: int
    min_words: int


@dataclass(frozen=True)
class Sorted:
    kind: Kind
    confidence: float
    ms: int


class Sorter(Protocol):
    async def sort(self, state: dict[str, str]) -> Sorted | DecisionFailure: ...


def request_body(prompt: Prompt, situation: Situation, model: str) -> dict[str, Any]:
    state: dict[str, str] = {
        "поручение": situation.task,
        "запрос": situation.request,
    }
    if situation.command is not None:
        state["команда"] = situation.command
    state["объявление"] = situation.announcement
    state["фраза пользователя"] = situation.phrase
    return {
        "model": model,
        "state": state,
        "questions": {
            "verdict": {
                "type": "choice",
                "instructions": prompt.instructions,
                "criteria": {name: prompt.verdicts[name] for name in VERDICTS},
            },
        },
    }


def _share(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)


def read_answer(data: object, prompt: Prompt, ms: int) -> Decision | DecisionFailure:
    failure = DecisionFailure("bad_answer", ms)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return failure
    verdict = answers.get("verdict")
    if not isinstance(verdict, dict):
        return failure
    picked = verdict.get("choice")
    confidence = _share(verdict.get("confidence"))
    if confidence is None:
        return failure
    for name in VERDICTS:
        if picked == name:
            chosen: Verdict = name if confidence >= prompt.threshold else "unclear"
            return Decision(chosen, name, confidence, ms)
    return failure


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
    prompt: SorterPrompt, state: dict[str, str], model: str
) -> dict[str, Any]:
    return {
        "model": model,
        "state": state,
        "questions": {
            "kind": {
                "type": "choice",
                "instructions": prompt.instructions,
                "criteria": {name: prompt.criteria[name] for name in KINDS},
            },
        },
    }


def read_sort(data: object, ms: int) -> Sorted | DecisionFailure:
    failure = DecisionFailure("bad_answer", ms)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return failure
    kind = answers.get("kind")
    if not isinstance(kind, dict):
        return failure
    picked = kind.get("choice")
    confidence = _share(kind.get("confidence"))
    if confidence is None:
        return failure
    for name in KINDS:
        if picked == name:
            return Sorted(name, confidence, ms)
    return failure


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
