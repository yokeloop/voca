import hashlib
import logging
from pathlib import Path

import pytest

from voca.conversation.memory import HEADER, compose, read_memory


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
