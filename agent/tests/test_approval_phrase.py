from voca.conversation.approval import (
    NearMiss,
    Phrase,
    distance,
    normalize,
    parse,
    refuses,
)


def test_parse_whole_phrase() -> None:
    for text in ("Подтверждаю.", "подтверждаю", " ПОДТВЕРЖДАЮ! ", "«Подтверждаю»"):
        assert parse(text) == Phrase("once", ""), text
    for text in ("Отклоняю.", "Отклоняю…"):
        assert parse(text) == Phrase("deny", ""), text


def test_parse_tail() -> None:
    assert parse("Отклоняю, отмени вообще задачу.") == Phrase(
        "deny", "отмени вообще задачу"
    )
    assert parse("Подтверждаю. А что дальше?") == Phrase("once", "А что дальше?")
    assert parse("Подтверждаю! Ёлка.") == Phrase("once", "Ёлка")


def test_parse_near_miss() -> None:
    for text in ("подверждаю", "Подтверждаем", "отклоню"):
        assert parse(text) == NearMiss(), text


def test_parse_both_keywords() -> None:
    for text in (
        "Подтверждаю или отклоняю",
        "Подтвердите на экране или скажите „Подтверждаю“ или „Отклоняю“.",
    ):
        assert parse(text) == NearMiss(), text


def test_parse_other() -> None:
    for text in ("да", "да, давай", "подтверди", "отмени задачу", "", "  ", None):
        assert parse(text) is None, text


def test_distance() -> None:
    assert distance("подтверждаю", "подтверждаю") == 0
    assert distance("подверждаю", "подтверждаю") == 1
    assert distance("", "abc") == 3


def test_normalize() -> None:
    assert normalize("  Ёлка!? ") == "елка"


def test_refuses() -> None:
    for text in (
        "Отклони запрос",
        "Откажись",
        "Не подтверждай",
        "не надо",
        "Нельзя это делать",
        "Запрещаю",
        "Нет.",
        "Ну нет, конечно",
    ):
        assert refuses(text), text
    for text in (
        "Ну окей, давай подтвердим его.",
        "Подтверждаю",
        "Нетрудно, давай",
        "А что там за скрипт?",
        "",
        None,
    ):
        assert not refuses(text), text
