import asyncio
import json
import logging
from collections.abc import Sequence

from livekit import rtc
from livekit.agents import JobContext

from voca.session.model import ArchiveRow
from voca.session.store import SessionStore

TOPIC = "voca.history"
WHO = {"user": "вы", "assistant": "агент", "summary": "сжатие"}


def message(session_id: str, rows: Sequence[ArchiveRow]) -> str:
    return json.dumps(
        {
            "type": "history",
            "session_id": session_id,
            "items": [
                {
                    "id": row.item_id,
                    "at": row.at,
                    "who": WHO[row.kind],
                    "text": "" if row.kind == "summary" else row.text,
                }
                for row in rows
            ],
        },
        ensure_ascii=False,
    )


class HistoryChannel:
    def __init__(self, store: SessionStore, session_id: str, limit: int) -> None:
        self.store = store
        self.session_id = session_id
        self.limit = limit
        self.room: rtc.Room | None = None
        self.sending: asyncio.Task[None] | None = None

    def bind(self, room: rtc.Room) -> None:
        self.room = room
        room.on(
            "participant_connected",
            lambda participant: self.send(participant.identity),
        )

    def send(self, identity: str) -> None:
        if self.room is None:
            return
        self.sending = asyncio.create_task(self._send(identity, self.sending))

    async def _send(self, identity: str, previous: asyncio.Task[None] | None) -> None:
        if previous is not None:
            await previous
        try:
            assert self.room is not None
            text = message(
                self.session_id, self.store.recent(self.session_id, self.limit)
            )
            await self.room.local_participant.send_text(
                text, topic=TOPIC, destination_identities=[identity]
            )
        except Exception:
            logging.getLogger("voca.history").exception("send history failed")


async def send_on_wait(
    ctx: JobContext, history: HistoryChannel
) -> rtc.RemoteParticipant:
    participant = await ctx.wait_for_participant()
    history.send(participant.identity)
    return participant
