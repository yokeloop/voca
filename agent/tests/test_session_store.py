import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from livekit.agents import llm

import voca.__main__
from voca.session.model import ArchiveRow
from voca.session.store import SearchError, SessionRecorder, SessionStore, StoreError

DUMP: dict[str, Any] = {
    "exclude_function_call": False,
    "exclude_timestamp": False,
    "exclude_config_update": True,
}


def dump(item: llm.ChatItem) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude_none=True)


def kinds(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as db:
        return [row[0] for row in db.execute("SELECT kind FROM messages ORDER BY id")]


def ids(path: Path) -> list[int]:
    with closing(sqlite3.connect(path)) as db:
        return [row[0] for row in db.execute("SELECT id FROM messages ORDER BY id")]


def recorder(path: Path) -> tuple[SessionStore, SessionRecorder]:
    store = SessionStore.open(path)
    return store, SessionRecorder(store, store.open_session().id)


def remember(path: Path, *texts: str) -> None:
    store, rec = recorder(path)
    ctx = llm.ChatContext()
    for text in texts:
        rec.add(dump(ctx.add_message(role="user", content=[text])))
    rec.flush(ctx.to_dict(**DUMP))
    store.close()


def pair(ctx: llm.ChatContext, n: int) -> list[llm.ChatItem]:
    return [
        ctx.add_message(role="user", content=[f"вопрос {n}"]),
        ctx.add_message(role="assistant", content=[f"ответ {n}"]),
    ]


def test_round_trip_keeps_ids_and_order(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content=["rules"])
    user = ctx.add_message(role="user", content=["Привет"])
    call = llm.FunctionCall(call_id="c1", name="delegate", arguments='{"task": "x"}')
    result = llm.FunctionCallOutput(
        call_id="c1", name="delegate", output="failed", is_error=True
    )
    ctx.insert([call, result])
    assistant = ctx.add_message(
        role="assistant", content=["Здравствуй"], interrupted=True
    )
    ctx.insert(llm.AgentConfigUpdate(instructions="rules"))
    store, rec = recorder(path)
    for item in ctx.items:
        rec.add(dump(item))
    rec.flush(ctx.to_dict(**DUMP))
    store.close()

    store = SessionStore.open(path)
    restored = llm.ChatContext.from_dict(store.open_session().context)
    store.close()
    original = [user, call, result, assistant]
    assert [item.type for item in restored.items] == [item.type for item in original]
    assert [item.id for item in restored.items] == [item.id for item in original]
    assert [item.created_at for item in restored.items] == [
        item.created_at for item in original
    ]
    assert [getattr(item, "call_id", None) for item in restored.items] == [
        None,
        "c1",
        "c1",
        None,
    ]
    restored_assistant = restored.items[3]
    assert isinstance(restored_assistant, llm.ChatMessage)
    assert restored_assistant.interrupted is True
    assert kinds(path) == ["user", "tool_call", "tool_result", "assistant"]


def test_reopen_restores_same_session(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    store, rec = recorder(path)
    ctx = llm.ChatContext()
    for item in pair(ctx, 1):
        rec.add(dump(item))
    rec.flush(ctx.to_dict(**DUMP))
    first = store.open_session()
    store.close()
    store = SessionStore.open(path)
    second = store.open_session()
    store.close()
    assert second.id == first.id
    assert second.context == first.context
    assert len(second.context["items"]) == 2


def test_trim_keeps_archive(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    store, rec = recorder(path)
    ctx = llm.ChatContext()
    first = pair(ctx, 1)
    second = pair(ctx, 2)
    for item in [*first, *second]:
        rec.add(dump(item))
    rec.flush(ctx.to_dict(**DUMP))
    trimmed = llm.ChatContext(second)
    rec.flush(trimmed.to_dict(**DUMP))
    store.close()
    assert len(kinds(path)) == 4
    store = SessionStore.open(path)
    assert len(store.open_session().context["items"]) == 2
    store.close()


def test_flush_twice_does_not_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    store, rec = recorder(path)
    ctx = llm.ChatContext()
    item = ctx.add_message(role="user", content=["Привет"])
    rec.add(dump(item))
    rec.flush(ctx.to_dict(**DUMP))
    rec.add(dump(item))
    rec.flush(ctx.to_dict(**DUMP))
    store.close()
    assert kinds(path) == ["user"]


def test_fts_case_yo_prefix(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    remember(path, "Сессия началась", "Ёлка стоит", "ещё раз")
    store = SessionStore.open_readonly(path)
    first, second, third = ids(path)
    assert [hit.id for hit in store.search("сессия", 10)] == [first]
    for query in ("елка", "ЕЛКА"):
        hits = store.search(query, 10)
        assert [hit.id for hit in hits] == [second]
        assert "[Ёлка]" in hits[0].snippet
    assert [hit.id for hit in store.search("еще", 10)] == [third]
    assert store.search("сессии", 10) == []
    assert [hit.id for hit in store.search("сесси*", 10)] == [first]
    store.close()


def test_search_error(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    remember(path, "Привет")
    store = SessionStore.open_readonly(path)
    with pytest.raises(SearchError):
        store.search('"', 10)
    store.close()


def test_file_and_dir_modes(tmp_path: Path) -> None:
    path = tmp_path / "new" / "sessions.sqlite3"
    SessionStore.open(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(StoreError):
        SessionStore.open(path)
    with pytest.raises(StoreError):
        SessionStore.open_readonly(path)


def test_readonly_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    with pytest.raises(StoreError):
        SessionStore.open_readonly(path)
    assert not path.exists()


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["voca", "archive", "search", *args])
    voca.__main__.main()


def test_archive_search_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "sessions.sqlite3"
    remember(path, "Мой кот Ёжик")
    monkeypatch.setenv("VOCA_SESSION__PATH", str(path))
    run_cli(monkeypatch, "ежик")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "\tuser\t" in lines[0]
    assert "[Ёжик]" in lines[0]


def test_archive_search_cli_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("VOCA_SESSION__PATH", str(missing))
    with pytest.raises(SystemExit) as stopped:
        run_cli(monkeypatch, "ежик")
    assert stopped.value.code == 2
    assert "нет файла сессий" in capsys.readouterr().err
    assert not missing.exists()

    path = tmp_path / "sessions.sqlite3"
    remember(path, "Привет")
    monkeypatch.setenv("VOCA_SESSION__PATH", str(path))
    with pytest.raises(SystemExit) as stopped:
        run_cli(monkeypatch, '"')
    assert stopped.value.code == 2
    assert "неверный запрос" in capsys.readouterr().err

    config = tmp_path / "config.toml"
    config.write_text("[session]\nunknown = 1\n", encoding="utf-8")
    monkeypatch.setenv("VOCA_CONFIG", str(config))
    with pytest.raises(SystemExit) as stopped:
        run_cli(monkeypatch, "ежик")
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "voca archive: конфиг не прочитан (" in err
    assert str(config) in err


class RolledBack(sqlite3.Connection):
    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        self.execute("ROLLBACK")
        raise sqlite3.OperationalError("database or disk is full")


def test_record_keeps_error_after_sqlite_rollback(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    SessionStore.open(path).close()
    store = SessionStore(sqlite3.connect(path, autocommit=True, factory=RolledBack))
    rec = SessionRecorder(store, store.open_session().id)
    ctx = llm.ChatContext()
    rec.add(dump(ctx.add_message(role="user", content=["Привет"])))
    with pytest.raises(sqlite3.OperationalError, match="full"):
        rec.flush(ctx.to_dict(**DUMP))
    assert len(rec.pending) == 1
    store.close()


def test_summary_row_and_counter(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    store, rec = recorder(path)

    def summary(item_id: str) -> ArchiveRow:
        return ArchiveRow(
            kind="summary",
            item_id=item_id,
            at=5.0,
            call_id=None,
            name=None,
            text="Кота зовут Ёжик",
            data={"trigger": "threshold", "folded_turns": 4},
        )

    def counters() -> tuple[float | None, int]:
        with closing(sqlite3.connect(path)) as db:
            row = db.execute(
                "SELECT compacted_at, summary_count FROM sessions"
            ).fetchone()
        return row[0], row[1]

    rec.add_row(summary("sum1"))
    rec.flush({"items": []}, compacted_at=123.0)
    with closing(sqlite3.connect(path)) as db:
        written = db.execute(
            "SELECT kind, data_json FROM messages WHERE item_id = ?", ("sum1",)
        ).fetchall()
    assert len(written) == 1
    assert written[0][0] == "summary"
    assert json.loads(written[0][1])["folded_turns"] == 4
    assert counters() == (123.0, 1)
    rec.add_row(summary("sum2"))
    rec.flush({"items": []}, compacted_at=124.0)
    assert counters() == (124.0, 2)
    hits = store.search("ежик", 20)
    assert hits and all(hit.kind == "summary" for hit in hits)
    rec.flush({"items": []})
    assert counters() == (124.0, 2)
    store.close()
