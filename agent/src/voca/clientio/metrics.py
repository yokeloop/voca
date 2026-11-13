import asyncio
import json
import logging
import math
from collections.abc import Callable

from livekit import rtc

TOPIC = "voca.metrics"
MAX_CLIENT_MS = 30000
TASKS: set[asyncio.Task[None]] = set()


def parse_client(text: str) -> dict[str, int | float] | None:
    try:
        message = json.loads(text)
    except ValueError:
        return None
    if not isinstance(message, dict) or message.get("type") != "client_e2e":
        return None
    seq = message.get("seq")
    client_e2e_ms = message.get("client_e2e_ms")
    speech_ms = message.get("speech_ms")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        return None
    if (
        not isinstance(client_e2e_ms, int | float)
        or isinstance(client_e2e_ms, bool)
        or not math.isfinite(client_e2e_ms)
        or not 0 < client_e2e_ms <= MAX_CLIENT_MS
    ):
        return None
    if (
        not isinstance(speech_ms, int | float)
        or isinstance(speech_ms, bool)
        or not math.isfinite(speech_ms)
        or speech_ms < 0
    ):
        return None
    return {"seq": seq, "client_e2e_ms": client_e2e_ms, "speech_ms": speech_ms}


def bind_metrics(room: rtc.Room, write: Callable[..., None]) -> None:
    async def read(reader: rtc.TextStreamReader) -> None:
        text = await reader.read_all()
        fields = parse_client(text)
        if fields is None:
            logging.getLogger("voca.metrics").warning(
                "invalid client message: %s", text[:200]
            )
            return
        write("client", **fields)

    def handler(reader: rtc.TextStreamReader, identity: str) -> None:
        task = asyncio.create_task(read(reader))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)

    room.register_text_stream_handler(TOPIC, handler)
