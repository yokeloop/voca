import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "voca"
PURE_MODULES = (
    "delegation/model",
    "delegation/ports",
    "delegation/service",
    "conversation/context",
    "conversation/instructions",
    "speech/markup",
    "speech/numbers",
)
FORBIDDEN = frozenset({"livekit", "httpx", "aiosqlite"})


def forbidden_imports(source: str) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return sorted(names & FORBIDDEN)


def pure_files() -> list[Path]:
    files: list[Path] = []
    for module in PURE_MODULES:
        file = SRC / f"{module}.py"
        if file.is_file():
            files.append(file)
        directory = SRC / module
        if directory.is_dir():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def test_pure_modules_do_not_import_adapters() -> None:
    for file in pure_files():
        assert forbidden_imports(file.read_text()) == [], file


def test_violation_is_caught() -> None:
    source = "import livekit.rtc\nfrom httpx import AsyncClient\nimport json\nfrom . import local\n"
    assert forbidden_imports(source) == ["httpx", "livekit"]


def test_clean_source_passes() -> None:
    assert forbidden_imports("import json\nfrom pathlib import Path\n") == []
