from __future__ import annotations

import json
import logging
from pathlib import Path

from voca.diagnostics import SafeRotatingFileHandler, configure_logging


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


def test_failure_warning_is_safe_and_once(tmp_path: Path, capsys: object) -> None:
    diagnostics = configure_logging(tmp_path / "logs", "INFO", {})
    assert isinstance(diagnostics.handler, SafeRotatingFileHandler)
    record = logging.makeLogRecord({"msg": "VOCA_SYNTHETIC_SECRET"})
    diagnostics.handler.handleError(record)
    diagnostics.handler.handleError(record)
    diagnostics.close()
    captured = capsys.readouterr()
    assert captured.err == "diagnostics/write_failed\n"
    assert "VOCA_SYNTHETIC_SECRET" not in captured.err
