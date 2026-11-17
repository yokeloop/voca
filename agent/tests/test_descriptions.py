import re

from voca.conversation.descriptions import DESCRIPTIONS, describe


def test_known_descriptions() -> None:
    assert describe("delete in root path") == "удаление в корневом каталоге"
    assert describe("recursive delete") == "рекурсивное удаление"
    assert describe("SQL DROP") == "удаление таблицы SQL"
    assert describe("git force push") == "принудительная отправка в git"
    assert (
        describe("script execution via -e/-c flag")
        == "запуск скрипта через флаг -e или -c"
    )
    assert describe("script execution via heredoc") == "запуск скрипта через heredoc"


def test_parenthetical_suffix() -> None:
    assert describe("recursive delete (long flag)") == "рекурсивное удаление"
    assert (
        describe("git force push (rewrites remote history)")
        == "принудительная отправка в git"
    )


def test_arbitrary_program_prefix() -> None:
    assert (
        describe("arbitrary program execution via python3 -c")
        == "запуск произвольной программы"
    )


def test_unknown_is_dangerous_command() -> None:
    for description in ("something new", None, ""):
        assert describe(description) == "опасная команда"


def test_translations_are_russian() -> None:
    for english, russian in DESCRIPTIONS.items():
        assert re.search("[а-яё]", russian), english
