import asyncio
import json
from collections.abc import Callable
from typing import Any

from voca.clientio import metrics
from voca.clientio.metrics import TOPIC, bind_metrics, parse_client


def message(**fields: object) -> str:
    return json.dumps(
        {
            "type": "client_e2e",
            "seq": 1,
            "client_e2e_ms": 1234,
            "speech_ms": 900,
            **fields,
        }
    )


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[Any, str], None]] = {}

    def register_text_stream_handler(
        self, topic: str, handler: Callable[[Any, str], None]
    ) -> None:
        self.handlers[topic] = handler


class FakeReader:
    def __init__(self, text: str) -> None:
        self.text = text

    async def read_all(self) -> str:
        return self.text


def test_parse_client() -> None:
    assert parse_client(message()) == {
        "seq": 1,
        "client_e2e_ms": 1234,
        "speech_ms": 900,
    }
    assert parse_client("{") is None
    assert parse_client("[1]") is None
    assert parse_client(message(type="state")) is None
    assert parse_client(message(seq=0)) is None
    assert parse_client(message(seq=True)) is None
    assert parse_client(message(client_e2e_ms=0)) is None
    assert parse_client(message(client_e2e_ms=30001)) is None
    no_speech = json.loads(message())
    del no_speech["speech_ms"]
    assert parse_client(json.dumps(no_speech)) is None


def test_bind_metrics_writes_row() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def write(kind: str, **fields: object) -> None:
        calls.append((kind, fields))

    async def scenario() -> None:
        room = FakeRoom()
        bind_metrics(room, write)
        handler = room.handlers[TOPIC]
        handler(FakeReader(message()), "user-host")
        handler(FakeReader("not json"), "user-host")
        await asyncio.gather(*metrics.TASKS)

    asyncio.run(scenario())
    assert calls == [("client", {"seq": 1, "client_e2e_ms": 1234, "speech_ms": 900})]
