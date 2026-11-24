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
from voca.conversation.memory import apply_ops, parse_file, parse_ops, render_file
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


OPS_ADD = '{"operations": [{"op": "add", "section": "user", "text": "кот Ёжик"}]}'
NO_OPS = '{"operations": []}'
SHORT = "## О пользователе\n- Живёт в Казани\n\n## Заметки\n- Любит чай\n"
LONG_ADD = (
    '{"operations": [{"op": "add", "section": "notes", "text": "' + "д" * 40 + '"}]}'
)


class ScriptedSummarizer(FakeSummarizer):
    def __init__(self, results: list[Summary | Failure]) -> None:
        super().__init__(results[0])
        self.results = results

    async def summarize(self, prompt: str) -> Summary | Failure:
        self.prompts.append(prompt)
        return self.results.pop(0)


def test_runs_on_turns_and_moves_cursor(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert await run(keeper) is True
        memory = tmp_path / "memory.md"
        state = tmp_path / "memory.state.json"
        assert memory.read_text(encoding="utf-8") == BODY
        assert stat.S_IMODE(memory.stat().st_mode) == 0o600
        assert stat.S_IMODE(state.stat().st_mode) == 0o600
        assert json.loads(state.read_text(encoding="utf-8")) == {
            "last_message_id": all_ids(store)[3]
        }
        written = memory_rows(events)
        assert len(written) == 1
        assert written[0]["kind"] == "memory_updated"
        assert written[0]["added"] == 1
        assert written[0]["replaced"] == 0
        assert written[0]["removed"] == 0
        assert written[0]["chars_before"] == 0
        assert written[0]["chars_after"] == len(BODY) - 1
        assert written[0]["messages"] == 4
        assert written[0]["user_turns"] == 2
        assert written[0]["ms"] == 5
        assert written[0]["attempts"] == 1
        assert "(файл пуст)" in model.prompts[0]
        assert "ПОЛЬЗОВАТЕЛЬ: реплика 0" in model.prompts[0]

    asyncio.run(main())


def test_below_turns_no_run(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert await run(keeper) is True
        fill(store, 2, start=4)
        assert await run(keeper) is False
        assert len(model.prompts) == 1
        fill(store, 2, start=6)
        assert await run(keeper) is True
        for part in ("реплика 4", "реплика 6", "[1] (user) кот Ёжик"):
            assert part in model.prompts[1]
        assert "реплика 0" not in model.prompts[1]

    asyncio.run(main())


def test_every_turns_zero_off(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock(), every_turns=0)
        fill(store, 40)
        assert await run(keeper) is False
        assert model.prompts == []
        assert not (tmp_path / "memory.md").exists()
        assert not (tmp_path / "memory.state.json").exists()

    asyncio.run(main())


def test_batch_limit_and_remainder(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(NO_OPS, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 250)
        ids = all_ids(store)
        state = tmp_path / "memory.state.json"
        assert await run(keeper) is True
        assert memory_rows(events)[0]["messages"] == 200
        assert memory_rows(events)[0]["user_turns"] == 100
        assert json.loads(state.read_text())["last_message_id"] == ids[199]
        assert "реплика 199" in model.prompts[0]
        assert "реплика 200" not in model.prompts[0]
        assert await run(keeper) is True
        assert memory_rows(events)[1]["messages"] == 50
        assert json.loads(state.read_text())["last_message_id"] == ids[-1]

    asyncio.run(main())


def test_batch_only_user_and_assistant(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(NO_OPS, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        rec = SessionRecorder(store, store.open_session().id)
        for row in (
            ArchiveRow("user", "u1", 1.0, None, None, "вопрос", {}),
            ArchiveRow("tool_call", "t1", 2.0, "c1", "delegate", "СЕКРЕТНЫЙ_ВЫЗОВ", {}),
            ArchiveRow("tool_result", "r1", 3.0, "c1", None, "СЕКРЕТНЫЙ_ВЫВОД", {}),
            ArchiveRow("summary", "s1", 4.0, None, None, "ПЕРЕСКАЗ_X", {}),
            ArchiveRow("assistant", "a1", 5.0, None, None, "ответ", {}),
            ArchiveRow("user", "u2", 6.0, None, None, "ещё", {}),
        ):
            rec.add_row(row)
        rec.flush({"items": []})
        assert await run(keeper) is True
        for mark in ("СЕКРЕТНЫЙ_ВЫЗОВ", "СЕКРЕТНЫЙ_ВЫВОД", "ПЕРЕСКАЗ_X"):
            assert mark not in model.prompts[0]
        assert memory_rows(events)[0]["messages"] == 3
        assert memory_rows(events)[0]["user_turns"] == 2

    asyncio.run(main())


def test_zero_ops_keeps_file(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(NO_OPS, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        memory = tmp_path / "memory.md"
        memory.write_text("старое\n* как есть", encoding="utf-8")
        before = memory.read_bytes()
        assert await run(keeper) is True
        assert memory.read_bytes() == before
        state = json.loads((tmp_path / "memory.state.json").read_text())
        assert state == {"last_message_id": all_ids(store)[3]}
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_updated"]
        assert written[0]["added"] == 0
        assert written[0]["replaced"] == 0
        assert written[0]["removed"] == 0
        assert written[0]["chars_before"] == len("старое\n* как есть")
        assert written[0]["chars_after"] == written[0]["chars_before"]

    asyncio.run(main())


def test_ops_applied_untouched_bytes(tmp_path: Path) -> None:
    async def main() -> None:
        reply = (
            '{"operations": [{"op": "remove", "id": 2}, '
            '{"op": "replace", "id": 3, "text": "Любит кофе"}, '
            '{"op": "add", "section": "notes", "text": "Ходит в бассейн"}]}'
        )
        model = FakeSummarizer(Summary(reply, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        memory = tmp_path / "memory.md"
        memory.write_text(
            "## О пользователе\n- Живёт в Казани\n- Собаку зовут Бублик\n\n"
            "## Заметки\n- Любит чай\n",
            encoding="utf-8",
        )
        line = memory.read_bytes().split(b"\n")[1]
        assert await run(keeper) is True
        assert memory.read_text(encoding="utf-8") == (
            "## О пользователе\n- Живёт в Казани\n\n"
            "## Заметки\n- Любит кофе\n- Ходит в бассейн\n"
        )
        assert memory.read_bytes().split(b"\n")[1] == line
        written = memory_rows(events)
        assert written[0]["kind"] == "memory_updated"
        assert (
            written[0]["added"],
            written[0]["replaced"],
            written[0]["removed"],
        ) == (1, 1, 1)

    asyncio.run(main())


def test_hand_written_file(tmp_path: Path) -> None:
    async def main() -> None:
        reply = (
            '{"operations": [{"op": "add", "section": "user", "text": "Пёс Бублик"}]}'
        )
        model = FakeSummarizer(Summary(reply, 1, 5))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        memory = tmp_path / "memory.md"
        memory.write_text(
            "Без заголовка\n## О пользователе\n* Кот Ёжик\n", encoding="utf-8"
        )
        assert await run(keeper) is True
        assert memory.read_text(encoding="utf-8") == (
            "## О пользователе\n- Кот Ёжик\n- Пёс Бублик\n\n## Заметки\n- Без заголовка\n"
        )
        assert "[1] (user) Кот Ёжик" in model.prompts[0]
        assert "[2] (notes) Без заголовка" in model.prompts[0]

    asyncio.run(main())


@pytest.mark.parametrize(
    "reply",
    [
        "не json",
        '{"operations": [{"op": "remove", "id": 7}]}',
        '{"operations": [{"op": "add", "section": "x", "text": "y"}]}',
    ],
)
def test_bad_reply_keeps_file_and_cursor(tmp_path: Path, reply: str) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(reply, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        memory = tmp_path / "memory.md"
        state = tmp_path / "memory.state.json"
        memory.write_text("старое", encoding="utf-8")
        state.write_text('{"last_message_id": 0}', encoding="utf-8")
        memory_before = memory.read_bytes()
        state_before = state.read_bytes()
        assert await run(keeper) is True
        assert memory.read_bytes() == memory_before
        assert state.read_bytes() == state_before
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed"]
        assert written[0]["reason"] == "bad_reply"
        assert written[0]["attempts"] == 1
        assert written[0]["messages"] == 4

    asyncio.run(main())


def test_over_limit_retry_succeeds(tmp_path: Path) -> None:
    async def main() -> None:
        retry = (
            '{"operations": [{"op": "remove", "id": 2}, '
            '{"op": "add", "section": "user", "text": "Кот Ёжик"}]}'
        )
        model = ScriptedSummarizer([Summary(LONG_ADD, 1, 3), Summary(retry, 1, 4)])
        keeper, store, events = make_keeper(
            tmp_path, model, FakeClock(), every_turns=2, max_chars=60
        )
        fill(store, 4)
        memory = tmp_path / "memory.md"
        memory.write_text(SHORT, encoding="utf-8")
        entries = parse_file(SHORT)
        ops = parse_ops(LONG_ADD, entries)
        assert ops is not None
        over = len(render_file(apply_ops(entries, ops))) - 60
        assert await run(keeper) is True
        assert len(model.prompts) == 2
        assert "ТВОЙ ПРОШЛЫЙ ОТВЕТ:" in model.prompts[1]
        assert f"на {over} символов" in model.prompts[1]
        text = memory.read_text(encoding="utf-8")
        assert text == "## О пользователе\n- Живёт в Казани\n- Кот Ёжик\n\n## Заметки\n"
        assert "д" * 40 not in text
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_updated"]
        assert written[0]["attempts"] == 2
        assert written[0]["ms"] == 7
        assert written[0]["added"] == 1
        assert written[0]["removed"] == 1

    asyncio.run(main())


def test_over_limit_retry_fails(tmp_path: Path) -> None:
    async def main() -> None:
        model = ScriptedSummarizer([Summary(LONG_ADD, 1, 3), Summary(LONG_ADD, 1, 3)])
        keeper, store, events = make_keeper(
            tmp_path, model, FakeClock(), every_turns=2, max_chars=60
        )
        fill(store, 4)
        memory = tmp_path / "memory.md"
        memory.write_text(SHORT, encoding="utf-8")
        assert await run(keeper) is True
        assert memory.read_text(encoding="utf-8") == SHORT
        assert not (tmp_path / "memory.state.json").exists()
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed"]
        assert written[0]["reason"] == "too_long"
        assert written[0]["attempts"] == 2
        assert written[0]["ms"] == 6

    asyncio.run(main())


def test_hand_file_over_limit(tmp_path: Path) -> None:
    async def main() -> None:
        retry = '{"operations": [{"op": "remove", "id": 2}, {"op": "remove", "id": 3}]}'
        model = ScriptedSummarizer([Summary(NO_OPS, 1, 1), Summary(retry, 1, 1)])
        keeper, store, events = make_keeper(
            tmp_path, model, FakeClock(), every_turns=2, max_chars=40
        )
        fill(store, 4)
        memory = tmp_path / "memory.md"
        original = (
            "## О пользователе\n- Кот\n- Живёт в Казани\n\n## Заметки\n- Любит чай\n"
        )
        assert len(original) - 1 == 64
        memory.write_text(original, encoding="utf-8")
        assert await run(keeper) is True
        assert (
            memory.read_text(encoding="utf-8")
            == "## О пользователе\n- Кот\n\n## Заметки\n"
        )
        assert memory.read_bytes().split(b"\n")[1] == original.encode().split(b"\n")[1]
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_updated"]
        assert written[0]["attempts"] == 2
        assert written[0]["removed"] == 2

    asyncio.run(main())


@pytest.mark.parametrize(
    ("model", "reason", "error"),
    [
        (FakeSummarizer(Failure("timeout", 60000)), "timeout", None),
        (RaisingSummarizer(Summary("x", 1, 1)), "error", "RuntimeError"),
    ],
)
def test_failures_keep_file_and_cursor(
    tmp_path: Path, model: FakeSummarizer, reason: str, error: str | None
) -> None:
    async def main() -> None:
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        memory = tmp_path / "memory.md"
        state = tmp_path / "memory.state.json"
        memory.write_text("старое", encoding="utf-8")
        state.write_text('{"last_message_id": 0}', encoding="utf-8")
        memory_before = memory.read_bytes()
        state_before = state.read_bytes()
        assert await run(keeper) is True
        assert memory.read_bytes() == memory_before
        assert state.read_bytes() == state_before
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed"]
        assert written[0]["reason"] == reason
        assert written[0]["messages"] == 4
        assert written[0]["attempts"] == 1
        assert written[0].get("error") == error

    asyncio.run(main())


def test_backoff_after_failure(tmp_path: Path) -> None:
    async def main() -> None:
        model = ScriptedSummarizer([Failure("connection", 1), Summary(OPS_ADD, 1, 1)])
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert await run(keeper) is True
        assert await run(keeper) is False
        fill(store, 2, start=4)
        assert await run(keeper) is False
        assert len(model.prompts) == 1
        fill(store, 2, start=6)
        assert await run(keeper) is True
        assert "реплика 0" in model.prompts[1]
        written = memory_rows(events)
        assert [row["kind"] for row in written] == ["memory_failed", "memory_updated"]
        assert written[1]["messages"] == 8

    asyncio.run(main())


def test_backoff_reset_on_restart(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Failure("connection", 1))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert await run(keeper) is True
        assert keeper.check() is False
        again = MemoryKeeper(
            store=store,
            log=keeper.log,
            config=keeper.config,
            model=model,
            clock=FakeClock(),
        )
        assert await run(again) is True
        assert len(model.prompts) == 2

    asyncio.run(main())


def test_no_key(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5), ready=False)
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert keeper.check() is False
        assert model.prompts == []
        written = memory_rows(events)
        assert len(written) == 1
        assert written[0]["kind"] == "memory_failed"
        assert written[0]["reason"] == "no_key"
        assert written[0]["ms"] == 0
        assert written[0]["messages"] == 4
        assert "attempts" not in written[0]
        assert keeper.check() is False
        assert len(memory_rows(events)) == 1

    asyncio.run(main())


def test_write_failure_keeps_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise OSError("disk")

    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
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
        assert written[0]["attempts"] == 1

    asyncio.run(main())


@pytest.mark.parametrize("broken", ["directory", "bytes"])
def test_unreadable_file(tmp_path: Path, broken: str) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
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
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5), hold=True)
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert keeper.check() is True
        while not model.prompts:
            await asyncio.sleep(0)
        fill(store, 4, start=4)
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


def test_old_state_file(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 2)
        state = tmp_path / "memory.state.json"
        state.write_text(
            json.dumps({"last_message_id": all_ids(store)[1], "run_at": 5.0}),
            encoding="utf-8",
        )
        fill(store, 4, start=2)
        assert await run(keeper) is True
        assert "реплика 0" not in model.prompts[0]
        assert "реплика 1" not in model.prompts[0]
        assert memory_rows(events)[0]["messages"] == 4
        assert json.loads(state.read_text(encoding="utf-8")) == {
            "last_message_id": all_ids(store)[-1]
        }

    asyncio.run(main())


def test_cursor_survives_restart(tmp_path: Path) -> None:
    async def main() -> None:
        model = FakeSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, _ = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
        fill(store, 4)
        assert await run(keeper) is True
        again = MemoryKeeper(
            store=store,
            log=keeper.log,
            config=keeper.config,
            model=model,
            clock=FakeClock(),
        )
        fill(store, 2, start=4)
        assert again.check() is False
        assert len(model.prompts) == 1
        fill(store, 2, start=6)
        assert await run(again) is True
        assert "реплика 4" in model.prompts[1]
        assert "реплика 6" in model.prompts[1]
        assert "реплика 0" not in model.prompts[1]
        assert "реплика 3" not in model.prompts[1]

    asyncio.run(main())


def test_real_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str | None, dict[str, Any]]] = []

    async def chat(request: web.Request) -> web.Response:
        requests.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response(completion(OPS_ADD))

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
                    update={"path": tmp_path / "memory.md", "every_turns": 2}
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
        assert "ТЕКУЩИЕ ЗАПИСИ:" in body["messages"][0]["content"]
        assert (tmp_path / "memory.md").read_text(encoding="utf-8") == BODY
        assert [row["kind"] for row in memory_rows(events)] == ["memory_updated"]

    asyncio.run(main())


class CancellingSummarizer(FakeSummarizer):
    async def summarize(self, prompt: str) -> Summary | Failure:
        self.prompts.append(prompt)
        raise asyncio.CancelledError


def test_model_cancelled_error_propagates(tmp_path: Path) -> None:
    async def main() -> None:
        model = CancellingSummarizer(Summary(OPS_ADD, 1, 5))
        keeper, store, events = make_keeper(tmp_path, model, FakeClock(), every_turns=2)
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
