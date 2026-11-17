from voca.conversation.approval import match, normalize


def test_match_once() -> None:
    for text in ("Подтверждаю.", "подтверждаю", " ПОДТВЕРЖДАЮ! ", "«Подтверждаю»"):
        assert match(text) == "once", text


def test_match_deny() -> None:
    for text in ("Отклоняю.", "Отклоняю…"):
        assert match(text) == "deny", text


def test_no_match() -> None:
    for text in (
        "подтверждаю, но позже",
        "да",
        "да, давай",
        "подтверди",
        "подтверждаю подтверждаю",
        "",
        "  ",
        None,
    ):
        assert match(text) is None, text


def test_normalize() -> None:
    assert normalize("  Ёлка!? ") == "елка"
