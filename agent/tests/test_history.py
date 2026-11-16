import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voca.clientio.history import HistoryChannel, message, send_on_wait
from voca.session.model import ArchiveRow
from voca.session.store import Session, SessionStore


class FakeParticipant:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, list[str]]] = []

    async def send_text(
        self, text: str, *, topic: str, destination_identities: list[str]
    ) -> None:
        self.sent.append((text, topic, destination_identities))


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.local_participant = FakeParticipant()

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers[event] = handler


class FakeRemote:
    def __init__(self, identity: str) -> None:
        self.identity = identity


class FakeContext:
    def __init__(self, participant: FakeRemote) -> None:
        self.participant = participant

    async def wait_for_participant(self) -> FakeRemote:
        return self.participant


def row(
    kind: str,
    item_id: str,
    at: float,
    text: str = "",
    call_id: str | None = None,
    name: str | None = None,
) -> ArchiveRow:
    return ArchiveRow(
        kind=kind,
        item_id=item_id,
        at=at,
        call_id=call_id,
        name=name,
        text=text,
        data={},
    )


def archive(tmp_path: Path) -> tuple[SessionStore, Session]:
    store = SessionStore.open(tmp_path / "sessions.sqlite3")
    session = store.open_session()
    rows = [
        row("user", "u1", 1.0, "привет"),
        row("assistant", "a1", 2.0, "здравствуйте"),
        row("tool_call", "t1", 3.0, call_id="c1", name="delegate"),
        row("tool_result", "r1", 4.0, call_id="c1", name="delegate"),
        row("user", "u2", 5.0, "как дела"),
        row("assistant", "a2", 6.0, "хорошо"),
        row("summary", "s1", 5.0 - 1e-6, "пересказ беседы"),
        row("user", "u3", 7.0, "что нового"),
        row("assistant", "a3", 8.0, "ничего"),
    ]
    store.record(session.id, rows, [])
    return store, session


def test_message_schema() -> None:
    rows = [
        row("user", "u1", 1.0, "привет"),
        row("assistant", "a1", 2.0, "здравствуйте"),
        row("summary", "s1", 0.5, "пересказ"),
    ]
    assert json.loads(message("s", rows)) == {
        "type": "history",
        "session_id": "s",
        "items": [
            {"id": "u1", "at": 1.0, "who": "вы", "text": "привет"},
            {"id": "a1", "at": 2.0, "who": "агент", "text": "здравствуйте"},
            {"id": "s1", "at": 0.5, "who": "сжатие", "text": ""},
        ],
    }


def test_recent_keeps_last_replies(tmp_path: Path) -> None:
    store, session = archive(tmp_path)
    recent = store.recent(session.id, 5)
    assert [item.item_id for item in recent] == ["u2", "a2", "s1", "u3", "a3"]
    assert not {item.kind for item in recent} & {"tool_call", "tool_result"}
    assert store.recent(session.id, 0) == []
    store.close()


def test_sends_history_to_connected_participant(tmp_path: Path) -> None:
    store, session = archive(tmp_path)

    async def main() -> list[tuple[str, str, list[str]]]:
        room = FakeRoom()
        history = HistoryChannel(store, session.id, 5)
        history.bind(room)  # type: ignore[arg-type]
        room.handlers["participant_connected"](FakeRemote("user-phone"))
        assert history.sending is not None
        await history.sending
        return room.local_participant.sent

    sent = asyncio.run(main())
    store.close()
    assert len(sent) == 1
    text, topic, destinations = sent[0]
    assert topic == "voca.history"
    assert destinations == ["user-phone"]
    items = json.loads(text)["items"]
    assert len(items) <= 5
    assert [item["id"] for item in items] == ["u2", "a2", "s1", "u3", "a3"]
    assert not {item["id"] for item in items} & {"t1", "r1"}
    assert any(item["who"] == "сжатие" for item in items)


def test_sends_after_wait_for_participant(tmp_path: Path) -> None:
    store, session = archive(tmp_path)

    async def main() -> tuple[Any, list[tuple[str, str, list[str]]]]:
        room = FakeRoom()
        history = HistoryChannel(store, session.id, 5)
        history.bind(room)  # type: ignore[arg-type]
        participant = await send_on_wait(
            FakeContext(FakeRemote("user-host")),  # type: ignore[arg-type]
            history,
        )
        assert history.sending is not None
        await history.sending
        return participant, room.local_participant.sent

    participant, sent = asyncio.run(main())
    store.close()
    assert participant.identity == "user-host"
    assert len(sent) == 1
    assert sent[0][2] == ["user-host"]


def test_sends_in_chain_order(tmp_path: Path) -> None:
    store, session = archive(tmp_path)

    async def main() -> list[tuple[str, str, list[str]]]:
        room = FakeRoom()
        history = HistoryChannel(store, session.id, 5)
        history.bind(room)  # type: ignore[arg-type]
        history.send("user-a")
        history.send("user-b")
        assert history.sending is not None
        await history.sending
        return room.local_participant.sent

    sent = asyncio.run(main())
    store.close()
    assert [destinations for _, _, destinations in sent] == [["user-a"], ["user-b"]]
