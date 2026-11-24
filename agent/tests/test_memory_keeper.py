import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tests.test_agent_turn import FakeClock, FakeSummarizer, RaisingSummarizer, rows
from tests.test_summarizer import completion
from voca.app.wiring import make_memory_model
from voca.config import MemoryConfig, Settings
from voca.conversation.memory_keeper import MemoryKeeper
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.session.model import ArchiveRow
from voca.session.store import SessionRecorder, SessionStore
from voca.telemetry.jsonl import JsonlLog

BODY = "## О пользователе\n- кот Ёжик\n\n## Заметки\n"


def fill(store: SessionStore, n: int, start: int = 0) -> None:
    rec = SessionRecorder(store, store.open_session().id)
    for i in range(start, start + n):
        kind = "user" if i % 2 == 0 else "assistant"
        rec.add_row(ArchiveRow(kind, f"m{i}", float(i), None, None, f"реплика {i}", {}))
    rec.flush({"items": []})


def make_keeper(
    tmp_path: Path, model: Summarizer, clock: FakeClock, **config: Any
) -> tuple[MemoryKeeper, SessionStore, Path]:
    store = SessionStore.open(tmp_path / "sessions.sqlite3")
    events = tmp_path / "events.jsonl"
    keeper = MemoryKeeper(
        store=store,
        log=JsonlLog(events, session="t"),
        config=MemoryConfig(path=tmp_path / "memory.md", **config),
        model=model,
        clock=clock,
    )
    return keeper, store, events


def memory_rows(events: Path) -> list[dict[str, object]]:
    if not events.exists():
        return []
    return [row for row in rows(events) if str(row["kind"]).startswith("memory_")]


def all_ids(store: SessionStore) -> list[int]:
    return [message_id for message_id, _ in store.after(0, 10_000)]


async def run(keeper: MemoryKeeper) -> bool:
    started = keeper.check()
    if started:
        assert keeper.task is not None
        await keeper.task
    return started


