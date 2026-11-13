import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FILES = ("SOUL.md", "user.md", "agent.md")


class InstructionError(Exception):
    pass


@dataclass(frozen=True)
class InstructionSet:
    text: str
    sha256: str
    source: Literal["fresh", "last_valid"]
    error: str | None


class InstructionLoader:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.last: InstructionSet | None = None

    def read(self) -> list[str]:
        parts: list[str] = []
        for name in FILES:
            path = self.directory / name
            try:
                part = path.read_bytes().decode("utf-8").strip()
            except OSError:
                raise InstructionError(f"{path}: нет файла") from None
            except UnicodeDecodeError:
                raise InstructionError(f"{path}: не UTF-8") from None
            if not part:
                raise InstructionError(f"{path}: пустой")
            parts.append(part)
        return parts

    def load(self) -> InstructionSet:
        try:
            parts = self.read()
        except InstructionError as error:
            if self.last is None:
                raise
            return InstructionSet(
                self.last.text, self.last.sha256, "last_valid", str(error)
            )
        text = "\n\n".join(parts)
        self.last = InstructionSet(
            text, hashlib.sha256(text.encode()).hexdigest(), "fresh", None
        )
        return self.last
