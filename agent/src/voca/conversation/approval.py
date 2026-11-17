import unicodedata
from typing import Literal

Choice = Literal["once", "deny"]
PHRASES: dict[str, Choice] = {"подтверждаю": "once", "отклоняю": "deny"}


def _edge(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    start, end = 0, len(text)
    while start < end and _edge(text[start]):
        start += 1
    while end > start and _edge(text[end - 1]):
        end -= 1
    return text[start:end]


def match(text: str | None) -> Choice | None:
    return PHRASES.get(normalize(text or ""))
