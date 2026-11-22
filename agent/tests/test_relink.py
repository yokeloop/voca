import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from livekit import rtc
from livekit.agents.voice.room_io import RoomIO, RoomOptions

from voca.app.worker import relink


def member(identity: str) -> Any:
    return SimpleNamespace(
        identity=identity,
        sid=f"PA_{identity}",
        name="",
        metadata="",
        attributes={},
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
        disconnect_reason=None,
    )


def make() -> tuple[RoomIO, Any]:
    room = SimpleNamespace(
        remote_participants={},
        local_participant=SimpleNamespace(identity="agent"),
        name="voca",
    )
    io = RoomIO(
        MagicMock(),
        cast(rtc.Room, room),
        options=RoomOptions(close_on_disconnect=False),
    )
    return io, room


def join(io: RoomIO, room: Any, participant: Any) -> None:
    room.remote_participants[participant.identity] = participant
    io._on_participant_connected(participant)


def leave(io: RoomIO, room: Any, participant: Any) -> None:
    del room.remote_participants[participant.identity]
    io._on_participant_disconnected(participant)


def linked(io: RoomIO) -> str | None:
    participant = io.linked_participant
    return None if participant is None else participant.identity


def test_relink_new_identity() -> None:
    async def main() -> None:
        io, room = make()
        a, b = member("user-a"), member("user-b")
        join(io, room, a)
        io.set_participant(a.identity)
        leave(io, room, a)
        assert relink(io, room.remote_participants, leaving=a.identity) is None
        join(io, room, b)
        assert io.linked_participant is None
        assert relink(io, room.remote_participants, joined=b.identity) == b.identity
        assert linked(io) == b.identity

    asyncio.run(main())


def test_relink_on_leave_to_remaining_user() -> None:
    async def main() -> None:
        io, room = make()
        a, b = member("user-a"), member("user-b")
        join(io, room, a)
        io.set_participant(a.identity)
        join(io, room, b)
        assert linked(io) == a.identity
        leave(io, room, a)
        assert relink(io, room.remote_participants, leaving=a.identity) == b.identity
        assert linked(io) == b.identity

    asyncio.run(main())


def test_relink_keeps_live_link() -> None:
    async def main() -> None:
        io, room = make()
        a = member("user-a")
        join(io, room, a)
        io.set_participant(a.identity)
        join(io, room, member("user-c"))
        assert relink(io, room.remote_participants, joined="user-c") is None
        assert linked(io) == a.identity

    asyncio.run(main())


def test_relink_ignores_non_users() -> None:
    async def main() -> None:
        io, room = make()
        a = member("user-a")
        join(io, room, a)
        io.set_participant(a.identity)
        join(io, room, member("agent-x"))
        leave(io, room, a)
        with patch.object(io, "set_participant", wraps=io.set_participant) as spy:
            assert relink(io, room.remote_participants, leaving=a.identity) is None
        spy.assert_not_called()

    asyncio.run(main())
