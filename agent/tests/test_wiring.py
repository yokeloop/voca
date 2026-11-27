import asyncio
import json
import math
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from livekit.agents import tts
from livekit.plugins import silero

from voca.app.wiring import (
    MAX_UNRECOVERABLE_ERRORS,
    TTS_TEXT_TRANSFORMS,
    build_session,
    check_context,
    compaction_limits,
    make_decider,
    make_llm,
    make_memory_model,
    make_summarizer,
    make_tts,
)
from voca.app.worker import run, server_options
from voca.config import Settings
from voca.conversation.compaction import Limits
from voca.conversation.verdict import DecisionFailure
from voca.speech.prepare import Prepared
from voca.telemetry.jsonl import JsonlLog


def test_build_session_options() -> None:
    async def main() -> None:
        settings = Settings(
            llm_url="http://127.0.0.1:9/v1", audio_url="http://127.0.0.1:9/v1"
        )
        vad = silero.VAD.load(min_silence_duration=0.3)
        with ThreadPoolExecutor(max_workers=1) as executor:
            session = build_session(
                settings,
                vad=vad,
                executor=executor,
                on_prepared=lambda text, prepared, ms: None,
            )
            options = session.options
            assert session.turn_detection == "vad"
            assert options.interruption["mode"] == "vad"
            assert options.interruption["min_duration"] == 0.5
            assert options.interruption["min_words"] == 0
            assert options.preemptive_generation["enabled"] is True
            assert options.endpointing["min_delay"] == 0.2
            assert options.aec_warmup_duration == 3.0
            assert options.tts_text_transforms == TTS_TEXT_TRANSFORMS
            assert session.vad is vad
            assert session.tts is not None
            assert session.tts.capabilities.streaming is True
            assert isinstance(session.tts, tts.StreamAdapter)
            conn = session.conn_options
            assert conn.llm_conn_options.max_retry == 0
            assert conn.stt_conn_options.max_retry == 1
            assert conn.tts_conn_options.max_retry == 1
            assert conn.llm_conn_options.timeout == 5.0
            assert conn.stt_conn_options.timeout == 5.0
            assert conn.tts_conn_options.timeout == 5.0
            assert conn.max_unrecoverable_errors == MAX_UNRECOVERABLE_ERRORS
            await session.aclose()

    asyncio.run(main())


def test_prepared_tts_body() -> None:
    bodies: list[dict[str, Any]] = []
    calls: list[tuple[str, Prepared, float]] = []

    async def speech(request: web.Request) -> web.Response:
        bodies.append(await request.json())
        return web.Response(body=bytes(4410 * 2), content_type="audio/pcm")

    async def main() -> None:
        app = web.Application()
        app.router.add_post("/v1/audio/speech", speech)
        async with TestServer(app, host="127.0.0.1") as server:
            settings = Settings(audio_url=str(server.make_url("/v1")))
            with ThreadPoolExecutor(max_workers=1) as executor:
                adapter = make_tts(
                    settings,
                    executor,
                    lambda text, prepared, ms: calls.append((text, prepared, ms)),
                )
                async with adapter.synthesize("Через 5 минут.") as stream:
                    async for _ in stream:
                        pass
                await adapter.aclose()

    asyncio.run(main())
    assert len(bodies) == 1
    body = bodies[0]
    assert body["language"] == "ru"
    assert body["voice"] == "F4"
    assert not any(char.isdigit() for char in body["input"])
    assert len(calls) == 1
    assert calls[0][1].leftover_digits == 0


def test_tts_url_and_sample_rate() -> None:
    bodies: list[dict[str, Any]] = []

    async def speech(request: web.Request) -> web.Response:
        bodies.append(await request.json())
        return web.Response(body=bytes(2400 * 2), content_type="audio/pcm")

    async def main() -> None:
        app = web.Application()
        app.router.add_post("/v1/audio/speech", speech)
        async with TestServer(app, host="127.0.0.1") as server:
            settings = Settings(
                audio_url="http://127.0.0.1:9/v1",
                tts_url=str(server.make_url("/v1")),
                agent={"tts_voice": "clone-perf", "tts_sample_rate": 24000},
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                adapter = make_tts(settings, executor, lambda text, prepared, ms: None)
                assert adapter.sample_rate == 24000
                async with adapter.synthesize("Привет.") as stream:
                    async for _ in stream:
                        pass
                await adapter.aclose()

    asyncio.run(main())
    assert len(bodies) == 1
    assert bodies[0]["voice"] == "clone-perf"


def test_server_options() -> None:
    options = server_options(Settings())
    assert options.port == 8084
    assert options.host == "127.0.0.1"
    assert options.num_idle_processes == 1
    assert options.load_threshold == math.inf
    assert options.ws_url == "ws://127.0.0.1:7880"
    assert options.initialize_process_timeout == 60.0
    assert options.drain_timeout == 5


def test_run_stops_without_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def run_app(options: Any) -> None:
        raise AssertionError("run_app called")

    monkeypatch.setenv("VOCA_INSTRUCTIONS_DIR", str(tmp_path))
    monkeypatch.setattr("voca.app.worker.cli.run_app", run_app)
    monkeypatch.setattr("sys.argv", ["voca"])
    with pytest.raises(SystemExit) as stopped:
        run("start", [])
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert "инструкции не загружены" in err
    assert str(tmp_path / "SOUL.md") in err


def test_run_stops_on_newer_session_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def run_app(options: Any) -> None:
        raise AssertionError("run_app called")

    instructions = tmp_path / "instructions"
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "instructions.example", instructions
    )
    store = tmp_path / "sessions.sqlite3"
    with closing(sqlite3.connect(store)) as db:
        db.execute("PRAGMA user_version = 2")
    monkeypatch.setenv("VOCA_INSTRUCTIONS_DIR", str(instructions))
    monkeypatch.setenv("VOCA_SESSION__PATH", str(store))
    monkeypatch.setattr("voca.app.worker.cli.run_app", run_app)
    monkeypatch.setattr("sys.argv", ["voca"])
    with pytest.raises(SystemExit) as stopped:
        run("start", [])
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert "хранилище сессии не открыто" in err
    assert str(store) in err


