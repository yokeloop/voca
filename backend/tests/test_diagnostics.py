from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voca.bootstrap.app import create_app
from voca.bootstrap.settings import Settings
from voca.infrastructure.observability.logging import configure_logging


def entries(log_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for path in log_dir.glob("voca.jsonl*")
        for line in path.read_text().splitlines()
    ]


def test_json_fields_filtering_and_rotation(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    diagnostics = configure_logging(log_dir, "INFO", {"storage": "ERROR"})
    with diagnostics.operation("session", "session.resume", session_id="session"):
        pass
    diagnostics.log_event(
        "storage", "storage.cursor_advance", "succeeded", session_id="session"
    )
    for _ in range(3000):
        diagnostics.log_event(
            "session", "session.resume", "succeeded", session_id="session"
        )
    diagnostics.close()
    recorded = entries(log_dir)
    assert recorded
    assert all(
        {"timestamp", "level", "event", "operation_id", "component", "outcome"}
        <= entry.keys()
        for entry in recorded
    )
    assert any(
        entry["event"] == "session.resume" and entry.get("duration_ms", -1) >= 0
        for entry in recorded
    )
    assert not any(entry["event"] == "storage.cursor_advance" for entry in recorded)
    files = list(log_dir.glob("voca.jsonl*"))
    assert len(files) <= 4
    assert all(path.stat().st_size <= 262144 + 2048 for path in files)


def test_timeout_is_logged_as_timeout(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    diagnostics = configure_logging(log_dir, "INFO", {})
    with (
        pytest.raises(TimeoutError),
        diagnostics.operation("control", "control.snapshot_send"),
    ):
        raise TimeoutError
    diagnostics.close()
    timeout_entries = [
        entry for entry in entries(log_dir) if entry["outcome"] == "timeout"
    ]
    assert len(timeout_entries) == 1
    assert timeout_entries[0]["event"] == "control.snapshot_send"
    assert timeout_entries[0]["reason"] == "operation_timeout"


def test_cancellation_is_logged_and_reraised(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    diagnostics = configure_logging(log_dir, "INFO", {})

    async def cancelled() -> None:
        with diagnostics.operation("control", "control.snapshot_send"):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    diagnostics.close()
    cancelled_entries = [
        entry for entry in entries(log_dir) if entry["outcome"] == "cancelled"
    ]
    assert len(cancelled_entries) == 1
    assert cancelled_entries[0]["event"] == "control.snapshot_send"


def test_failure_warning_is_safe_and_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unavailable_log_dir = tmp_path / "unavailable-log-dir"
    unavailable_log_dir.write_text("not a directory")
    settings = Settings(db_path=tmp_path / "voca.sqlite3", log_dir=unavailable_log_dir)
    marker = "VOCA_SYNTHETIC_SECRET"
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/sessions", json={})
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        with client.websocket_connect(f"/api/sessions/{session_id}/control") as socket:
            socket.send_json(
                {
                    "type": "session.resume",
                    "session_id": session_id,
                    "event_id": "f1d37af1-2ed6-4bb2-a7a2-5d0777d1db4c",
                    "last_event_id": None,
                }
            )
            assert socket.receive_json()["type"] == "session.snapshot"
        invalid = client.post(
            "/api/sessions",
            content=f'{{"marker":"{marker}"}}',
            headers={"content-type": "application/json"},
        )
        assert invalid.status_code == 400
    with sqlite3.connect(settings.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone() == (1,)
    captured = capsys.readouterr()
    assert captured.err == "diagnostics/write_failed\n"
    assert marker not in captured.err
