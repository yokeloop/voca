import hashlib
from pathlib import Path

import pytest

from voca.conversation.instructions import InstructionError, InstructionLoader

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"


def write_set(directory: Path) -> None:
    (directory / "SOUL.md").write_text("Душа.\n", encoding="utf-8")
    (directory / "user.md").write_text("  Пользователь.  ", encoding="utf-8")
    (directory / "agent.md").write_text("Агент.", encoding="utf-8")


def test_first_failure_raises(tmp_path: Path) -> None:
    with pytest.raises(InstructionError, match="SOUL.md"):
        InstructionLoader(tmp_path).load()


def test_fresh_set(tmp_path: Path) -> None:
    write_set(tmp_path)
    loaded = InstructionLoader(tmp_path).load()
    assert loaded.source == "fresh"
    assert loaded.error is None
    assert loaded.text == "Душа.\n\nПользователь.\n\nАгент."
    assert loaded.sha256 == hashlib.sha256(loaded.text.encode()).hexdigest()


def test_late_failure_gives_last_valid(tmp_path: Path) -> None:
    write_set(tmp_path)
    loader = InstructionLoader(tmp_path)
    first = loader.load()
    (tmp_path / "user.md").unlink()
    missing = loader.load()
    assert missing.source == "last_valid"
    assert missing.sha256 == first.sha256
    assert missing.text == first.text
    assert missing.error is not None and "user.md" in missing.error
    (tmp_path / "user.md").write_text("   \n", encoding="utf-8")
    empty = loader.load()
    assert empty.source == "last_valid"
    assert empty.error is not None and "пустой" in empty.error
    (tmp_path / "user.md").write_bytes(b"\xff")
    binary = loader.load()
    assert binary.source == "last_valid"
    assert binary.error is not None and "не UTF-8" in binary.error
    assert loader.last == first


def test_fix_returns_fresh(tmp_path: Path) -> None:
    write_set(tmp_path)
    loader = InstructionLoader(tmp_path)
    first = loader.load()
    (tmp_path / "user.md").unlink()
    assert loader.load().source == "last_valid"
    (tmp_path / "user.md").write_text("Другой пользователь.", encoding="utf-8")
    fixed = loader.load()
    assert fixed.source == "fresh"
    assert fixed.error is None
    assert fixed.sha256 != first.sha256


def test_example_set_is_valid() -> None:
    assert InstructionLoader(EXAMPLE).load().source == "fresh"
