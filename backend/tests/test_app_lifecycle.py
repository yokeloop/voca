from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voca.application.ports import StorageUnavailable, StoreClosed
from voca.bootstrap.app import create_app
from voca.bootstrap.settings import Settings


def test_app_lifecycle(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "voca.sqlite3", log_dir=tmp_path / "logs")
    app = create_app(settings)
    assert not settings.db_path.exists()
    with TestClient(app) as client:
        assert settings.db_path.exists()
        assert client.post("/api/sessions", json={}).status_code == 201
        store = app.state.store
    with pytest.raises(StoreClosed):
        store.get("any")
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/sessions", json={}).status_code == 201
    logs = (settings.log_dir / "voca.jsonl").read_text().splitlines()
    assert sum("app.startup" in line for line in logs) == 4


def test_unusable_database(tmp_path: Path) -> None:
    database_directory = tmp_path / "database-directory"
    database_directory.mkdir()
    settings = Settings(db_path=database_directory, log_dir=tmp_path / "logs")
    with pytest.raises(StorageUnavailable), TestClient(create_app(settings)):
        pass
    incompatible = tmp_path / "incompatible.sqlite3"
    with sqlite3.connect(incompatible) as database:
        database.execute("PRAGMA user_version=2")
    with (
        pytest.raises(StorageUnavailable),
        TestClient(
            create_app(Settings(db_path=incompatible, log_dir=tmp_path / "other-logs"))
        ),
    ):
        pass
    incompatible_shape = tmp_path / "incompatible-shape.sqlite3"
    with sqlite3.connect(incompatible_shape) as database:
        database.execute(
            "CREATE TABLE sessions(session_id TEXT PRIMARY KEY, created_at TEXT, event_cursor TEXT)"
        )
        database.execute("PRAGMA user_version=1")
    with (
        pytest.raises(StorageUnavailable),
        TestClient(
            create_app(
                Settings(db_path=incompatible_shape, log_dir=tmp_path / "shape-logs")
            )
        ),
    ):
        pass


def test_import_boundaries() -> None:
    source_root = Path(__file__).parents[1] / "src" / "voca"
    for path in source_root.rglob("*.py"):
        imports = [
            alias.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Import)
            for alias in node.names
        ] + [
            node.module or ""
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom)
        ]
        relative = path.relative_to(source_root).as_posix()
        if relative.startswith(("domain/", "application/")):
            assert not any(
                name.startswith(
                    (
                        "fastapi",
                        "pydantic",
                        "starlette",
                        "sqlite3",
                        "logging",
                        "voca.presentation",
                        "voca.infrastructure",
                        "voca.bootstrap",
                    )
                )
                for name in imports
            )
        if relative.startswith("presentation/"):
            assert not any(
                name.startswith(("voca.infrastructure", "voca.bootstrap"))
                for name in imports
            )
        if relative.startswith("infrastructure/"):
            assert not any(
                name.startswith(("voca.presentation", "voca.bootstrap"))
                for name in imports
            )
