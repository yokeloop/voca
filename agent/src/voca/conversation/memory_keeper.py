import asyncio
import json
import os
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from voca.config import MemoryConfig
from voca.conversation.memory import BATCH, build_extract_prompt, due, fits
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.session.model import ArchiveRow
from voca.session.store import SessionStore
from voca.telemetry.jsonl import JsonlLog


def state_path(memory: Path) -> Path:
    return memory.with_suffix(".state.json")


def read_state(path: Path) -> tuple[int, float | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, None
    if not isinstance(data, dict):
        return 0, None
    last_id = data.get("last_message_id")
    run_at = data.get("run_at")
    if not isinstance(last_id, int) or isinstance(last_id, bool):
        return 0, None
    if not isinstance(run_at, int | float) or isinstance(run_at, bool):
        return 0, None
    return last_id, float(run_at)


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
        self.attempted_at: float | None = None
        self.task: asyncio.Task[None] | None = None

    def check(self) -> bool:
        if self.config.interval_hours == 0 or (
            self.task is not None and not self.task.done()
        ):
            return False
        last_id, run_at = read_state(self.state)
        known = [at for at in (run_at, self.attempted_at) if at is not None]
        last_at = max(known) if known else None
        now = self.clock()
        if not due(now, last_at, self.config.interval_hours):
            return False
        batch = self.store.after(last_id, BATCH)
        if not batch:
            return False
        self.attempted_at = now
        if not self.model.ready:
            self.log.write("memory_failed", reason="no_key", ms=0, messages=len(batch))
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
            return False
        prompt = build_extract_prompt(
            current,
            [row for _, row in batch],
            today=date.fromtimestamp(now),
            max_chars=self.config.max_chars,
        )
        self.task = asyncio.create_task(self._run(current, batch, prompt, now))
        return True

    async def _run(
        self,
        current: str,
        batch: list[tuple[int, ArchiveRow]],
        prompt: str,
        at: float,
    ) -> None:
        messages = len(batch)
        named: dict[str, str] = {}
        (outcome,) = await asyncio.gather(
            self.model.summarize(prompt), return_exceptions=True
        )
        result: Summary | Failure
        if isinstance(outcome, BaseException):
            result = Failure("error", 0)
            named = {"error": type(outcome).__name__}
        else:
            result = outcome
        if isinstance(result, Failure):
            self.log.write(
                "memory_failed",
                reason=result.reason,
                ms=result.ms,
                messages=messages,
                **named,
            )
            return
        if not fits(result.body, self.config.max_chars):
            self.log.write(
                "memory_failed", reason="too_long", ms=result.ms, messages=messages
            )
            return
        try:
            write_atomic(self.config.path, result.body)
        except OSError as error:
            self.log.write(
                "memory_failed",
                reason="write",
                ms=result.ms,
                messages=messages,
                error=type(error).__name__,
            )
            return
        last_message_id = batch[-1][0]
        try:
            write_atomic(
                self.state,
                json.dumps({"last_message_id": last_message_id, "run_at": at}),
            )
        except OSError as error:
            self.log.write(
                "memory_failed",
                reason="write",
                ms=result.ms,
                messages=messages,
                error=type(error).__name__,
            )
            return
        self.log.write(
            "memory_updated",
            chars_before=len(current.strip()),
            chars_after=len(result.body),
            messages=messages,
            last_message_id=last_message_id,
            ms=result.ms,
        )

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
        self.task = None
