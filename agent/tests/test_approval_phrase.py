from voca.config import ApprovalConfig
from voca.conversation.approval import (
    Phrase,
    Vocabulary,
    approval_prompt,
    echoes,
    normalize,
    parse,
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


def test_parse_both_keywords() -> None:
    for text in (
        "Подтверждаю или отклоняю",
        "Подтвердите на экране или скажите „Подтверждаю“ или „Отклоняю“.",
    ):
        assert parse(text, VOCABULARY) is None, text


def test_parse_near_word_is_none() -> None:
    for text in ("подверждаю", "Подтверждаем", "отклоню"):
        assert parse(text, VOCABULARY) is None, text


def test_parse_other() -> None:
    for text in ("да", "да, давай", "подтверди", "отмени задачу", "", "  ", None):
        assert parse(text, VOCABULARY) is None, text


def test_normalize() -> None:
    assert normalize("  Ёлка!? ") == "елка"


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
    echo_words=2,
)


def test_custom_vocabulary() -> None:
    assert parse("Принимаю.", CUSTOM) == Phrase("once", "")
    assert parse("Запрещаю, хватит", CUSTOM) == Phrase("deny", "хватит")
    assert parse("Подтверждаю", CUSTOM) is None
    assert parse("Принимаем", CUSTOM) is None
    assert parse("Принимаю", CUSTOM) == Phrase("once", "")
    assert parse("Принемаю", CUSTOM) is None
    assert parse("Принимаю или запрещаю", CUSTOM) is None
    assert echoes("скажите принимаю", "Скажите „Принимаю“", CUSTOM)
    assert approval_prompt(CUSTOM) == (
        "Подтвердите на экране или скажите „Принимаю“ или „Запрещаю“."
    )
    assert "„Принимаю“" in unclear_text(CUSTOM)
