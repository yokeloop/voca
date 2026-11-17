import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Choice = Literal["once", "deny"]


@dataclass(frozen=True)
class Vocabulary:
    confirm: tuple[str, ...]
    deny: tuple[str, ...]
    echo_words: int

    def phrases(self) -> dict[str, Choice]:
        found: dict[str, Choice] = {normalize(word): "deny" for word in self.deny}
        found.update({normalize(word): "once" for word in self.confirm})
        return found


@dataclass(frozen=True)
class Phrase:
    choice: Choice
    tail: str


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


def parse(text: str | None, vocabulary: Vocabulary) -> Phrase | None:
    words = re.findall(r"\w+", normalize(text or ""))
    if not words:
        return None
    confirm = {normalize(word) for word in vocabulary.confirm}
    deny = {normalize(word) for word in vocabulary.deny}
    if confirm & set(words) and deny & set(words):
        return None
    choice = vocabulary.phrases().get(words[0])
    if choice is None:
        return None
    found = re.search(r"\w+", text or "")
    assert found is not None
    return Phrase(choice, _strip((text or "")[found.end() :], keep="?"))


def echoes(text: str | None, source: str, vocabulary: Vocabulary) -> bool:
    words = re.findall(r"\w+", normalize(text or ""))
    spoken = " " + " ".join(re.findall(r"\w+", normalize(source))) + " "
    size = vocabulary.echo_words
    return any(
        f" {' '.join(words[i : i + size])} " in spoken
        for i in range(len(words) - size + 1)
    )


def approval_prompt(vocabulary: Vocabulary) -> str:
    return (
        f"Подтвердите на экране или скажите „{vocabulary.confirm[0]}“ "
        f"или „{vocabulary.deny[0]}“."
    )


def unclear_text(vocabulary: Vocabulary) -> str:
    return (
        f"Не разобрал подтверждение. Скажите „{vocabulary.confirm[0]}“ "
        f"или „{vocabulary.deny[0]}“, или ответьте на экране"
    )