def test_runs_when_due_and_moves_cursor(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        assert await run(keeper) is True
        memory = tmp_path / "memory.md"
        state = tmp_path / "memory.state.json"
        assert memory.read_text(encoding="utf-8") == BODY
        assert stat.S_IMODE(memory.stat().st_mode) == 0o600
        assert stat.S_IMODE(state.stat().st_mode) == 0o600
        assert json.loads(state.read_text(encoding="utf-8")) == {
            "last_message_id": all_ids(store)[3],
            "run_at": 0.0,
        }
        written = memory_rows(events)
        assert len(written) == 1
        assert written[0]["kind"] == "memory_updated"
        assert written[0]["chars_before"] == 0
        assert written[0]["chars_after"] == len(BODY)
        assert written[0]["messages"] == 4
        assert written[0]["ms"] == 5
        assert "(файл пуст)" in model.prompts[0]
        assert "ПОЛЬЗОВАТЕЛЬ: реплика 0" in model.prompts[0]

    asyncio.run(main())


def test_due_by_clock(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        clock = FakeClock()
        keeper, store, _ = make_keeper(tmp_path, model, clock)
        fill(store, 4)
        assert await run(keeper) is True
        fill(store, 2, start=4)
        clock.now = 3599
        assert await run(keeper) is False
        assert len(model.prompts) == 1
        clock.now = 3600
        assert await run(keeper) is True
        assert "реплика 4" in model.prompts[1]
        assert "реплика 0" not in model.prompts[1]

    asyncio.run(main())


def test_no_new_rows_no_run(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        clock = FakeClock()
        keeper, store, events = make_keeper(tmp_path, model, clock)
        fill(store, 4)
        assert await run(keeper) is True
        before = len(memory_rows(events))
        clock.now = 7200
        assert await run(keeper) is False
        assert len(model.prompts) == 1
        assert len(memory_rows(events)) == before

    asyncio.run(main())


def test_interval_zero_off(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock(), interval_hours=0)
        fill(store, 4)
        assert await run(keeper) is False
        assert model.prompts == []
        assert not (tmp_path / "memory.md").exists()
        assert not (tmp_path / "memory.state.json").exists()

    asyncio.run(main())


def test_batch_limit_and_remainder(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        clock = FakeClock()
        keeper, store, events = make_keeper(tmp_path, model, clock)
        fill(store, 250)
        ids = all_ids(store)
        state = tmp_path / "memory.state.json"
        assert await run(keeper) is True
        assert memory_rows(events)[0]["messages"] == 200
        assert json.loads(state.read_text())["last_message_id"] == ids[199]
        assert "реплика 199" in model.prompts[0]
        assert "реплика 200" not in model.prompts[0]
        clock.now = 3599
        assert await run(keeper) is False
        clock.now = 3600
        assert await run(keeper) is True
        assert memory_rows(events)[1]["messages"] == 50
        assert json.loads(state.read_text())["last_message_id"] == ids[-1]

    asyncio.run(main())


def test_batch_only_user_and_assistant(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        rec = SessionRecorder(store, store.open_session().id)
        for row in (
            ArchiveRow("user", "u1", 1.0, None, None, "вопрос", {}),
            ArchiveRow("tool_call", "t1", 2.0, "c1", "delegate", "СЕКРЕТНЫЙ_ВЫЗОВ", {}),
            ArchiveRow("tool_result", "r1", 3.0, "c1", None, "СЕКРЕТНЫЙ_ВЫВОД", {}),
            ArchiveRow("summary", "s1", 4.0, None, None, "ПЕРЕСКАЗ_X", {}),
            ArchiveRow("assistant", "a1", 5.0, None, None, "ответ", {}),
        ):
            rec.add_row(row)
        rec.flush({"items": []})
        assert await run(keeper) is True
        for mark in ("СЕКРЕТНЫЙ_ВЫЗОВ", "СЕКРЕТНЫЙ_ВЫВОД", "ПЕРЕСКАЗ_X"):
            assert mark not in model.prompts[0]
        assert memory_rows(events)[0]["messages"] == 2

    asyncio.run(main())


@pytest.mark.parametrize(
    ("model", "reason", "error"),
    [
        (FakeSummarizer(Summary("а" * 2201, 1, 1)), "too_long", None),
        (FakeSummarizer(Failure("timeout", 60000)), "timeout", None),
        (RaisingSummarizer(Summary("x", 1, 1)), "error", "RuntimeError"),
    ],
)
def test_failures_keep_file_and_cursor(
    tmp_path: Path, model: FakeSummarizer, reason: str, error: str | None
) -> None:
    async def main() -> None:
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        memory = tmp_path / "memory.md"
        state = tmp_path / "memory.state.json"
        memory.write_text("старое", encoding="utf-8")
        state.write_text('{"last_message_id": 0, "run_at": null}', encoding="utf-8")
        memory_before = memory.read_bytes()
        state_before = state.read_bytes()
        assert await run(keeper) is True
        assert memory.read_bytes() == memory_before
        assert state.read_bytes() == state_before
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed"]
        assert written[0]["reason"] == reason
        assert written[0]["messages"] == 4
        assert written[0].get("error") == error

    asyncio.run(main())


def test_retry_not_before_interval(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Failure("connection", 1))
        clock = FakeClock()
        keeper, store, _ = make_keeper(tmp_path, model, clock)
        fill(store, 4)
        assert await run(keeper) is True
        clock.now = 3599
        assert await run(keeper) is False
        assert len(model.prompts) == 1
        clock.now = 3600
        assert await run(keeper) is True
        assert len(model.prompts) == 2

    asyncio.run(main())


def test_no_key(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5), ready=False)
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        assert keeper.check() is False
        assert model.prompts == []
        written = memory_rows(events)
        assert len(written) == 1
        assert written[0]["kind"] == "memory_failed"
        assert written[0]["reason"] == "no_key"
        assert written[0]["ms"] == 0
        assert written[0]["messages"] == 4
        assert keeper.check() is False
        assert len(memory_rows(events)) == 1

    asyncio.run(main())


def test_write_failure_keeps_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise OSError("disk")

    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        memory = tmp_path / "memory.md"
        memory.write_text("старое", encoding="utf-8")
        monkeypatch.setattr(os, "replace", fail)
        assert await run(keeper) is True
        assert memory.read_text(encoding="utf-8") == "старое"
        assert not (tmp_path / ".memory.md.tmp").exists()
        assert not (tmp_path / "memory.state.json").exists()
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed"]
        assert written[0]["reason"] == "write"
        assert written[0]["error"] == "OSError"

    asyncio.run(main())


@pytest.mark.parametrize("broken", ["directory", "bytes"])
def test_unreadable_file(tmp_path: Path, broken: str) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        memory = tmp_path / "memory.md"
        if broken == "directory":
            memory.mkdir()
            error = "IsADirectoryError"
        else:
            memory.write_bytes(b"\xff\xfe")
            error = "UnicodeDecodeError"
        assert keeper.check() is False
        assert model.prompts == []
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed"]
        assert written[0]["reason"] == "read"
        assert written[0]["error"] == error
        if broken == "bytes":
            assert memory.read_bytes() == b"\xff\xfe"

    asyncio.run(main())


def test_one_run_at_a_time_and_cancel(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5), hold=True)
        clock = FakeClock()
        keeper, store, events = make_keeper(tmp_path, model, clock)
        fill(store, 4)
        assert keeper.check() is True
        while not model.prompts:
            await asyncio.sleep(0)
        clock.now = 7200
        assert keeper.check() is False
        assert len(model.prompts) == 1
        task = keeper.task
        assert task is not None
        keeper.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert memory_rows(events) == []
        assert not (tmp_path / "memory.md").exists()
        assert not (tmp_path / "memory.state.json").exists()
        assert keeper.task is None

    asyncio.run(main())


def test_cursor_survives_restart(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(BODY.strip(), 1, 5))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        assert await run(keeper) is True
        fill(store, 2, start=4)
        clock = FakeClock()
        clock.now = 100
        again = MemoryKeeper(
            store=store,
            log=keeper.log,
            config=keeper.config,
            model=model,
            clock=clock,
        )
        assert again.check() is False
        assert len(model.prompts) == 1

    asyncio.run(main())


def test_real_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str | None, dict[str, Any]]] = []

    async def chat(request: web.Request) -> web.Response:
        requests.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response(completion(BODY))

    async def main() -> None:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat)
        async with TestServer(app, host="127.0.0.1") as server:
            monkeypatch.setenv("VOCA_LITELLM_KEY", "k")
            monkeypatch.setenv("VOCA_MEMORY__MODEL__URL", str(server.make_url("/v1")))
            monkeypatch.setenv("VOCA_MEMORY__MODEL__MODEL", "m-x")
            model = make_memory_model(Settings())
            store = SessionStore.open(tmp_path / "sessions.sqlite3")
            events = tmp_path / "events.jsonl"
            keeper = MemoryKeeper(
                store=store,
                log=JsonlLog(events, session="t"),
                config=Settings().memory.model_copy(
                    update={"path": tmp_path / "memory.md"}
                ),
                model=model,
            )
            fill(store, 4)
            assert await run(keeper) is True
            await model.close()
        assert len(requests) == 1
        authorization, body = requests[0]
        assert authorization == "Bearer k"
        assert body["model"] == "m-x"
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"
        assert "НОВЫЕ РЕПЛИКИ:" in body["messages"][0]["content"]
        assert (tmp_path / "memory.md").read_text(encoding="utf-8") == BODY.strip()
        assert [row["kind"] for row in memory_rows(events)] == ["memory_updated"]

    asyncio.run(main())


class CancellingSummarizer(FakeSummarizer):
    async def summarize(self, prompt: str) -> Summary | Failure:
        self.prompts.append(prompt)
        raise asyncio.CancelledError


def test_model_cancelled_error_propagates(tmp_path: Path) -> None:
    async def main() -> None:
        model = CancellingSummarizer(Summary(BODY.strip(), 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock())
        fill(store, 4)
        assert keeper.check() is True
        task = keeper.task
        assert task is not None
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(model.prompts) == 1
        assert memory_rows(events) == []
        assert not (tmp_path / "memory.md").exists()

    asyncio.run(main())
