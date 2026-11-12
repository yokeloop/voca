import asyncio

import pytest
from livekit.agents.tokenize import blingfire

from voca.speech.fragments import FirstFragmentTokenizer, fragment_of

SUNNY = (
    "Сегодня в Москве солнечно, около двадцати градусов. "
    "Вечером возможен небольшой дождь, возьмите зонт."
)
WARM = "Завтра будет тепло, около двадцати градусов. Ветер слабый и южный."
SHORT = "Да, конечно. Сейчас посмотрю погоду в Москве на завтра."
SENTENCE_FIRST = "Готово. Теперь посмотрим, что ещё нужно сделать сегодня вечером."
DECIMAL = "Население около 5,6 миллиона человек, и город растёт каждый год."
TEXTS = [SUNNY, WARM, SHORT, SENTENCE_FIRST, DECIMAL]


def sentences(text: str) -> list[str]:
    return blingfire.SentenceTokenizer(retain_format=True).tokenize(text)


def streamed(tokenizer: FirstFragmentTokenizer, text: str) -> list[str]:
    async def main() -> list[str]:
        stream = tokenizer.stream()
        for char in text:
            stream.push_text(char)
        stream.end_input()
        tokens = [data.token async for data in stream]
        await stream.aclose()
        return tokens

    return asyncio.run(main())


def stripped(tokens: list[str]) -> list[str]:
    return [token.strip() for token in tokens if token.strip()]


def test_first_fragment_split_off() -> None:
    tokens = FirstFragmentTokenizer().tokenize(SUNNY)
    assert tokens[0] == "Сегодня в Москве солнечно,"
    assert stripped(tokens[1:]) == stripped(
        sentences(
            "около двадцати градусов. Вечером возможен небольшой дождь, возьмите зонт."
        )
    )


def test_short_fragment_still_separate() -> None:
    tokens = FirstFragmentTokenizer().tokenize(WARM)
    assert tokens[0] == "Завтра будет тепло,"
    assert len(tokens[0]) < 20


def test_fewer_than_three_words_gives_sentences() -> None:
    assert FirstFragmentTokenizer().tokenize(SHORT) == sentences(SHORT)


def test_sentence_end_before_break_gives_sentences() -> None:
    assert FirstFragmentTokenizer().tokenize(SENTENCE_FIRST) == sentences(
        SENTENCE_FIRST
    )


def test_decimal_comma_not_a_break() -> None:
    assert fragment_of(DECIMAL) == "Население около 5,6 миллиона человек,"


@pytest.mark.parametrize("text", TEXTS)
def test_stream_matches_tokenize(text: str) -> None:
    tokenizer = FirstFragmentTokenizer()
    assert stripped(streamed(tokenizer, text)) == stripped(tokenizer.tokenize(text))


def test_each_stream_cuts_its_own_first_fragment() -> None:
    tokenizer = FirstFragmentTokenizer()
    first = streamed(tokenizer, SUNNY)
    second = streamed(tokenizer, WARM)
    assert first[0] == "Сегодня в Москве солнечно,"
    assert second[0] == "Завтра будет тепло,"
