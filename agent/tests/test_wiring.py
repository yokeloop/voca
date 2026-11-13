import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pydantic
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from livekit.agents import tts
from livekit.plugins import silero

from voca.app.wiring import (
    MAX_UNRECOVERABLE_ERRORS,
    TTS_TEXT_TRANSFORMS,
    build_session,
    make_tts,
)
from voca.app.worker import server_options
from voca.config import Settings
from voca.speech.prepare import Prepared


def test_settings_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert settings.agent_port == 8084
    assert settings.llm_thinking_budget == 128
    assert settings.state_dir.name == ".local"
    assert settings.instructions_dir == Path.home() / ".voca" / "instructions"
    assert settings.ctx_high_tokens() == 9830
    assert settings.ctx_low_tokens() == 6553
    monkeypatch.setenv("VOCA_AGENT_PORT", "9999")
    assert Settings().agent_port == 9999
    monkeypatch.setenv("VOCA_INSTRUCTIONS_DIR", "~/x")
    assert Settings().instructions_dir == Path.home() / "x"


def test_settings_trim_thresholds() -> None:
    with pytest.raises(pydantic.ValidationError):
        Settings(ctx_trim_high=0.4, ctx_trim_low=0.6)
    with pytest.raises(pydantic.ValidationError):
        Settings(ctx_trim_low=0)
    Settings(ctx_trim_high=0.08, ctx_trim_low=0.05)


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
                stress=lambda text: text,
                executor=executor,
                on_prepared=lambda text, prepared, ms: None,
            )
            options = session.options
            assert session.turn_detection == "vad"
            assert options.interruption["mode"] == "vad"
            assert options.interruption["min_duration"] == 0.5
            assert options.interruption["min_words"] == 0
            assert options.preemptive_generation["enabled"] is False
            assert options.endpointing["min_delay"] == 0.5
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
                    lambda text: text,
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


def test_server_options() -> None:
    options = server_options(Settings())
    assert options.port == 8084
    assert options.host == "127.0.0.1"
    assert options.num_idle_processes == 1
    assert options.load_threshold == math.inf
    assert options.ws_url == "ws://127.0.0.1:7880"
    assert options.initialize_process_timeout == 60.0
    assert options.drain_timeout == 5
