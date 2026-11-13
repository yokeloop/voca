from collections.abc import Sequence
from dataclasses import dataclass

TURN_KINDS = frozenset({"message", "function_call", "function_call_output"})
KEPT_ROLES = frozenset({"system", "developer"})


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    role: str | None
    call_id: str | None
    tokens: int


@dataclass(frozen=True)
class Trim:
    drop: frozenset[str]
    estimate_before: int
    estimate_after: int


def approx_tokens(text: str) -> int:
    return len(text) // 3


def split_turns(items: Sequence[Item]) -> list[list[Item]]:
    turns: list[list[Item]] = []
    pending: set[str] = set()
    for item in items:
        if item.kind not in TURN_KINDS or (
            item.kind == "message" and item.role in KEPT_ROLES
        ):
            continue
        if not turns or (
            item.kind == "message" and item.role == "user" and not pending
        ):
            turns.append([])
        if item.kind == "function_call" and item.call_id is not None:
            pending.add(item.call_id)
        elif item.kind == "function_call_output":
            pending.discard(item.call_id or "")
        turns[-1].append(item)
    return turns


def trim_chat_ctx(items: Sequence[Item], *, high: int, low: int) -> Trim | None:
    total = sum(item.tokens for item in items)
    if total <= high:
        return None
    remaining = total
    drop: set[str] = set()
    for turn in split_turns(items)[:-1]:
        if remaining <= low:
            break
        drop.update(item.id for item in turn)
        remaining -= sum(item.tokens for item in turn)
    if not drop:
        return None
    return Trim(frozenset(drop), total, remaining)
