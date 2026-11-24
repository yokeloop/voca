import asyncio
import json
import os
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from voca.config import MemoryConfig
from voca.conversation.memory import (
    BATCH,
    Entry,
    Op,
    apply_ops,
    build_extract_prompt,
    build_retry_prompt,
    fits,
    parse_file,
    parse_ops,
    render_file,
)
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.session.model import ArchiveRow
from voca.session.store import SessionStore
from voca.telemetry.jsonl import JsonlLog


def state_path(memory: Path) -> Path:
    return memory.with_suffix(".state.json")


def read_state(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    last_id = data.get("last_message_id")
    if not isinstance(last_id, int) or isinstance(last_id, bool):
        return 0
    return last_id


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_current(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


class MemoryKeeper:
    def __init__(
        self,
        *,
        store: SessionStore,
        log: JsonlLog,
        config: MemoryConfig,
        model: Summarizer,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.log = log
        self.config = config
        self.model = model
        self.clock = clock
        self.state = state_path(config.path)
        self.failed_at: int | None = None
        self.task: asyncio.Task[None] | None = None

    def check(self) -> bool:
        if self.config.every_turns == 0 or (
            self.task is not None and not self.task.done()
        ):
            return False
        last_id = read_state(self.state)
        since = (
            self.failed_at
            if self.failed_at is not None and self.failed_at > last_id
            else last_id
        )
        probe = self.store.after(since, BATCH)
        user_turns = sum(1 for _, row in probe if row.kind == "user")
        if user_turns < self.config.every_turns:
            return False
        batch = probe if since == last_id else self.store.after(last_id, BATCH)
        user_turns = sum(1 for _, row in batch if row.kind == "user")
        if not self.model.ready:
            self.log.write("memory_failed", reason="no_key", ms=0, messages=len(batch))
            self.failed_at = batch[-1][0]
            return False
        try:
            current = read_current(self.config.path)
        except (OSError, UnicodeDecodeError) as error:
            self.log.write(
                "memory_failed",
                reason="read",
                ms=0,
                messages=len(batch),
                error=type(error).__name__,
            )
            self.failed_at = batch[-1][0]
            return False
        entries = parse_file(current)
        prompt = build_extract_prompt(
            entries,
            [row for _, row in batch],
            today=date.fromtimestamp(self.clock()),
            max_chars=self.config.max_chars,
        )
        self.task = asyncio.create_task(
            self._run(current, entries, batch, user_turns, prompt)
        )
        return True

    async def _ask(self, prompt: str) -> tuple[Summary | Failure, dict[str, str]]:
        (outcome,) = await asyncio.gather(
            self.model.summarize(prompt), return_exceptions=True
        )
        if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, Exception):
            return Failure("error", 0), {"error": type(outcome).__name__}
        return outcome, {}

    async def _run(
        self,
        current: str,
        entries: list[Entry],
        batch: list[tuple[int, ArchiveRow]],
        user_turns: int,
        prompt: str,
    ) -> None:
        result, named = await self._ask(prompt)
        attempts = 1
        if isinstance(result, Failure):
            self.log.write(
                "memory_failed",
                reason=result.reason,
                ms=result.ms,
                messages=len(batch),
                attempts=attempts,
                **named,
            )
            self.failed_at = batch[-1][0]
            return
        ms = result.ms
        ops: list[Op] | None = parse_ops(result.body, entries)
        if ops is None:
            self.log.write(
                "memory_failed",
                reason="bad_reply",
                ms=ms,
                messages=len(batch),
                attempts=attempts,
            )
            self.failed_at = batch[-1][0]
            return
        text = render_file(apply_ops(entries, ops))
        if not fits(text, self.config.max_chars):
            over = len(text) - self.config.max_chars
            retry, named = await self._ask(
                build_retry_prompt(prompt, result.body, over)
            )
            attempts = 2
            ms += retry.ms
            if isinstance(retry, Failure):
                self.log.write(
                    "memory_failed",
                    reason=retry.reason,
                    ms=ms,
                    messages=len(batch),
                    attempts=attempts,
                    **named,
                )
                self.failed_at = batch[-1][0]
                return
            ops = parse_ops(retry.body, entries)
            if ops is None:
                self.log.write(
                    "memory_failed",
                    reason="bad_reply",
                    ms=ms,
                    messages=len(batch),
                    attempts=attempts,
                )
                self.failed_at = batch[-1][0]
                return
            text = render_file(apply_ops(entries, ops))
            if not fits(text, self.config.max_chars):
                self.log.write(
                    "memory_failed",
                    reason="too_long",
                    ms=ms,
                    messages=len(batch),
                    attempts=attempts,
                )
                self.failed_at = batch[-1][0]
                return
        if ops:
            try:
                write_atomic(self.config.path, text + "\n")
            except OSError as error:
                self.log.write(
                    "memory_failed",
                    reason="write",
                    ms=ms,
                    messages=len(batch),
                    attempts=attempts,
                    error=type(error).__name__,
                )
                self.failed_at = batch[-1][0]
                return
        last_message_id = batch[-1][0]
        try:
            write_atomic(self.state, json.dumps({"last_message_id": last_message_id}))
        except OSError as error:
            self.log.write(
                "memory_failed",
                reason="write",
                ms=ms,
                messages=len(batch),
                attempts=attempts,
                error=type(error).__name__,
            )
            self.failed_at = batch[-1][0]
            return
        self.failed_at = None
        chars_before = len(current.strip())
        self.log.write(
            "memory_updated",
            added=sum(1 for op in ops if op.op == "add"),
            replaced=sum(1 for op in ops if op.op == "replace"),
            removed=sum(1 for op in ops if op.op == "remove"),
            chars_before=chars_before,
            chars_after=len(text) if ops else chars_before,
            messages=len(batch),
            user_turns=user_turns,
            last_message_id=last_message_id,
            ms=ms,
            attempts=attempts,
        )

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
        self.task = None
