import json
from dataclasses import dataclass

SENTENCE_ENDS = ".!?…"
FACT_PREFIXES = ("voca_", "delivery_")


@dataclass(frozen=True)
class Entry:
    id: str
    type: str
    role: str | None = None
    text: str = ""
    interrupted: bool = False
    call_id: str | None = None
    name: str | None = None
    output: str = ""
    started_at: float | None = None
    stopped_at: float | None = None


@dataclass(frozen=True)
class Glued:
    drop: frozenset[str]
    fragment: str
    gap_s: float


def _not_sent(entry: Entry) -> bool:
    try:
        output = json.loads(entry.output)
    except ValueError:
        return False
    return isinstance(output, dict) and output.get("event") == "not_sent"


def glue(
    entries: list[Entry], *, started_at: float | None, window_s: float
) -> Glued | None:
    last = None
    for index, entry in enumerate(entries):
        if entry.type == "message" and entry.role == "user" and entry.text:
            last = index
    if last is None:
        return None
    fragment = entries[last]
    after = entries[last + 1 :]
    unsent = {
        entry.call_id
        for entry in after
        if entry.type == "function_call_output"
        and entry.name == "delegate"
        and _not_sent(entry)
    }
    drop = {fragment.id}
    for entry in after:
        if entry.type == "message" and entry.role == "assistant":
            if not entry.interrupted or any(c in entry.text for c in SENTENCE_ENDS):
                return None
            drop.add(entry.id)
        elif (
            entry.type in ("function_call", "function_call_output")
            and entry.name == "delegate"
            and entry.call_id in unsent
        ):
            drop.add(entry.id)
        elif entry.call_id is not None and entry.call_id.startswith(FACT_PREFIXES):
            continue
        else:
            return None
    if started_at is None or fragment.stopped_at is None:
        return None
    gap_s = started_at - fragment.stopped_at
    if gap_s > window_s:
        return None
    return Glued(frozenset(drop), fragment.text, gap_s)
