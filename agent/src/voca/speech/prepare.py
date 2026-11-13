import re
from dataclasses import dataclass

from voca.speech.numbers import cardinal, normalize_numbers
from voca.speech.stress import Stress, place

DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class Prepared:
    text: str
    leftover_digits: int


def spell(match: re.Match[str]) -> str:
    words = " ".join(cardinal(int(digit)) for digit in match.group(0))
    text = match.string
    before = " " if match.start() > 0 and text[match.start() - 1].isalnum() else ""
    after = " " if match.end() < len(text) and text[match.end()].isalnum() else ""
    return f"{before}{words}{after}"


def spell_digits(text: str) -> tuple[str, int]:
    return DIGITS.subn(spell, text)


def prepare(sentence: str, stress: Stress) -> Prepared:
    text, leftover = spell_digits(normalize_numbers(sentence))
    return Prepared(place(stress, text), leftover)
