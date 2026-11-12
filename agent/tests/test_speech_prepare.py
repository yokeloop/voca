import json
import re
from pathlib import Path

from voca.speech.prepare import prepare, spell_digits

CORPUS = Path(__file__).resolve().parents[2] / "bench" / "corpus"


def same(text: str) -> str:
    return text


def test_pipeline_answers_leave_no_digits() -> None:
    corpus = json.loads((CORPUS / "tts_pipeline.json").read_text(encoding="utf-8"))
    for item in corpus:
        assert not re.search(r"\d", prepare(item["answer"], same).text), item


def test_leftover_digits_spelled() -> None:
    prepared = prepare("COVID19", same)
    assert prepared.leftover_digits == 1
    assert "один девять" in prepared.text
    assert not re.search(r"\d", prepared.text)


def test_spell_digits_counts_sequences() -> None:
    assert spell_digits("A1 B20") == ("A один B два ноль", 2)
