from typing import Any

from voca.session.model import ArchiveRow, archive_row, fold, snapshot_items


def message(id: str, role: str, content: list[Any], **extra: Any) -> dict[str, Any]:
    return {
        "id": id,
        "type": "message",
        "role": role,
        "content": content,
        "created_at": 1.5,
        **extra,
    }


def test_snapshot_items_drops_system_developer_config() -> None:
    items = [
        message("s", "system", ["rules"]),
        message("u", "user", ["hi"]),
        message("d", "developer", ["dev"]),
        {"id": "cfg", "type": "agent_config_update", "created_at": 1.5},
        message("a", "assistant", ["hello"]),
    ]
    assert [item["id"] for item in snapshot_items(items)] == ["u", "a"]


def test_archive_row_kinds() -> None:
    user = message("u", "user", ["first", {"type": "image_content"}, "second"])
    assistant = message("a", "assistant", ["reply"], interrupted=True)
    call = {
        "id": "fc",
        "type": "function_call",
        "call_id": "c1",
        "name": "delegate",
        "arguments": '{"task": "x"}',
        "created_at": 2.0,
    }
    result = {
        "id": "fo",
        "type": "function_call_output",
        "call_id": "c1",
        "name": "delegate",
        "output": "failed",
        "is_error": True,
        "created_at": 3.0,
    }
    assert archive_row(user) == ArchiveRow(
        kind="user",
        item_id="u",
        at=1.5,
        call_id=None,
        name=None,
        text="first\nsecond",
        data={},
    )
    assert archive_row(assistant) == ArchiveRow(
        kind="assistant",
        item_id="a",
        at=1.5,
        call_id=None,
        name=None,
        text="reply",
        data={"interrupted": True},
    )
    assert archive_row(call) == ArchiveRow(
        kind="tool_call",
        item_id="fc",
        at=2.0,
        call_id="c1",
        name="delegate",
        text='{"task": "x"}',
        data={},
    )
    assert archive_row(result) == ArchiveRow(
        kind="tool_result",
        item_id="fo",
        at=3.0,
        call_id="c1",
        name="delegate",
        text="failed",
        data={"is_error": True},
    )


def test_archive_row_skips_system() -> None:
    assert archive_row(message("s", "system", ["rules"])) is None
    assert (
        archive_row({"id": "cfg", "type": "agent_config_update", "created_at": 1.5})
        is None
    )


def test_fold() -> None:
    assert fold("Ёлка ещё") == "Елка еще"
