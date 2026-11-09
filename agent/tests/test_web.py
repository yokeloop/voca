import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from livekit import api

from voca.app.web import create_app
from voca.config import Settings


def make_dist(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>VOCA</title>")
    return tmp_path


def run(
    settings: Settings,
    dist: Path,
    scenario: Callable[[TestClient[object, object]], Awaitable[None]],
) -> None:
    async def main() -> None:
        async with TestClient(TestServer(create_app(settings, dist))) as client:
            await scenario(client)

    asyncio.run(main())


def test_healthz(tmp_path: Path) -> None:
    async def scenario(client: TestClient[object, object]) -> None:
        response = await client.get("/healthz")
        assert response.status == 200
        assert await response.json() == {"status": "ok"}

    run(Settings(), make_dist(tmp_path), scenario)


def test_index(tmp_path: Path) -> None:
    async def scenario(client: TestClient[object, object]) -> None:
        response = await client.get("/")
        assert response.status == 200
        assert "<title>VOCA</title>" in await response.text()

    run(Settings(), make_dist(tmp_path), scenario)


def test_token(tmp_path: Path) -> None:
    settings = Settings(
        public_livekit_url="wss://voca.example",
        livekit_api_key="devkey",
        livekit_api_secret="secret",
    )

    async def scenario(client: TestClient[object, object]) -> None:
        response = await client.post("/api/token", json={"device": "phone"})
        assert response.status == 200
        body = await response.json()
        assert body["url"] == "wss://voca.example"
        claims = api.TokenVerifier("devkey", "secret").verify(body["token"])
        assert claims.identity == "user-phone"
        assert claims.video.room == "voca"
        assert claims.video.room_join is True

    run(settings, make_dist(tmp_path), scenario)


def test_token_rejects_bad_device(tmp_path: Path) -> None:
    async def scenario(client: TestClient[object, object]) -> None:
        response = await client.post("/api/token", json={"device": "Bad Name"})
        assert response.status == 400

    run(Settings(), make_dist(tmp_path), scenario)


def test_missing_dist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="pnpm --dir web build"):
        create_app(Settings(), tmp_path)
