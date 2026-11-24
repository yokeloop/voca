import hashlib
import logging
from datetime import date
from pathlib import Path

import pytest

from voca.conversation.memory import (
    HEADER,
    build_extract_prompt,
    compose,
    due,
    fits,
    read_memory,
)
from voca.session.model import ArchiveRow

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


def test_due() -> None:
    assert due(0, None, 1) is True
    assert due(10**6, None, 0) is False
    assert due(10**6, 0, 0) is False
    assert due(3599, 0, 1) is False
    assert due(3600, 0, 1) is True
    assert due(900, 0, 0.25) is True


def test_fits() -> None:
    assert fits("а" * 2200, 2200) is True
    assert fits("а" * 2201, 2200) is False


def test_extract_prompt() -> None:
    prompt = build_extract_prompt(
        "## О пользователе\n- живёт в Казани\n\n## Заметки\n",
        ROWS,
        today=date(2026, 11, 24),
        max_chars=2200,
    )
    for part in (
        "ПОЛЬЗОВАТЕЛЬ: Моего кота зовут Ёжик",
        "АССИСТЕНТ: Запомнил.",
        "- живёт в Казани",
        "Сегодня 2026-11-24.",
        "не длиннее 2200 символов",
        "«## О пользователе» и «## Заметки»",
        "[СКРЫТО]",
        "«забудь…»",
    ):
        assert part in prompt
    assert prompt.count("АССИСТЕНТ:") == 1


def test_extract_prompt_empty_file() -> None:
    prompt = build_extract_prompt(
        "  \n", ROWS, today=date(2026, 11, 24), max_chars=2200
    )
    assert "ТЕКУЩИЙ ФАЙЛ:\n(файл пуст)" in prompt
