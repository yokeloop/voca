import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from voca.speech.markup import strip_latex_and_tables, strip_tool_calls
from voca.speech.prepare import prepare, spell_digits

CORPUS = Path(__file__).resolve().parents[2] / "bench" / "corpus"


def same(text: str) -> str:
    return text


def test_pipeline_answers_leave_no_digits() -> None:
    corpus = json.loads((CORPUS / "tts_pipeline.json").read_text(encoding="utf-8"))
    for item in corpus:
        assert not re.search(r"\d", prepare(item["answer"], same).text), item


async def whole(text: str) -> AsyncIterator[str]:
    yield text


def cleaned(text: str) -> str:
    async def main() -> str:
        chain = strip_latex_and_tables(strip_tool_calls(whole(text)))
        return "".join([chunk async for chunk in chain])

    return asyncio.run(main())


def test_reply_corpus_leaves_no_digits() -> None:
    corpus = json.loads((CORPUS / "tts_prepare.json").read_text(encoding="utf-8"))
    for item in corpus["replies"]:
        text = prepare(cleaned(item["reply"]), same).text
        assert not re.search(r"\d", text), item["id"]


def test_leftover_digits_spelled() -> None:
    prepared = prepare("COVID19", same)
    assert prepared.leftover_digits == 1
    assert "один девять" in prepared.text
    assert not re.search(r"\d", prepared.text)


def test_spell_digits_counts_sequences() -> None:
    assert spell_digits("A1 B20") == ("A один B два ноль", 2)
