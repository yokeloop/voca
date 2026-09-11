from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voca.bootstrap.app import create_app
from voca.bootstrap.settings import Settings, load_settings


def settings_for(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "voca.sqlite3", log_dir=tmp_path / "logs")


def create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["last_event_id"] is None
    return response.json()["session_id"]


def resume(
    session_id: str, last_event_id: str | None = None, command_id: str | None = None
) -> dict[str, object]:
    return {
        "type": "session.resume",
        "session_id": session_id,
        "event_id": command_id or str(uuid4()),
        "last_event_id": last_event_id,
    }


def test_session_flow(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        session_id = create_session(client)
        command_id = str(uuid4())
        with client.websocket_connect(f"/api/sessions/{session_id}/control") as socket:
            socket.send_json(resume(session_id, command_id=command_id))
            first = socket.receive_json()
            assert first["type"] == "session.snapshot"
            assert first["event_id"] == "e1"
            assert first["connection"] == "disconnected"
            assert first["conversation"] == "off"
            assert first["messages"] == []
            assert first["tasks"] == []
            socket.send_json(resume(session_id, "e1", command_id))
            second = socket.receive_json()
            assert second["event_id"] == "e2"
        with client.websocket_connect(f"/api/sessions/{session_id}/control") as socket:
            socket.send_json(resume(session_id, "e2"))
            reconnected = socket.receive_json()
            assert reconnected["event_id"] == "e3"
    with (
        TestClient(create_app(settings)) as restarted,
        restarted.websocket_connect(f"/api/sessions/{session_id}/control") as socket,
    ):
        socket.send_json(resume(session_id, "e3"))
        restored = socket.receive_json()
        assert restored["session_id"] == session_id
        assert restored["event_id"] == "e4"
        assert restored["messages"] == []
    with sqlite3.connect(settings.db_path) as database:
        rows = database.execute(
            "SELECT session_id, event_cursor FROM sessions"
        ).fetchall()
    assert rows == [(session_id, 4)]
    entries = [
        json.loads(line)
        for line in (settings.log_dir / "voca.jsonl").read_text().splitlines()
    ]
    assert any(entry.get("session_id") == session_id for entry in entries)
    assert any(
        "duration_ms" in entry and entry["duration_ms"] >= 0 for entry in entries
    )
    resume_events = [
        entry
        for entry in entries
        if entry.get("session_id") == session_id
        and entry["event"]
        in {
            "control.receive",
            "session.resume",
            "storage.cursor_advance",
            "control.snapshot_send",
        }
    ]
    operation_ids = {entry["operation_id"] for entry in resume_events}
    assert any(
        sum(entry["operation_id"] == operation_id for entry in resume_events) == 8
        for operation_id in operation_ids
    )


@pytest.mark.parametrize(
    "selected_path", [".local/voca.sqlite3", "custom/voca.sqlite3"]
)
def test_absolute_paths_survive_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected_path: str
) -> None:
    repo = tmp_path / "repo"
    backend = repo / "backend"
    backend.mkdir(parents=True)
    monkeypatch.chdir(repo)
    settings = load_settings(
        {
            "VOCA_DB_PATH": str((repo / selected_path).resolve()),
            "VOCA_LOG_DIR": str((repo / ".local/logs").resolve()),
        }
    )
    with TestClient(create_app(settings)) as client:
        session_id = create_session(client)
        with client.websocket_connect(f"/api/sessions/{session_id}/control") as socket:
            socket.send_json(resume(session_id))
            assert socket.receive_json()["event_id"] == "e1"
    monkeypatch.chdir(backend)
    with (
        TestClient(create_app(settings)) as client,
        client.websocket_connect(f"/api/sessions/{session_id}/control") as socket,
    ):
        socket.send_json(resume(session_id, "e1"))
        assert socket.receive_json()["event_id"] == "e2"
    with sqlite3.connect(settings.db_path) as database:
        assert database.execute(
            "SELECT session_id, event_cursor FROM sessions"
        ).fetchall() == [(session_id, 2)]
    assert not (backend / ".local").exists()


