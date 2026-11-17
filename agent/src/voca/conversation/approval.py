import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Choice = Literal["once", "deny"]


@dataclass(frozen=True)
class Vocabulary:
    confirm: tuple[str, ...]
    deny: tuple[str, ...]
    refusal_stems: tuple[str, ...]
    refusal_words: tuple[str, ...]
    near: int
    echo_words: int

    def phrases(self) -> dict[str, Choice]:
        found: dict[str, Choice] = {normalize(word): "deny" for word in self.deny}
        found.update({normalize(word): "once" for word in self.confirm})
        return found


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


def _near(word: str, keys: tuple[str, ...], limit: int) -> bool:
    return any(distance(word, normalize(key)) <= limit for key in keys)


def parse(text: str | None, vocabulary: Vocabulary) -> Phrase | NearMiss | None:
    words = re.findall(r"\w+", normalize(text or ""))
    if not words:
        return None
    limit = vocabulary.near
    if any(_near(word, vocabulary.confirm, limit) for word in words) and any(
        _near(word, vocabulary.deny, limit) for word in words
    ):
        return NearMiss()
    phrases = vocabulary.phrases()
    first = words[0]
    choice = phrases.get(first)
    if choice is not None:
        found = re.search(r"\w+", text or "")
        assert found is not None
        return Phrase(choice, _strip((text or "")[found.end() :], keep="?"))
    if any(0 < distance(first, key) <= limit for key in phrases):
        return NearMiss()
    return None


def refuses(text: str | None, vocabulary: Vocabulary) -> bool:
    words = re.findall(r"\w+", normalize(text or ""))
    joined = " ".join(words)
    refusals = {normalize(word) for word in vocabulary.refusal_words}
    return any(normalize(stem) in joined for stem in vocabulary.refusal_stems) or any(
        word in refusals for word in words
    )


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


def not_denied_reason(vocabulary: Vocabulary) -> str:
    return (
        "пользователь не просил отклонять; подтвердить можно только кнопкой "
        f"«Подтвердить» или словом «{vocabulary.confirm[0]}»"
    )
