from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voca.app import create_app
from voca.config import Settings
from voca.core.session import StorageUnavailable, StoreClosed


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
