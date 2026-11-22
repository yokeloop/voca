import re
from dataclasses import dataclass
from typing import Literal

from voca.conversation.verdict import DecisionFailure, Sorted, words
from voca.delegation.danger import Danger

Binding = Literal["content", "place", "none"]
Outcome = Literal[
    "approve",
    "deny",
    "confirm",
    "clarify_which",
    "clarify_sure",
    "left",
    "asks",
    "pass",
    "unavailable",
]
PlaceKind = Literal["essence", "asks", "clarify", "confirm"]

STEM = 4
STOP_WORDS = frozenset(
    {
        "через",
        "всех",
        "которая",
        "может",
        "прямо",
        "сразу",
        "после",
        "команда",
        "команды",
        "команду",
    }
)
DENY_WORDS = frozenset({"не", "нет", "ни", "нельзя"})
DENY_STEMS = ("отклон", "отмен", "запрещ")
PACKAGE_VERBS = frozenset({"install", "remove", "purge", "uninstall", "erase"})
SENTENCE_END = re.compile(r"[.!?…](\s|$)")


@dataclass(frozen=True)
class Request:
    id: str
    danger: Danger
    essence: bool
    request: str
    command: str | None


@dataclass(frozen=True)
class Place:
    ids: tuple[str, ...]
    kind: PlaceKind


@dataclass(frozen=True)
class Rules:
    low: float = 0.45
    medium: float = 0.50
    high: float = 0.50
    deny: float = 0.45
    clarify_limit: int = 2

    def threshold(self, danger: Danger) -> float:
        return {
            Danger.LOW: self.low,
            Danger.MEDIUM: self.medium,
            Danger.HIGH: self.high,
        }[danger]


@dataclass(frozen=True)
class Judgement:
    outcome: Outcome
    ids: tuple[str, ...]
    binding: Binding = "none"
    confidence: float | None = None
    threshold: float | None = None


def stems(text: str) -> set[str]:
    return {
        word[:STEM]
        for word in words(text)
        if len(word) >= STEM and word not in STOP_WORDS
    }


def deny_marked(text: str) -> bool:
    return any(
        word in DENY_WORDS or word.startswith(DENY_STEMS) for word in words(text)
    )


def interrogative(text: str) -> bool:
    return "?" in text or "ли" in words(text)


def objects(command: str | None) -> set[str]:
    if not command:
        return set()
    found: set[str] = set()
    after_verb = False
    for word in command.lower().split():
        if "/" in word:
            parts = [part for part in word.strip("'\"").split("/") if part]
            if parts:
                found.add(parts[-1].strip("'\""))
        if after_verb and not word.startswith("-"):
            found.add(word.strip("'\""))
        if word in PACKAGE_VERBS:
            after_verb = True
    return found


def mentions(text: str, request: Request) -> bool:
    if stems(text) & stems(request.request):
        return True
    said = words(text)
    for item in objects(request.command):
        for word in said:
            if word == item:
                return True
            if len(word) >= STEM and len(item) >= STEM and word[:STEM] == item[:STEM]:
                return True
    return False


def essence_spoken(played: str, interrupted: bool) -> bool:
    if not played.strip():
        return False
    return not interrupted or SENTENCE_END.search(played) is not None


def _limited(judgement: Judgement, rules: Rules, clarified: int) -> Judgement:
    if (
        judgement.outcome in ("clarify_which", "clarify_sure", "confirm")
        and clarified >= rules.clarify_limit
    ):
        return Judgement(
            "left",
            judgement.ids,
            judgement.binding,
            judgement.confidence,
            judgement.threshold,
        )
    return judgement


def judge(
    text: str,
    requests: list[Request],
    sorted: Sorted | DecisionFailure | None,
    place: Place | None,
    rules: Rules,
    clarified: int,
) -> Judgement:
    every = tuple(request.id for request in requests)
    placed = place.ids if place is not None else ()
    if (
        not isinstance(sorted, Sorted)
        or sorted.relation is None
        or sorted.answer is None
    ):
        return Judgement("unavailable", every)
    if sorted.kind == "noise" or sorted.relation == "other":
        return Judgement("pass", placed)
    content = [request for request in requests if mentions(text, request)]
    if sorted.relation == "asks":
        ids = tuple(request.id for request in content) or placed or every
        return Judgement("asks", ids)
    if len(content) > 1:
        which = tuple(request.id for request in content)
        return _limited(Judgement("clarify_which", which), rules, clarified)
    if len(requests) == 1:
        candidate, share = requests[0], 1.0
    elif content:
        candidate = content[0]
        number = requests.index(candidate) + 1
        if sorted.target is not None and sorted.target != number:
            return _limited(Judgement("clarify_which", every), rules, clarified)
        share = sorted.target_confidence if sorted.target == number else 1.0
    elif sorted.target is None:
        return _limited(Judgement("clarify_which", every), rules, clarified)
    else:
        candidate, share = requests[sorted.target - 1], sorted.target_confidence
    binding: Binding = (
        "content"
        if candidate in content
        else "place"
        if place is not None and candidate.id in place.ids
        else "none"
    )
    confidence = (
        sorted.confidence
        * sorted.relation_confidence
        * sorted.answer_confidence
        * share
    )
    one = (candidate.id,)
    if sorted.answer == "deny":
        if not deny_marked(text):
            if interrogative(text):
                ids = tuple(request.id for request in content) or placed or every
                return Judgement("asks", ids)
            unsure = Judgement("clarify_sure", one, binding, confidence, rules.deny)
            return _limited(unsure, rules, clarified)
        if binding == "none" and len(requests) > 1:
            return Judgement("pass", (), binding, confidence)
        outcome: Outcome = "deny" if confidence >= rules.deny else "clarify_sure"
        verdict = Judgement(outcome, one, binding, confidence, rules.deny)
        return _limited(verdict, rules, clarified)
    if not (binding == "content" or (binding == "place" and candidate.essence)):
        return Judgement("pass", placed, binding, confidence)
    threshold = rules.threshold(candidate.danger)
    if confidence < threshold:
        outcome = "clarify_sure"
    elif (
        candidate.danger == Danger.HIGH
        and binding == "place"
        and place is not None
        and place.kind != "confirm"
    ):
        outcome = "confirm"
    else:
        outcome = "approve"
    return _limited(
        Judgement(outcome, one, binding, confidence, threshold), rules, clarified
    )
