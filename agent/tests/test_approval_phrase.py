from voca.config import ApprovalConfig
from voca.conversation.approval import (
    NearMiss,
    Phrase,
    Vocabulary,
    approval_prompt,
    distance,
    echoes,
    normalize,
    not_denied_reason,
    parse,
    refuses,
    unclear_text,
)

VOCABULARY = ApprovalConfig().vocabulary()


def test_parse_whole_phrase() -> None:
    for text in ("Подтверждаю.", "подтверждаю", " ПОДТВЕРЖДАЮ! ", "«Подтверждаю»"):
        assert parse(text, VOCABULARY) == Phrase("once", ""), text
    for text in ("Отклоняю.", "Отклоняю…"):
        assert parse(text, VOCABULARY) == Phrase("deny", ""), text


def test_parse_tail() -> None:
    assert parse("Отклоняю, отмени вообще задачу.", VOCABULARY) == Phrase(
        "deny", "отмени вообще задачу"
    )
    assert parse("Подтверждаю. А что дальше?", VOCABULARY) == Phrase(
        "once", "А что дальше?"
    )
    assert parse("Подтверждаю! Ёлка.", VOCABULARY) == Phrase("once", "Ёлка")


def test_parse_near_miss() -> None:
    for text in ("подверждаю", "Подтверждаем", "отклоню"):
        assert parse(text, VOCABULARY) == NearMiss(), text


def test_parse_both_keywords() -> None:
    for text in (
        "Подтверждаю или отклоняю",
        "Подтвердите на экране или скажите „Подтверждаю“ или „Отклоняю“.",
    ):
        assert parse(text, VOCABULARY) == NearMiss(), text


def test_parse_other() -> None:
    for text in ("да", "да, давай", "подтверди", "отмени задачу", "", "  ", None):
        assert parse(text, VOCABULARY) is None, text


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
        assert refuses(text, VOCABULARY), text
    for text in (
        "Ну окей, давай подтвердим его.",
        "Подтверждаю",
        "Нетрудно, давай",
        "А что там за скрипт?",
        "",
        None,
    ):
        assert not refuses(text, VOCABULARY), text


def test_echoes() -> None:
    source = (
        "Hermes просит подтверждения по поручению «Удали каталог /tmp/x»: "
        "удаление в корневом каталоге. Подтвердите на экране или скажите "
        "„Подтверждаю“ или „Отклоняю“."
    )
    assert echoes("Подтверждаю по поручению «Удали каталог", source, VOCABULARY)
    assert echoes("на экране или скажите", source, VOCABULARY)
    assert not echoes("Подтверждаю.", source, VOCABULARY)
    assert not echoes("Подтверждаю. А что дальше?", source, VOCABULARY)
    assert not echoes("Подтверждаю", "", VOCABULARY)


CUSTOM = Vocabulary(
    confirm=("Принимаю",),
    deny=("Запрещаю",),
    refusal_stems=("стоп",),
    refusal_words=("никак",),
    near=1,
    echo_words=2,
)


def test_custom_vocabulary() -> None:
    assert parse("Принимаю.", CUSTOM) == Phrase("once", "")
    assert parse("Запрещаю, хватит", CUSTOM) == Phrase("deny", "хватит")
    assert parse("Подтверждаю", CUSTOM) is None
    assert parse("Принимаем", CUSTOM) is None
    assert parse("Принимаю", CUSTOM) == Phrase("once", "")
    assert parse("Принемаю", CUSTOM) == NearMiss()
    assert parse("Принимаю или запрещаю", CUSTOM) == NearMiss()
    assert refuses("Стоп, не надо", CUSTOM)
    assert refuses("никак", CUSTOM)
    assert not refuses("нет", CUSTOM)
    assert echoes("скажите принимаю", "Скажите „Принимаю“", CUSTOM)
    assert approval_prompt(CUSTOM) == (
        "Подтвердите на экране или скажите „Принимаю“ или „Запрещаю“."
    )
    assert "„Принимаю“" in unclear_text(CUSTOM)
    assert "«Принимаю»" in not_denied_reason(CUSTOM)
