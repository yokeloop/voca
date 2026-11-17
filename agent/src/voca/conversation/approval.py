import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Choice = Literal["once", "deny"]
PHRASES: dict[str, Choice] = {"подтверждаю": "once", "отклоняю": "deny"}
NEAR = 2
REFUSAL_STEMS = (
    "отклон",
    "отказ",
    "откаж",
    "не подтверж",
    "не надо",
    "нельзя",
    "запрет",
    "запрещ",
)
REFUSAL_WORDS = frozenset({"нет"})


@dataclass(frozen=True)
class Phrase:
    choice: Choice
    tail: str


@dataclass(frozen=True)
class NearMiss:
    pass


def _edge(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


def _strip(text: str, keep: str = "") -> str:
    start, end = 0, len(text)
    while start < end and _edge(text[start]):
        start += 1
    while end > start and _edge(text[end - 1]) and text[end - 1] not in keep:
        end -= 1
    return text[start:end]


def normalize(text: str) -> str:
    return _strip(text.lower().replace("ё", "е"))


def distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1]


def parse(text: str | None) -> Phrase | NearMiss | None:
    words = re.findall(r"\w+", normalize(text or ""))
    if not words:
        return None
    if all(any(distance(word, key) <= NEAR for word in words) for key in PHRASES):
        return NearMiss()
    first = words[0]
    choice = PHRASES.get(first)
    if choice is not None:
        found = re.search(r"\w+", text or "")
        assert found is not None
        return Phrase(choice, _strip((text or "")[found.end() :], keep="?"))
    if any(0 < distance(first, key) <= NEAR for key in PHRASES):
        return NearMiss()
    return None


def refuses(text: str | None) -> bool:
    words = re.findall(r"\w+", normalize(text or ""))
    joined = " ".join(words)
    return any(stem in joined for stem in REFUSAL_STEMS) or any(
        word in REFUSAL_WORDS for word in words
    )