@pytest.mark.parametrize(
    "case,code",
    [
        ("malformed", "invalid_command"),
        ("unknown", "unsupported_command"),
        ("start", "unsupported_command"),
        ("stop", "unsupported_command"),
        ("mismatch", "session_mismatch"),
        ("future", "invalid_cursor"),
        ("unicode_cursor", "invalid_command"),
    ],
)
def test_control_errors(tmp_path: Path, case: str, code: str) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        session_id = create_session(client)
        command_id = str(uuid4())
        if case == "malformed":
            rendered = "{"
        else:
            command: dict[str, object] = {
                "type": "unknown",
                "session_id": session_id,
                "event_id": command_id,
            }
            if case in {"start", "stop"}:
                command["type"] = f"conversation.{case}"
            if case == "mismatch":
                command = {
                    "type": "session.resume",
                    "session_id": str(uuid4()),
                    "event_id": command_id,
                    "last_event_id": None,
                }
            if case == "future":
                command = {
                    "type": "session.resume",
                    "session_id": session_id,
                    "event_id": command_id,
                    "last_event_id": "e999",
                }
            if case == "unicode_cursor":
                command = {
                    "type": "session.resume",
                    "session_id": session_id,
                    "event_id": command_id,
                    "last_event_id": "e١",
                }
            rendered = json.dumps(command)
        with client.websocket_connect(f"/api/sessions/{session_id}/control") as socket:
            socket.send_text(rendered)
            error = socket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == code
            assert error["session_id"] == session_id
            socket.send_json(resume(session_id))
            assert socket.receive_json()["type"] == "session.snapshot"
        with client.websocket_connect(f"/api/sessions/{session_id}/control") as socket:
            socket.send_bytes(b"invalid")
            assert socket.receive_json()["code"] == "invalid_command"
        with (
            pytest.raises(WebSocketDisconnect) as oversized,
            client.websocket_connect(f"/api/sessions/{session_id}/control") as socket,
        ):
            socket.send_bytes(b"x" * 8193)
            socket.receive_json()
        assert oversized.value.code == 1009
    with TestClient(create_app(settings)) as client:
        with (
            pytest.raises(WebSocketDisconnect) as unknown,
            client.websocket_connect(f"/api/sessions/{uuid4()}/control") as socket,
        ):
            socket.receive_json()
        assert unknown.value.code == 4404
        with (
            pytest.raises(WebSocketDisconnect) as malformed,
            client.websocket_connect("/api/sessions/not-a-uuid/control") as socket,
        ):
            socket.receive_json()
        assert malformed.value.code == 4400
    diagnostics = [
        json.loads(line)
        for line in (settings.log_dir / "voca.jsonl").read_text().splitlines()
    ]
    assert any(
        entry["event"] == "control.command_rejected" and entry["level"] == "WARNING"
        for entry in diagnostics
    )


def test_transport_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    from voca.presentation import responses

    async def blocked(_: object = None, **kwargs: object) -> None:
        del kwargs
        await asyncio.Event().wait()

    monkeypatch.setattr(responses, "IO_TIMEOUT_SECONDS", 0)
    with pytest.raises(TimeoutError):
        asyncio.run(responses.send_with_deadline(blocked, {}))
    with pytest.raises(TimeoutError):
        asyncio.run(responses.close_with_deadline(blocked, code=1011, reason="timeout"))


def test_bootstrap_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = settings_for(tmp_path)
    marker = "VOCA_SYNTHETIC_SECRET"
    with TestClient(create_app(settings)) as client:
        invalid = client.post(
            f"/api/sessions?token={marker}",
            content=f'{{"value":"{marker}"}}',
            headers={"content-type": "application/json", "authorization": marker},
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_request"
        unsupported = client.post(
            "/api/sessions", content=marker, headers={"content-type": "text/plain"}
        )
        assert unsupported.status_code == 415
        malformed_utf8 = client.post(
            "/api/sessions",
            content=b"\xff",
            headers={"content-type": "application/json"},
        )
        assert malformed_utf8.status_code == 400
    logs = "".join(path.read_text() for path in settings.log_dir.glob("voca.jsonl*"))
    assert marker not in logs
    assert marker not in capsys.readouterr().err
