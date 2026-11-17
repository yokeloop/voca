from dataclasses import dataclass
from typing import Any, Literal, Protocol

Verdict = Literal["approve", "deny", "repeat", "unrelated", "unclear"]
VERDICTS: tuple[Verdict, ...] = ("approve", "deny", "repeat", "unrelated", "unclear")


@dataclass(frozen=True)
class Prompt:
    instructions: str
    verdicts: dict[str, str]
    extra: str
    threshold: float
    extra_threshold: float


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
    extra: bool
    ms: int


@dataclass(frozen=True)
class DecisionFailure:
    reason: str
    ms: int


class Decider(Protocol):
    async def decide(self, situation: Situation) -> Decision | DecisionFailure: ...


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
            "extra": {"type": "noul", "instructions": prompt.extra},
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
    extra = answers.get("extra")
    if not isinstance(verdict, dict) or not isinstance(extra, dict):
        return failure
    picked = verdict.get("choice")
    confidence = _share(verdict.get("confidence"))
    noul = _share(extra.get("noul"))
    if confidence is None or noul is None:
        return failure
    for name in VERDICTS:
        if picked == name:
            chosen: Verdict = name if confidence >= prompt.threshold else "unclear"
            return Decision(
                chosen, name, confidence, noul >= prompt.extra_threshold, ms
            )
    return failure