def test_make_llm_sampling() -> None:
    assert make_llm(Settings())._opts.extra_body == {"thinking_budget_tokens": 128}
    body = make_llm(Settings(model={"temperature": 0.2}))._opts.extra_body
    assert body == {"thinking_budget_tokens": 128, "temperature": 0.2}


def context_rows(tmp_path: Path, props: dict[str, Any] | None) -> list[Any]:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(props)

    async def main() -> None:
        app = web.Application()
        if props is not None:
            app.router.add_get("/props", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            settings = Settings(llm_url=str(server.make_url("/v1")))
            log = JsonlLog(tmp_path / "events.jsonl", session="t")
            await check_context(settings, log)
            log.close()

    asyncio.run(main())
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_context_mismatch_written(tmp_path: Path) -> None:
    rows = context_rows(tmp_path, {"default_generation_settings": {"n_ctx": 8192}})
    assert len(rows) == 1
    assert rows[0]["kind"] == "context_mismatch"
    assert rows[0]["n_ctx"] == 8192
    assert rows[0]["context_tokens"] == 16384


def test_context_match_or_unreachable_writes_nothing(tmp_path: Path) -> None:
    matched = context_rows(
        tmp_path / "match", {"default_generation_settings": {"n_ctx": 16384}}
    )
    assert not [row for row in matched if row["kind"] == "context_mismatch"]
    missing = context_rows(tmp_path / "missing", None)
    assert not [row for row in missing if row["kind"] == "context_mismatch"]


def test_run_stops_on_broken_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def run_app(options: Any) -> None:
        raise AssertionError("run_app called")

    monkeypatch.setattr("voca.app.worker.cli.run_app", run_app)
    monkeypatch.setattr("sys.argv", ["voca"])
    for index, text in enumerate(
        (
            "[compression\n",
            "[compression]\nhard_limit = 0.3\n",
            '[compression.model]\napi_key = "x"\n',
        )
    ):
        path = tmp_path / f"config-{index}.toml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setenv("VOCA_CONFIG", str(path))
        with pytest.raises(SystemExit) as stopped:
            run("start", [])
        assert stopped.value.code == 1
        err = capsys.readouterr().err
        assert "конфиг не прочитан" in err
        assert str(path) in err


def test_compaction_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    assert compaction_limits(Settings()) == Limits(
        threshold=9830,
        tail_budget=1966,
        tail_turns_min=2,
        idle_hours=4,
        daily_at="04:00",
    )
    assert compaction_limits(Settings(compression={"threshold": 0})).threshold == 0
    low = compaction_limits(Settings(compression={"threshold": 0.08}))
    assert low.threshold == 1310
    assert low.tail_budget == 262
    off = compaction_limits(Settings(compression={"idle_hours": 0, "daily_at": ""}))
    assert off.idle_hours == 0
    assert off.daily_at == ""
    monkeypatch.setenv("VOCA_COMPRESSION__IDLE_HOURS", "0.02")
    monkeypatch.setenv("VOCA_COMPRESSION__DAILY_AT", "")
    stand = compaction_limits(Settings())
    assert stand.idle_hours == 0.02
    assert stand.daily_at == ""


def test_make_summarizer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_summarizer(Settings()).ready is False
    monkeypatch.setenv("VOCA_LITELLM_KEY", "abc")
    summarizer = make_summarizer(Settings())
    assert summarizer.ready is True
    assert summarizer.client is not None
    assert str(summarizer.client.base_url) == "https://litellm.priney.com/v1/"
    assert summarizer.client.max_retries == 0
    assert summarizer.client.timeout == 60
    assert summarizer.model == "glm-5.3-flash"
    asyncio.run(summarizer.close())


def test_make_memory_model(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_memory_model(Settings()).ready is False
    monkeypatch.setenv("VOCA_LITELLM_KEY", "abc")
    monkeypatch.setenv("VOCA_MEMORY__MODEL__MODEL", "m-x")
    model = make_memory_model(Settings())
    assert model.ready is True
    assert model.client is not None
    assert str(model.client.base_url) == "https://litellm.priney.com/v1/"
    assert model.client.max_retries == 0
    assert model.client.timeout == 60
    assert model.model == "m-x"
    summarizer = make_summarizer(Settings())
    assert summarizer.model == "glm-5.3-flash"
    asyncio.run(model.close())
    asyncio.run(summarizer.close())


def test_make_decider_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOCA_DECISION__ENABLED", "false")

    async def main() -> object:
        decider = make_decider(Settings())
        result = await decider.sort({"фраза пользователя": "да"})
        await decider.aclose()
        return result

    assert asyncio.run(main()) == DecisionFailure("disabled", 0)


def test_make_decider_sorter() -> None:
    async def main() -> None:
        decider = make_decider(Settings())
        try:
            assert decider.sorter == Settings().decision.sorter_prompt()
            assert decider.sorter_timeout_s == 0.3
            assert decider.client is not None
            assert decider.client.timeout.read == 1.5
        finally:
            await decider.aclose()

    asyncio.run(main())
