import asyncio
from collections.abc import AsyncIterator, Callable

from voca.speech.markup import (
    TOOL_CALL_END,
    TOOL_CALL_START,
    clean_line,
    strip_latex_and_tables,
    strip_tool_calls,
)

CALL = f'{TOOL_CALL_START}[get_weather(city="Москва")]{TOOL_CALL_END}'
REPLY = f"Сейчас посмотрю. {CALL}В Москве < 20 градусов."
CLEAN = "Сейчас посмотрю. В Москве < 20 градусов."


async def chunks_of(parts: list[str]) -> AsyncIterator[str]:
    for part in parts:
        yield part


def collect(
    stage: Callable[[AsyncIterator[str]], AsyncIterator[str]], parts: list[str]
) -> str:
    async def main() -> str:
        return "".join([chunk async for chunk in stage(chunks_of(parts))])

    return asyncio.run(main())


def test_tool_call_removed_at_every_two_part_split() -> None:
    for cut in range(len(REPLY) + 1):
        assert collect(strip_tool_calls, [REPLY[:cut], REPLY[cut:]]) == CLEAN, cut


def test_tool_call_removed_char_by_char() -> None:
    assert collect(strip_tool_calls, list(REPLY)) == CLEAN


def test_unclosed_tool_call_dropped() -> None:
    assert collect(strip_tool_calls, ["Готово. ", TOOL_CALL_START, "[x("]) == "Готово. "


def test_text_with_angle_bracket_kept() -> None:
    assert collect(strip_tool_calls, ["a <", "| b <", "|tool"]) == "a <| b <|tool"


def test_table_rows_become_cells() -> None:
    table = "| Город | Температура |\n|:---|---:|\n| Москва | 20 |\nКонец"
    assert collect(strip_latex_and_tables, [table]) == (
        "Город, Температура.\nМосква, 20.\nКонец"
    )


def test_table_split_into_chunks() -> None:
    table = "| a | b |\n| --- | --- |\n| c | d |\n"
    assert collect(strip_latex_and_tables, list(table)) == "a, b.\nc, d.\n"


def test_latex_inline_power() -> None:
    assert clean_line("Площадь $x^2$ кв.") == "Площадь x в степени 2 кв."


def test_latex_fraction() -> None:
    assert clean_line(r"Ответ \(\frac{a}{b}\).") == "Ответ a делить на b."


def test_latex_display_and_operators() -> None:
    assert clean_line(r"$$2 \times 3 \cdot x_{1}$$") == "2 умножить на 3 умножить на x1"


def test_latex_brackets_and_braced_power() -> None:
    assert clean_line(r"\[e^{10}\]") == "e в степени 10"


def test_currency_dollars_untouched() -> None:
    line = "Бюджет $4 000, а билет $600."
    assert clean_line(line) == line
