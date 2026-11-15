from dataclasses import dataclass
from typing import Any

SKIPPED_ROLES = frozenset({"system", "developer"})


@dataclass(frozen=True)
class ArchiveRow:
    kind: str
    item_id: str
    at: float
    call_id: str | None
    name: str | None
    text: str
    data: dict[str, Any]


def snapshot_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item["type"] != "agent_config_update"
        and not (item["type"] == "message" and item["role"] in SKIPPED_ROLES)
    ]


def message_text(item: dict[str, Any]) -> str:
    return "\n".join(part for part in item["content"] if isinstance(part, str))


def archive_row(item: dict[str, Any]) -> ArchiveRow | None:
    item_id = item["id"]
    at = item["created_at"]
    kind = item["type"]
    if kind == "message" and item["role"] == "user":
        return ArchiveRow("user", item_id, at, None, None, message_text(item), {})
    if kind == "message" and item["role"] == "assistant":
        data = {"interrupted": item.get("interrupted", False)}
        return ArchiveRow(
            "assistant", item_id, at, None, None, message_text(item), data
        )
    if kind == "function_call":
        return ArchiveRow(
            "tool_call",
            item_id,
            at,
            item["call_id"],
            item["name"],
            item["arguments"],
            {},
        )
    if kind == "function_call_output":
        data = {"is_error": item["is_error"]}
        return ArchiveRow(
            "tool_result",
            item_id,
            at,
            item["call_id"],
            item.get("name") or None,
            item["output"],
            data,
        )
    return None


def fold(text: str) -> str:
    return text.replace("ё", "е").replace("Ё", "Е")
