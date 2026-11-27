import hashlib
import logging
from datetime import date
from pathlib import Path

import pytest

from voca.conversation.memory import (
    HEADER,
    Entry,
    Op,
    apply_ops,
    build_extract_prompt,
    build_retry_prompt,
    compose,
    fits,
    parse_file,
    parse_ops,
    read_memory,
    render_entries,
    render_file,
)
from voca.session.model import ArchiveRow

TEXT = "## О пользователе\n- Живёт в Казани\n- Собаку зовут Бублик\n\n## Заметки\n- Любит чай"

ROWS = [
    ArchiveRow("user", "u1", 0, None, None, "Моего кота зовут Ёжик", {}),
    ArchiveRow("assistant", "a1", 1, None, None, "Запомнил.", {"interrupted": False}),
    ArchiveRow("assistant", "a2", 2, None, None, "  ", {"interrupted": True}),
]


def test_compose_empty_memory_keeps_instructions() -> None:
    assert compose("И", "") == "И"


def test_compose_appends_block() -> None:
    assert (
        compose("И", "- Кота зовут Ёжик.") == "И\n\n" + HEADER + "\n- Кота зовут Ёжик."
    )


def test_read_memory_missing_file(tmp_path: Path) -> None:
    memory = read_memory(tmp_path / "memory.md")
    assert memory.text == ""
    assert memory.sha256 == hashlib.sha256(b"").hexdigest()


def test_read_memory_empty_and_blank(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    for content in ("", "  \n\n "):
        path.write_text(content, encoding="utf-8")
        assert read_memory(path).text == ""


def test_read_memory_strips_text(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    path.write_text("\n## О пользователе\n- Кота зовут Ёжик.\n\n", encoding="utf-8")
    memory = read_memory(path)
    assert memory.text == "## О пользователе\n- Кота зовут Ёжик."
    assert memory.sha256 == hashlib.sha256(memory.text.encode()).hexdigest()


def test_read_memory_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    path.write_bytes(b"\xff\xfe- \xd0\x81\n")
    assert "�" in read_memory(path).text


def test_read_memory_unreadable_path_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="voca.memory"):
        memory = read_memory(tmp_path)
    assert memory.text == ""
    assert any(
        record.name == "voca.memory" and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_fits() -> None:
    assert fits("а" * 2200, 2200) is True
    assert fits("а" * 2201, 2200) is False


def test_parse_render_round_trip() -> None:
    entries = parse_file(TEXT)
    assert render_file(entries) == TEXT
    assert [e.id for e in entries] == [1, 2, 3]
    assert [e.section for e in entries] == ["user", "user", "notes"]


def test_parse_hand_written() -> None:
    entries = parse_file(
        "Без заголовка\n# Заметки\n* звёздочкой\n\n## Прочее\n  - под чужим   \n"
        "## О пользователе\n-  с пробелами\nпросто строка\n-\n"
    )
    assert entries == [
        Entry(1, "user", "с пробелами"),
        Entry(2, "user", "просто строка"),
        Entry(3, "user", "-"),
        Entry(4, "notes", "Без заголовка"),
        Entry(5, "notes", "звёздочкой"),
        Entry(6, "notes", "под чужим"),
    ]


def test_render_empty() -> None:
    assert render_file([]) == "## О пользователе\n\n## Заметки"
    assert render_entries([]) == "(файл пуст)"


def test_parse_ops_valid() -> None:
    entries = parse_file(TEXT)
    body = (
        '```json\n{"operations": [{"op": "add", "section": "notes", "text": "a\\nb"}, '
        '{"op": "replace", "id": 1, "text": "Живёт в Москве"}, '
        '{"op": "remove", "id": 2}], "note": 1}\n```'
    )
    assert parse_ops(body, entries) == [
        Op("add", section="notes", text="a b"),
        Op("replace", id=1, text="Живёт в Москве"),
        Op("remove", id=2),
    ]
    assert parse_ops('{"operations": []}', entries) == []


@pytest.mark.parametrize(
    "body",
    [
        "не json",
        "[]",
        '{"ops": []}',
        '{"operations": {}}',
        '{"operations": ["add"]}',
        '{"operations": [{"op": "edit", "id": 1}]}',
        '{"operations": [{"op": "remove", "id": 9}]}',
        '{"operations": [{"op": "remove", "id": true}]}',
        '{"operations": [{"op": "remove", "id": "1"}]}',
        '{"operations": [{"op": "remove", "id": 1}, {"op": "replace", "id": 1, "text": "x"}]}',
        '{"operations": [{"op": "add", "section": "user", "text": "  \\n "}]}',
        '{"operations": [{"op": "add", "section": "misc", "text": "x"}]}',
        '{"operations": [{"op": "replace", "id": 1}]}',
    ],
)
def test_parse_ops_bad(body: str) -> None:
    assert parse_ops(body, parse_file(TEXT)) is None


def test_apply_ops() -> None:
    entries = parse_file(TEXT)
    text = render_file(
        apply_ops(
            entries,
            [
                Op("add", section="user", text="Есть кот"),
                Op("replace", id=3, text="Любит кофе"),
                Op("remove", id=2),
            ],
        )
    )
    assert (
        text
        == "## О пользователе\n- Живёт в Казани\n- Есть кот\n\n## Заметки\n- Любит кофе"
    )
    assert text.splitlines()[1] == TEXT.splitlines()[1]


def test_extract_prompt() -> None:
    entries = parse_file("## О пользователе\n- живёт в Казани\n\n## Заметки\n")
    prompt = build_extract_prompt(
        entries, ROWS, today=date(2026, 11, 24), max_chars=2200
    )
    for part in (
        "[1] (user) живёт в Казани",
        "ПОЛЬЗОВАТЕЛЬ: Моего кота зовут Ёжик",
        "АССИСТЕНТ: Запомнил.",
        "Сегодня 2026-11-24.",
        "не длиннее 2200 символов",
        f"сейчас в нём {len(render_file(entries))}",
        '{"op": "remove", "id": N}',
        '{"operations": []}',
        "[СКРЫТО]",
        "«забудь…»",
        "Никогда не убирай то, что по-прежнему верно",
    ):
        assert part in prompt
    assert prompt.count("АССИСТЕНТ:") == 1


def test_extract_prompt_empty_file() -> None:
    prompt = build_extract_prompt([], ROWS, today=date(2026, 11, 24), max_chars=2200)
    assert "ТЕКУЩИЕ ЗАПИСИ:\n(файл пуст)" in prompt


def test_retry_prompt() -> None:
    prompt = build_retry_prompt("P", "R", 37)
    assert prompt.startswith("P\n\nТВОЙ ПРОШЛЫЙ ОТВЕТ:\nR\n\n")
    for part in (
        "длиннее предела на 37 символов",
        "не меньше 37 символов",
        "сначала убирай из «Заметок»",
    ):
        assert part in prompt
