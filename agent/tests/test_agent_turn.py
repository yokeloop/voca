import asyncio
import hashlib
import json
import shutil
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from livekit.agents import StopResponse, llm, stt
from livekit.agents.voice.events import AgentStateChangedEvent

from voca.clientio.cards import CardState
from voca.config import DecisionConfig
from voca.conversation.agent import (
    BLOCK_STRIKES,
    VocaAgent,
    records,
    refusal,
    talking,
)
from voca.conversation.compaction import Limits, estimate
from voca.conversation.instructions import InstructionLoader
from voca.conversation.memory import HEADER
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.conversation.verdict import DecisionFailure, Sorted, SorterPrompt
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"

SORTER_PROMPT = DecisionConfig().sorter_prompt()


class FakeSorter:
    def __init__(
        self, script: dict[str, Sorted] | None = None, failure: str = "connection"
    ) -> None:
        self.script = script or {}
        self.failure = failure
        self.seen: list[dict[str, str]] = []
        self.counts: list[int] = []

    async def sort(
        self, state: dict[str, str], requests: int = 0
    ) -> Sorted | DecisionFailure:
        self.seen.append(state)
        self.counts.append(requests)
        found = self.script.get(state["фраза пользователя"])
        return found if found is not None else DecisionFailure(self.failure, 300)


def make_agent(
    tmp_path: Path,
    *,
    ctx_high: int = 13926,
    ctx_low: int = 6553,
    chat_ctx: llm.ChatContext | None = None,
    limits: Limits | None = None,
    summarizer: Summarizer | None = None,
    clock: Callable[[], float] = time.time,
    compacted_at: float | None = None,
    sorter: FakeSorter | None = None,
    sorter_prompt: SorterPrompt | None = None,
    memory_path: Path | None = None,
) -> tuple[VocaAgent, Path, Path, CardState]:
    directory = tmp_path / "instructions"
    shutil.copytree(EXAMPLE, directory)
    loader = InstructionLoader(directory)
    instructions = loader.load()
    events = tmp_path / "events.jsonl"
    cards = CardState(instructions)
    agent = VocaAgent(
        loader=loader,
        instructions=instructions,
        log=JsonlLog(events, session="t"),
        cards=cards,
        ctx_high=ctx_high,
        ctx_low=ctx_low,
        chat_ctx=chat_ctx,
        limits=limits,
        summarizer=summarizer,
        clock=clock,
        compacted_at=compacted_at,
        sorter=sorter,
        sorter_prompt=sorter_prompt,
        memory_path=memory_path,
    )
    return agent, directory, events, cards


def rows(events: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()
    ]


def user_message() -> llm.ChatMessage:
    return llm.ChatMessage(role="user", content=["Привет"])


def test_late_failure_gives_last_valid(tmp_path: Path) -> None:
    agent, directory, events, cards = make_agent(tmp_path)
    before = agent.instructions
    (directory / "user.md").unlink()
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), user_message()))
    assert agent.instructions == before
    written = [row for row in rows(events) if row["kind"] == "instructions"]
    assert len(written) == 1
    assert written[0]["source"] == "last_valid"
    assert written[0]["error"]
    assert json.loads(cards.message())["instructions_source"] == "last_valid"


def test_changed_instructions_reach_turn(tmp_path: Path) -> None:
    agent, directory, events, _ = make_agent(tmp_path)
    first = agent.current.sha256
    (directory / "agent.md").write_text("Новый агент.", encoding="utf-8")
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), user_message()))
    assert isinstance(agent.instructions, str)
    assert agent.instructions.endswith("Новый агент.")
    written = [row for row in rows(events) if row["kind"] == "instructions"]
    assert len(written) == 1
    assert written[0]["source"] == "fresh"
    assert written[0]["sha256"] != first


def test_trim_persists_and_rewrites_turn(tmp_path: Path) -> None:
    agent, _, events, _ = make_agent(tmp_path, ctx_high=50, ctx_low=20)
    ctx = llm.ChatContext.empty()
    for n in range(5):
        ctx.add_message(role="user", content=f"{n}" * 90)
        ctx.add_message(role="assistant", content=f"{n}" * 90)

    async def main() -> llm.ChatContext:
        await agent.update_chat_ctx(ctx)
        turn_ctx = agent.chat_ctx.copy()
        await agent.on_user_turn_completed(turn_ctx, user_message())
        return turn_ctx

    turn_ctx = asyncio.run(main())
    assert len(agent.chat_ctx.items) < len(ctx.items)
    assert [item.id for item in turn_ctx.items] == [
        item.id for item in agent.chat_ctx.items
    ]
    trimmed = [row for row in rows(events) if row["kind"] == "context_trimmed"]
    assert len(trimmed) == 1
    removed = trimmed[0]["removed"]
    after = trimmed[0]["after"]
    before = trimmed[0]["estimate_before"]
    assert isinstance(removed, int) and removed > 0
    assert isinstance(after, int) and isinstance(before, int) and after < before


def test_no_change_keeps_turn(tmp_path: Path) -> None:
    agent, _, events, _ = make_agent(tmp_path)
    turn_ctx = llm.ChatContext.empty()
    turn_ctx.add_message(role="user", content="Раньше")
    items = turn_ctx.items
    ids = [item.id for item in items]
    asyncio.run(agent.on_user_turn_completed(turn_ctx, user_message()))
    assert turn_ctx.items is items
    assert [item.id for item in turn_ctx.items] == ids
    assert rows(events) == []


def test_refusal() -> None:
    assert refusal("stt", tts_down=False) == "не расслышал"
    assert refusal("llm", tts_down=False) == "не могу ответить сейчас"
    assert refusal("tts", tts_down=False) is None
    assert refusal("llm", tts_down=True) is None


def test_restored_context_reaches_agent(tmp_path: Path) -> None:
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content=["Меня зовут Арсений"])
    ctx.add_message(role="assistant", content=["Приятно познакомиться"])
    agent, _, _, _ = make_agent(tmp_path, chat_ctx=ctx)
    assert [item.id for item in agent.chat_ctx.items] == [item.id for item in ctx.items]


class FakeSummarizer(Summarizer):
    def __init__(
        self, result: Summary | Failure, *, ready: bool = True, hold: bool = False
    ) -> None:
        super().__init__(None, model="fake")
        self.result = result
        self.prompts: list[str] = []
        self.gate = asyncio.Event()
        self.is_ready = ready
        if not hold:
            self.gate.set()

    @property
    def ready(self) -> bool:
        return self.is_ready

    async def summarize(self, prompt: str) -> Summary | Failure:
        self.prompts.append(prompt)
        await self.gate.wait()
        return self.result


LIMITS = Limits(threshold=500, tail_budget=300, tail_turns_min=2)


def add_pairs(ctx: llm.ChatContext, first: int, count: int, start: float) -> None:
    for n in range(first, first + count):
        ctx.add_message(
            role="user", content=f"факт {n} " + "у" * 300, created_at=start + 2 * n
        )
        ctx.add_message(
            role="assistant", content="а" * 300, created_at=start + 2 * n + 1
        )


def history(count: int, start: float = 1000.0) -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    add_pairs(ctx, 1, count, start)
    ctx.items.insert(0, llm.ChatMessage(role="system", content=["Инструкции"]))
    return ctx


def summaries(ctx: llm.ChatContext) -> list[llm.ChatItem]:
    return [item for item in ctx.items if item.extra.get("voca_summary")]


def kinds_of(events: Path, kind: str) -> list[dict[str, object]]:
    return [row for row in rows(events) if row["kind"] == kind]


async def compact_and_accept(agent: VocaAgent) -> None:
    agent.maybe_compact()
    assert agent.pending is not None
    await agent.pending.task
    await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())


def test_turn_does_not_wait_and_accepts_next_turn(tmp_path: Path) -> None:
    async def main() -> tuple[
        VocaAgent, Path, FakeSummarizer, llm.ChatContext, llm.ChatContext
    ]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200), hold=True)
        agent, _, events, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        start = history(6)
        await agent.update_chat_ctx(start)
        agent.maybe_compact()
        await asyncio.sleep(0)
        await asyncio.wait_for(
            agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message()), 1
        )
        assert summaries(agent.chat_ctx) == []
        assert kinds_of(events, "compaction") == []
        ctx = agent.chat_ctx.copy()
        ctx.add_message(role="user", content="Поздняя реплика")
        ctx.add_message(role="assistant", content="Поздний ответ")
        await agent.update_chat_ctx(ctx)
        fake.gate.set()
        assert agent.pending is not None
        await agent.pending.task
        turn_ctx = agent.chat_ctx.copy()
        await agent.on_user_turn_completed(turn_ctx, user_message())
        return agent, events, fake, start, turn_ctx

    agent, events, fake, start, turn_ctx = asyncio.run(main())
    items = agent.chat_ctx.items
    assert len(summaries(agent.chat_ctx)) == 1
    assert items[1].extra.get("voca_summary")
    assert items[2].type == "message" and items[2].role == "user"
    assert (items[2].text_content or "").startswith("факт 5 ")
    ids = {item.id for item in items}
    assert not ids & {item.id for item in start.items[1:9]}
    assert [item.text_content for item in items[-2:] if item.type == "message"] == [
        "Поздняя реплика",
        "Поздний ответ",
    ]
    assert [item.id for item in turn_ctx.items] == [item.id for item in items]
    written = kinds_of(events, "compaction")
    assert len(written) == 1
    row = written[0]
    assert row["trigger"] == "threshold"
    assert row["folded_turns"] == 4
    assert row["ms"] == 1200
    assert row["summary_tokens"] == 50
    after = row["after"]
    before = row["estimate_before"]
    assert isinstance(after, int) and isinstance(before, int) and after < before
    assert len(fake.prompts) == 1


def test_summary_position_survives_insert_and_merge(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        agent, _, _, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        await compact_and_accept(agent)
        return agent

    agent = asyncio.run(main())
    ctx = agent.chat_ctx.copy()
    ctx.insert(llm.ChatMessage(role="user", content=["Ещё"]))
    other = ctx.copy()
    other.add_message(role="assistant", content="Ответ")
    ctx.merge(other)
    items = ctx.items
    assert items[1].extra.get("voca_summary")
    assert items[2].type == "message"
    assert (items[2].text_content or "").startswith("факт 5 ")
    assert [item.text_content for item in items[-2:] if item.type == "message"] == [
        "Ещё",
        "Ответ",
    ]


MEMORY = "## О пользователе\n- Кота зовут Ёжик.\n"
MEMORY_TEXT = "## О пользователе\n- Кота зовут Ёжик."


def spy_updates(agent: VocaAgent, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = agent.update_instructions

    async def spy(instructions: str) -> None:
        calls.append(instructions)
        await original(instructions)

    monkeypatch.setattr(agent, "update_instructions", spy)
    return calls


def memory_file(tmp_path: Path, text: str = MEMORY) -> Path:
    path = tmp_path / "memory.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_memory_in_instructions_at_start(tmp_path: Path) -> None:
    agent, directory, events, _ = make_agent(
        tmp_path, memory_path=memory_file(tmp_path)
    )
    assert isinstance(agent.instructions, str)
    assert agent.instructions.startswith(InstructionLoader(directory).load().text)
    assert HEADER in agent.instructions
    assert agent.instructions.endswith("- Кота зовут Ёжик.")
    loaded = kinds_of(events, "memory_loaded")
    assert len(loaded) == 1
    assert loaded[0]["chars"] == len(MEMORY_TEXT)
    assert loaded[0]["sha256"] == hashlib.sha256(MEMORY_TEXT.encode()).hexdigest()


def test_memory_edit_reaches_after_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = memory_file(tmp_path)

    async def main() -> tuple[VocaAgent, Path, list[str]]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        agent, _, events, _ = make_agent(
            tmp_path, limits=LIMITS, summarizer=fake, memory_path=path
        )
        calls = spy_updates(agent, monkeypatch)
        await agent.update_chat_ctx(history(6))
        path.write_text("- Собаку зовут Шарик.\n", encoding="utf-8")
        await compact_and_accept(agent)
        return agent, events, calls

    agent, events, calls = asyncio.run(main())
    assert isinstance(agent.instructions, str)
    assert "Шарик" in agent.instructions
    assert "Ёжик" not in agent.instructions
    loaded = kinds_of(events, "memory_loaded")
    assert len(loaded) == 2
    assert loaded[1]["sha256"] != loaded[0]["sha256"]
    assert len(calls) == 1


def test_memory_unchanged_after_compaction_no_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = memory_file(tmp_path)

    async def main() -> tuple[VocaAgent, Path, list[str], str]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        agent, _, events, _ = make_agent(
            tmp_path, limits=LIMITS, summarizer=fake, memory_path=path
        )
        before = agent.instructions
        assert isinstance(before, str)
        calls = spy_updates(agent, monkeypatch)
        await agent.update_chat_ctx(history(6))
        await compact_and_accept(agent)
        return agent, events, calls, before

    agent, events, calls, before = asyncio.run(main())
    assert calls == []
    assert len(kinds_of(events, "memory_loaded")) == 2
    assert agent.instructions == before


def test_agent_md_change_keeps_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, directory, events, _ = make_agent(
        tmp_path, memory_path=memory_file(tmp_path)
    )
    calls = spy_updates(agent, monkeypatch)
    (directory / "agent.md").write_text("Новый агент.", encoding="utf-8")
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), user_message()))
    assert isinstance(agent.instructions, str)
    assert agent.instructions.endswith(
        "Новый агент.\n\n" + HEADER + "\n## О пользователе\n- Кота зовут Ёжик."
    )
    assert len(calls) == 1
    assert len(kinds_of(events, "memory_loaded")) == 2


def test_agent_md_same_content_no_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, directory, events, _ = make_agent(
        tmp_path, memory_path=memory_file(tmp_path)
    )
    calls = spy_updates(agent, monkeypatch)
    same = (directory / "agent.md").read_text(encoding="utf-8")
    (directory / "agent.md").write_text(same, encoding="utf-8")
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), user_message()))
    assert calls == []
    assert len(kinds_of(events, "memory_loaded")) == 1


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_missing_or_blank_memory_gives_no_block(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "memory.md"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    agent, directory, events, _ = make_agent(tmp_path, memory_path=path)
    assert agent.instructions == InstructionLoader(directory).load().text
    assert isinstance(agent.instructions, str)
    assert HEADER not in agent.instructions
    loaded = kinds_of(events, "memory_loaded")
    assert len(loaded) == 1
    assert loaded[0]["chars"] == 0


def test_turn_without_changes_no_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _, events, _ = make_agent(tmp_path, memory_path=memory_file(tmp_path))
    calls = spy_updates(agent, monkeypatch)
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), user_message()))
    assert calls == []
    assert len(kinds_of(events, "memory_loaded")) == 1


def test_second_compaction_replaces_summary(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        agent, _, events, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        await compact_and_accept(agent)
        ctx = agent.chat_ctx.copy()
        add_pairs(ctx, 7, 4, 1000.0)
        await agent.update_chat_ctx(ctx)
        second = FakeSummarizer(Summary("второй", 40, 900))
        agent.summarizer = second
        await compact_and_accept(agent)
        return agent, events, second

    agent, events, second = asyncio.run(main())
    assert len(second.prompts) == 1
    assert "ПРЕДЫДУЩИЙ ПЕРЕСКАЗ:\n## О чём говорили\nфакт 1" in second.prompts[0]
    found = summaries(agent.chat_ctx)
    assert len(found) == 1
    assert found[0].type == "message"
    assert "второй" in (found[0].text_content or "")
    assert len(kinds_of(events, "compaction")) == 2


def test_second_trigger_while_pending(tmp_path: Path) -> None:
    async def main() -> FakeSummarizer:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200), hold=True)
        agent, _, _, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        agent.maybe_compact()
        assert agent.pending is not None
        task = agent.pending.task
        await asyncio.sleep(0)
        agent.maybe_compact()
        await asyncio.sleep(0)
        assert len(fake.prompts) == 1
        assert agent.pending is not None and agent.pending.task is task
        fake.gate.set()
        await task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert agent.pending is None
        agent.maybe_compact()
        assert agent.pending is None
        return fake

    fake = asyncio.run(main())
    assert len(fake.prompts) == 1


def test_failure_keeps_context(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, list[str]]:
        fake = FakeSummarizer(Failure("connection", 5000))
        agent, _, events, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        before = [item.id for item in agent.chat_ctx.items]
        await compact_and_accept(agent)
        return agent, events, before

    agent, events, before = asyncio.run(main())
    assert [item.id for item in agent.chat_ctx.items] == before
    failed = kinds_of(events, "compaction_failed")
    assert len(failed) == 1
    assert failed[0]["reason"] == "connection"
    assert failed[0]["ms"] == 5000
    assert "error" not in failed[0]
    estimate_before = failed[0]["estimate_before"]
    assert isinstance(estimate_before, int) and estimate_before > 0
    assert kinds_of(events, "compaction") == []
    assert kinds_of(events, "context_trimmed") == []
    assert agent.pending is None


def test_no_key_writes_failure(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer, list[str]]:
        fake = FakeSummarizer(Summary("x", 1, 1), ready=False)
        agent, _, events, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        before = [item.id for item in agent.chat_ctx.items]
        agent.maybe_compact()
        return agent, events, fake, before

    agent, events, fake, before = asyncio.run(main())
    assert agent.pending is None
    assert fake.prompts == []
    failed = kinds_of(events, "compaction_failed")
    assert len(failed) == 1
    assert failed[0]["reason"] == "no_key"
    assert [item.id for item in agent.chat_ctx.items] == before


def test_hard_limit_trims_to_trim_to(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path]:
        fake = FakeSummarizer(Failure("timeout", 60000))
        agent, _, events, _ = make_agent(
            tmp_path, ctx_high=1500, ctx_low=800, limits=LIMITS, summarizer=fake
        )
        await agent.update_chat_ctx(history(10))
        await compact_and_accept(agent)
        return agent, events

    agent, events = asyncio.run(main())
    assert len(kinds_of(events, "compaction_failed")) == 1
    trimmed = kinds_of(events, "context_trimmed")
    assert len(trimmed) == 1
    after = trimmed[0]["after"]
    assert isinstance(after, int) and after <= 800
    assert estimate(records(agent.chat_ctx.copy())) <= 800


def test_threshold_zero_disables(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer]:
        fake = FakeSummarizer(Summary("x", 1, 1))
        limits = Limits(threshold=0, tail_budget=300, tail_turns_min=2)
        agent, _, events, _ = make_agent(tmp_path, limits=limits, summarizer=fake)
        await agent.update_chat_ctx(history(10))
        agent.maybe_compact()
        return agent, events, fake

    agent, events, fake = asyncio.run(main())
    assert agent.pending is None
    assert fake.prompts == []
    assert rows(events) == []


def test_summary_survives_snapshot(tmp_path: Path) -> None:
    async def main() -> VocaAgent:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        agent, _, _, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        await compact_and_accept(agent)
        return agent

    agent = asyncio.run(main())
    restored = llm.ChatContext.from_dict(
        agent.chat_ctx.copy().to_dict(
            exclude_function_call=False,
            exclude_timestamp=False,
            exclude_config_update=True,
        )
    )
    found = [item for item in records(restored) if item.summary]
    assert len(found) == 1
    assert found[0].text.startswith("[СЖАТИЕ КОНТЕКСТА")


def test_trim_while_pending_then_adopt(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200), hold=True)
        agent, _, events, _ = make_agent(
            tmp_path, ctx_high=1500, ctx_low=800, limits=LIMITS, summarizer=fake
        )
        await agent.update_chat_ctx(history(10))
        agent.maybe_compact()
        await asyncio.sleep(0)
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert len(kinds_of(events, "context_trimmed")) == 1
        assert kinds_of(events, "compaction") == []
        assert summaries(agent.chat_ctx) == []
        fake.gate.set()
        assert agent.pending is not None
        await agent.pending.task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return agent, events

    agent, events = asyncio.run(main())
    items = agent.chat_ctx.items
    assert len(summaries(agent.chat_ctx)) == 1
    assert items[1].extra.get("voca_summary")
    assert items[2].type == "message" and items[2].role == "user"
    assert (items[2].text_content or "").startswith("факт 9 ")
    assert len(kinds_of(events, "compaction")) == 1
    assert len(kinds_of(events, "context_trimmed")) == 1


class RaisingSummarizer(FakeSummarizer):
    async def summarize(self, prompt: str) -> Summary | Failure:
        self.prompts.append(prompt)
        raise RuntimeError("boom")


def test_summarizer_exception_names_error(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, list[str]]:
        fake = RaisingSummarizer(Summary("x", 1, 1))
        agent, _, events, _ = make_agent(tmp_path, limits=LIMITS, summarizer=fake)
        await agent.update_chat_ctx(history(6))
        before = [item.id for item in agent.chat_ctx.items]
        agent.maybe_compact()
        assert agent.pending is not None
        await asyncio.wait([agent.pending.task])
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return agent, events, before

    agent, events, before = asyncio.run(main())
    assert [item.id for item in agent.chat_ctx.items] == before
    failed = kinds_of(events, "compaction_failed")
    assert len(failed) == 1
    assert failed[0]["reason"] == "error"
    assert failed[0]["error"] == "RuntimeError"
    assert agent.pending is None


class FakeClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now


async def block(
    tmp_path: Path, clock: FakeClock
) -> tuple[VocaAgent, Path, FakeSummarizer]:
    fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
    agent, _, events, _ = make_agent(
        tmp_path, limits=LIMITS, summarizer=fake, clock=clock
    )
    await agent.update_chat_ctx(history(6))
    await compact_and_accept(agent)
    assert agent.strikes == 1
    ctx = agent.chat_ctx.copy()
    add_pairs(ctx, 7, 4, 1000.0)
    await agent.update_chat_ctx(ctx)
    await compact_and_accept(agent)
    assert agent.strikes == 2
    ctx = agent.chat_ctx.copy()
    add_pairs(ctx, 11, 2, 1000.0)
    await agent.update_chat_ctx(ctx)
    agent.maybe_compact()
    agent.maybe_compact()
    return agent, events, fake


T0 = datetime(2026, 11, 15, 3, 0).timestamp()


def schedule(idle_hours: float, daily_at: str, tail_budget: int = 300) -> Limits:
    return Limits(
        threshold=500,
        tail_budget=tail_budget,
        tail_turns_min=2,
        idle_hours=idle_hours,
        daily_at=daily_at,
    )


def test_join_starts_one_compaction_and_first_turn_uses_full_context(
    tmp_path: Path,
) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 900), hold=True)
        clock = FakeClock()
        clock.now = T0 + 13 + 5 * 3600
        agent, _, events, _ = make_agent(
            tmp_path,
            limits=schedule(4, ""),
            summarizer=fake,
            clock=clock,
            compacted_at=T0,
        )
        await agent.update_chat_ctx(history(6, start=T0))
        assert agent.check_due() == "idle"
        assert agent.check_due() is None
        await asyncio.sleep(0)
        assert len(fake.prompts) == 1
        assert agent.pending is not None
        assert agent.pending.trigger == "idle"
        ids = [item.id for item in agent.chat_ctx.items]
        turn_ctx = agent.chat_ctx.copy()
        await asyncio.wait_for(
            agent.on_user_turn_completed(turn_ctx, user_message()), 1
        )
        assert [item.id for item in agent.chat_ctx.items] == ids
        assert [item.id for item in turn_ctx.items] == ids
        assert summaries(agent.chat_ctx) == []
        assert kinds_of(events, "compaction") == []
        assert len(fake.prompts) == 1
        fake.gate.set()
        await agent.pending.task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return agent, events, fake

    agent, events, fake = asyncio.run(main())
    assert len(summaries(agent.chat_ctx)) == 1
    written = kinds_of(events, "compaction")
    assert len(written) == 1
    assert written[0]["trigger"] == "idle"
    assert len(fake.prompts) == 1


def test_silent_room_then_reply_triggers_idle(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer, FakeClock]:
        fake = FakeSummarizer(Summary("тело", 40, 700))
        clock = FakeClock()
        agent, _, events, _ = make_agent(
            tmp_path,
            limits=schedule(4, ""),
            summarizer=fake,
            clock=clock,
            compacted_at=T0,
        )
        await agent.update_chat_ctx(history(6, start=T0))
        clock.now = T0 + 13 + 3 * 3600 + 59 * 60
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert agent.pending is None
        assert fake.prompts == []
        clock.now = T0 + 13 + 4 * 3600
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert agent.pending is not None
        assert agent.pending.trigger == "idle"
        await asyncio.sleep(0)
        assert len(fake.prompts) == 1
        await agent.pending.task
        clock.now += 5
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        written = kinds_of(events, "compaction")
        assert len(written) == 1
        assert written[0]["trigger"] == "idle"
        assert agent.compacted_at == clock.now
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return agent, events, fake, clock

    _, _, fake, _ = asyncio.run(main())
    assert len(fake.prompts) == 1


def test_reply_after_daily_at_triggers_daily(tmp_path: Path) -> None:
    async def main() -> Path:
        fake = FakeSummarizer(Summary("тело", 40, 700))
        clock = FakeClock()
        agent, _, events, _ = make_agent(
            tmp_path,
            limits=schedule(0, "04:00"),
            summarizer=fake,
            clock=clock,
            compacted_at=T0,
        )
        await agent.update_chat_ctx(history(6, start=T0 + 60))
        clock.now = datetime(2026, 11, 15, 3, 59).timestamp()
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert agent.pending is None
        clock.now = datetime(2026, 11, 15, 4, 1).timestamp()
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert agent.pending is not None
        assert agent.pending.trigger == "daily"
        await agent.pending.task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return events

    events = asyncio.run(main())
    written = kinds_of(events, "compaction")
    assert len(written) == 1
    assert written[0]["trigger"] == "daily"


def test_schedule_failure_keeps_trigger(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, list[str]]:
        fake = FakeSummarizer(Failure("connection", 300))
        clock = FakeClock()
        clock.now = T0 + 13 + 5 * 3600
        agent, _, events, _ = make_agent(
            tmp_path,
            limits=schedule(4, ""),
            summarizer=fake,
            clock=clock,
            compacted_at=T0,
        )
        await agent.update_chat_ctx(history(6, start=T0))
        ids = [item.id for item in agent.chat_ctx.items]
        assert agent.check_due() == "idle"
        assert agent.pending is not None
        await agent.pending.task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert [item.id for item in agent.chat_ctx.items] == ids
        return agent, events, ids

    agent, events, _ = asyncio.run(main())
    failed = kinds_of(events, "compaction_failed")
    assert len(failed) == 1
    assert failed[0]["trigger"] == "idle"
    assert failed[0]["reason"] == "connection"
    assert kinds_of(events, "compaction") == []
    assert agent.compacted_at == T0


def test_schedule_skips_small_context(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, FakeSummarizer]:
        fake = FakeSummarizer(Summary("тело", 40, 700))
        clock = FakeClock()
        clock.now = T0 + 13 + 5 * 3600
        agent, _, _, _ = make_agent(
            tmp_path,
            limits=schedule(4, "", tail_budget=100_000),
            summarizer=fake,
            clock=clock,
            compacted_at=T0,
        )
        await agent.update_chat_ctx(history(6, start=T0))
        assert agent.check_due() is None
        return agent, fake

    agent, fake = asyncio.run(main())
    assert agent.pending is None
    assert fake.prompts == []


def test_schedule_bypasses_guard(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer, FakeClock]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        clock = FakeClock()
        clock.now = T0 + 13 + 5 * 3600
        agent, _, events, _ = make_agent(
            tmp_path,
            limits=schedule(4, ""),
            summarizer=fake,
            clock=clock,
            compacted_at=T0,
        )
        await agent.update_chat_ctx(history(6, start=T0))
        agent.strikes = BLOCK_STRIKES
        agent.blocked_at = clock.now
        assert agent.check_due() == "idle"
        await asyncio.sleep(0)
        assert len(fake.prompts) == 1
        assert agent.pending is not None
        await agent.pending.task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return agent, events, fake, clock

    agent, events, _, clock = asyncio.run(main())
    written = kinds_of(events, "compaction")
    assert len(written) == 1
    assert written[0]["trigger"] == "idle"
    assert agent.strikes == BLOCK_STRIKES
    assert agent.blocked_at == clock.now
    assert kinds_of(events, "compaction_blocked") == []


def test_ineffective_compactions_block(tmp_path: Path) -> None:
    agent, events, fake = asyncio.run(block(tmp_path, FakeClock()))
    assert agent.pending is None
    assert len(fake.prompts) == 2
    assert len(kinds_of(events, "compaction")) == 2
    blocked = kinds_of(events, "compaction_blocked")
    assert len(blocked) == 1
    assert blocked[0]["strikes"] == 2
    assert blocked[0]["threshold"] == 500
    assert blocked[0]["retry_in_s"] == 300
    total = blocked[0]["estimate"]
    assert isinstance(total, int) and total >= 500


def test_effective_compaction_resets_strikes(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path]:
        fake = FakeSummarizer(Summary("## О чём говорили\nфакт 1", 50, 1200))
        limits = Limits(threshold=1000, tail_budget=300, tail_turns_min=2)
        agent, _, events, _ = make_agent(tmp_path, limits=limits, summarizer=fake)
        await agent.update_chat_ctx(history(10))
        agent.strikes = 1
        await compact_and_accept(agent)
        return agent, events

    agent, events = asyncio.run(main())
    written = kinds_of(events, "compaction")
    assert len(written) == 1
    after = written[0]["after"]
    assert isinstance(after, int) and after < 1000
    assert agent.strikes == 0
    assert kinds_of(events, "compaction_blocked") == []


def test_probe_after_retry(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path]:
        clock = FakeClock()
        agent, events, fake = await block(tmp_path, clock)
        assert len(kinds_of(events, "compaction_blocked")) == 1
        clock.now = 299.0
        agent.maybe_compact()
        assert agent.pending is None
        assert len(fake.prompts) == 2
        clock.now = 300.0
        agent.maybe_compact()
        await asyncio.sleep(0)
        assert agent.pending is not None
        assert agent.strikes == 1
        assert len(fake.prompts) == 3
        await agent.pending.task
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        assert agent.strikes == 2
        clock.now = 301.0
        agent.maybe_compact()
        assert agent.pending is None
        return agent, events

    _, events = asyncio.run(main())
    assert len(kinds_of(events, "compaction_blocked")) == 2


def test_hard_limit_trims_while_blocked(tmp_path: Path) -> None:
    async def main() -> tuple[VocaAgent, Path, FakeSummarizer]:
        fake = FakeSummarizer(Summary("x", 1, 1))
        agent, _, events, _ = make_agent(
            tmp_path,
            ctx_high=1500,
            ctx_low=800,
            limits=LIMITS,
            summarizer=fake,
            clock=FakeClock(),
        )
        await agent.update_chat_ctx(history(10))
        agent.strikes = 2
        agent.maybe_compact()
        assert agent.pending is None
        assert fake.prompts == []
        assert len(kinds_of(events, "compaction_blocked")) == 1
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), user_message())
        return agent, events, fake

    _, events, _ = asyncio.run(main())
    trimmed = kinds_of(events, "context_trimmed")
    assert len(trimmed) == 1
    after = trimmed[0]["after"]
    assert isinstance(after, int) and after <= 800
    assert kinds_of(events, "compaction") == []


NOISE = Sorted("noise", 0.9, 40)
REPLY = Sorted("reply", 0.9, 40)


def final(text: str) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[stt.SpeechData(language="ru", text=text)],
    )


async def screened_source(
    events: list[stt.SpeechEvent | str],
) -> AsyncIterator[stt.SpeechEvent | str]:
    for event in events:
        yield event


async def screened(
    agent: VocaAgent, events: list[stt.SpeechEvent | str], speaking: bool
) -> list[stt.SpeechEvent | str]:
    return [
        event
        async for event in agent._screen(screened_source(events), lambda: speaking)
    ]


def texts(events: list[stt.SpeechEvent | str]) -> list[str]:
    return [
        event if isinstance(event, str) else event.alternatives[0].text
        for event in events
    ]


def sorter_agent(tmp_path: Path, sorter: FakeSorter) -> tuple[VocaAgent, Path]:
    agent, _, events, _ = make_agent(
        tmp_path, sorter=sorter, sorter_prompt=SORTER_PROMPT
    )
    return agent, events


class FakeSpeech:
    def __init__(self, *, done: bool = False, interrupted: bool = False) -> None:
        self.finished = done
        self.interrupted = interrupted

    def done(self) -> bool:
        return self.finished


class FakeSession:
    def __init__(self, speech: FakeSpeech | None) -> None:
        self.current_speech = speech


def is_talking(speech: FakeSpeech | None, voiced: FakeSpeech | None) -> bool:
    return talking(cast(Any, FakeSession(speech)), cast(Any, voiced))


def test_talking_by_current_speech() -> None:
    speech = FakeSpeech()
    assert not is_talking(None, None)
    assert is_talking(speech, speech)
    assert not is_talking(speech, None)
    assert not is_talking(speech, FakeSpeech())
    done = FakeSpeech(done=True)
    assert not is_talking(done, done)
    cut = FakeSpeech(interrupted=True)
    assert not is_talking(cut, cut)


def test_agent_state_marks_voiced_speech(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _ = sorter_agent(tmp_path, FakeSorter())
    session = FakeSession(FakeSpeech())
    monkeypatch.setattr(VocaAgent, "session", property(lambda _: session))
    agent.on_agent_state(
        AgentStateChangedEvent(old_state="listening", new_state="thinking")
    )
    assert agent.voiced is None
    agent.on_agent_state(
        AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    assert agent.voiced is session.current_speech


def test_stt_drops_noise_while_speaking(tmp_path: Path) -> None:
    noise = ["Угу", "Yeah, yeah", "Okay."]
    sorter = FakeSorter({text: NOISE for text in noise})
    agent, events = sorter_agent(tmp_path, sorter)
    out = asyncio.run(screened(agent, [final(text) for text in noise], True))
    assert out == []
    sorted_rows = kinds_of(events, "sorter")
    assert [row["text"] for row in sorted_rows] == noise
    assert all(row["branch"] == "stt" and row["dropped"] for row in sorted_rows)
    assert all(row["choice"] == "noise" and row["speaking"] for row in sorted_rows)
    assert agent.passed == []


def test_stt_keeps_anchored_while_speaking(tmp_path: Path) -> None:
    agent, events = sorter_agent(tmp_path, FakeSorter({"Нет.": NOISE}))
    out = asyncio.run(screened(agent, [final("Нет.")], True))
    assert texts(out) == ["Нет."]
    assert [passed[0] for passed in agent.passed] == ["Нет."]
    assert kinds_of(events, "sorter")[0]["dropped"] is False


def test_stt_silence_passes_without_call(tmp_path: Path) -> None:
    sorter = FakeSorter({"Угу": NOISE})
    agent, events = sorter_agent(tmp_path, sorter)
    interim = stt.SpeechEvent(
        type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
        alternatives=[stt.SpeechData(language="ru", text="Уг")],
    )
    out = asyncio.run(screened(agent, [final("Угу")], False))
    assert texts(out) == ["Угу"]
    out = asyncio.run(screened(agent, [interim, "текст", final(" ")], True))
    assert len(out) == 3
    assert sorter.seen == []
    assert not events.exists() or kinds_of(events, "sorter") == []


@pytest.mark.parametrize("failure", ["connection", "timeout"])
def test_stt_failure_length_threshold(tmp_path: Path, failure: str) -> None:
    agent, events = sorter_agent(tmp_path, FakeSorter(failure=failure))
    out = asyncio.run(
        screened(agent, [final("Угу"), final("Расскажи подробнее")], True)
    )
    assert texts(out) == ["Расскажи подробнее"]
    sorted_rows = kinds_of(events, "sorter")
    assert [row["fallback"] for row in sorted_rows] == [failure, failure]
    assert [row["dropped"] for row in sorted_rows] == [True, False]
    assert all("choice" not in row for row in sorted_rows)


def test_hook_drops_noise_in_silence(tmp_path: Path) -> None:
    agent, events = sorter_agent(tmp_path, FakeSorter({"Угу": NOISE}))
    message = llm.ChatMessage(role="user", content=["Угу"])
    with pytest.raises(StopResponse):
        asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), message))
    row = kinds_of(events, "sorter")[0]
    assert (row["branch"], row["dropped"], row["speaking"]) == ("hook", True, False)


def test_hook_failure_in_silence_passes(tmp_path: Path) -> None:
    agent, events = sorter_agent(tmp_path, FakeSorter())
    message = llm.ChatMessage(role="user", content=["Угу"])
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), message))
    row = kinds_of(events, "sorter")[0]
    assert (row["branch"], row["dropped"], row["fallback"]) == (
        "hook",
        False,
        "connection",
    )


def test_one_call_per_reply(tmp_path: Path) -> None:
    script = {
        text: REPLY
        for text in ("Расскажи подробнее", "Расскажи", "подробнее", "Что нового")
    }
    sorter = FakeSorter(script)
    agent, _ = sorter_agent(tmp_path, sorter)

    async def turn(text: str) -> None:
        message = llm.ChatMessage(role="user", content=[text])
        await agent.on_user_turn_completed(llm.ChatContext.empty(), message)

    async def main() -> None:
        await screened(agent, [final("Расскажи подробнее")], True)
        await turn("Расскажи подробнее")
        assert len(sorter.seen) == 1
        await screened(agent, [final("Расскажи"), final("подробнее")], True)
        await turn("Расскажи подробнее")
        assert len(sorter.seen) == 3
        assert agent.passed == []
        await turn("Что нового")
        assert len(sorter.seen) == 4
        assert sorter.seen[-1]["фраза пользователя"] == "Что нового"

    asyncio.run(main())


def test_normal_reply_keeps_turn_ctx(tmp_path: Path) -> None:
    agent, events = sorter_agent(tmp_path, FakeSorter({"Привет": REPLY}))
    turn_ctx = llm.ChatContext.empty()
    turn_ctx.add_message(role="user", content="Раньше")
    items = turn_ctx.items
    ids = [item.id for item in items]
    message = user_message()
    asyncio.run(agent.on_user_turn_completed(turn_ctx, message))
    assert turn_ctx.items is items
    assert [item.id for item in turn_ctx.items] == ids
    assert message.content == ["Привет"]
    assert kinds_of(events, "sorter")[0]["choice"] == "reply"


def test_sorter_state_reads_last_agent_phrase(tmp_path: Path) -> None:
    sorter = FakeSorter({"Угу": NOISE})
    agent, _ = sorter_agent(tmp_path, sorter)
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="assistant", content="Первое.")
    ctx.add_message(role="user", content="Ещё")
    ctx.add_message(role="assistant", content="Второе.")

    async def main() -> None:
        await agent.update_chat_ctx(ctx)
        await screened(agent, [final("Угу")], True)

    asyncio.run(main())
    assert sorter.seen == [
        {"последняя фраза агента": "Второе.", "фраза пользователя": "Угу"}
    ]


def test_sorter_two_options_without_requests(tmp_path: Path) -> None:
    sorter = FakeSorter({"Угу": NOISE, "Привет": REPLY})
    agent, _ = sorter_agent(tmp_path, sorter)

    async def main() -> None:
        await screened(agent, [final("Угу")], True)
        message = llm.ChatMessage(role="user", content=["Привет"])
        await agent.on_user_turn_completed(llm.ChatContext.empty(), message)

    asyncio.run(main())
    assert sorter.counts == [0, 0]


def test_sorter_none_is_passthrough(tmp_path: Path) -> None:
    agent, _, events, _ = make_agent(tmp_path)
    out = asyncio.run(screened(agent, [final("Угу"), "x"], True))
    assert texts(out) == ["Угу", "x"]
    message = llm.ChatMessage(role="user", content=["Угу"])
    asyncio.run(agent.on_user_turn_completed(llm.ChatContext.empty(), message))
    assert not events.exists() or kinds_of(events, "sorter") == []


def fragment_ctx() -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="assistant", content="Здравствуйте.")
    ctx.add_message(
        role="user",
        content="Попроси Гермеса создать папку",
        id="fragment",
        metrics={"started_speaking_at": 98.0, "stopped_speaking_at": 100.0},
    )
    return ctx


def glue_turn(
    tmp_path: Path, ctx: llm.ChatContext
) -> tuple[VocaAgent, llm.ChatContext, llm.ChatMessage, Path]:
    agent, _, events, _ = make_agent(tmp_path)
    message = llm.ChatMessage(
        role="user",
        content=["отчёты, и в ней файл за ноябрь"],
        metrics={"started_speaking_at": 101.5, "stopped_speaking_at": 103.0},
    )

    async def main() -> llm.ChatContext:
        await agent.update_chat_ctx(ctx)
        turn_ctx = agent.chat_ctx.copy()
        await agent.on_user_turn_completed(turn_ctx, message)
        return turn_ctx

    return agent, asyncio.run(main()), message, events


def test_hook_glues_fragment(tmp_path: Path) -> None:
    ctx = fragment_ctx()
    ctx.add_message(role="assistant", content="Сейчас", id="cut", interrupted=True)
    agent, turn_ctx, message, events = glue_turn(tmp_path, ctx)
    assert (
        message.text_content
        == "Попроси Гермеса создать папку отчёты, и в ней файл за ноябрь"
    )
    for items in (agent.chat_ctx.items, turn_ctx.items):
        ids = [item.id for item in items]
        assert "fragment" not in ids and "cut" not in ids
        assert len(ids) == 1
    row = kinds_of(events, "glue")[0]
    assert row["removed"] == 2
    assert row["fragment"] == "Попроси Гермеса создать папку"
    assert row["gap_s"] == 1.5


def test_hook_glue_drops_not_sent_delegate(tmp_path: Path) -> None:
    ctx = fragment_ctx()
    ctx.items.append(
        llm.FunctionCall(id="call", call_id="c1", name="delegate", arguments="{}")
    )
    ctx.items.append(
        llm.FunctionCallOutput(
            id="out",
            call_id="c1",
            name="delegate",
            output=json.dumps({"id": "c1", "event": "not_sent"}),
            is_error=False,
        )
    )
    agent, turn_ctx, message, events = glue_turn(tmp_path, ctx)
    ids = [item.id for item in agent.chat_ctx.items]
    assert "call" not in ids and "out" not in ids and "fragment" not in ids
    assert [item.id for item in turn_ctx.items] == ids
    assert kinds_of(events, "glue")[0]["removed"] == 3
    assert message.text_content is not None
    assert message.text_content.startswith("Попроси Гермеса создать папку ")


def test_hook_no_glue_after_heard_answer(tmp_path: Path) -> None:
    ctx = fragment_ctx()
    ctx.add_message(role="assistant", content="Хорошо.")
    ids = [item.id for item in ctx.items]
    agent, turn_ctx, message, events = glue_turn(tmp_path, ctx)
    assert [item.id for item in agent.chat_ctx.items] == ids
    assert [item.id for item in turn_ctx.items] == ids
    assert message.text_content == "отчёты, и в ней файл за ноябрь"
    assert not events.exists() or kinds_of(events, "glue") == []


def continuation(text: str) -> llm.ChatMessage:
    return llm.ChatMessage(
        role="user", content=[text], metrics={"started_speaking_at": 101.5}
    )


def cut_ctx() -> llm.ChatContext:
    ctx = fragment_ctx()
    ctx.add_message(role="assistant", content="Сейчас", id="cut", interrupted=True)
    return ctx


def test_hook_glues_short_noise_continuation(tmp_path: Path) -> None:
    sorter = FakeSorter({"отчёты": NOISE})
    agent, events = sorter_agent(tmp_path, sorter)
    message = continuation("отчёты")

    async def main() -> None:
        await agent.update_chat_ctx(cut_ctx())
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), message)

    asyncio.run(main())
    assert message.text_content == "Попроси Гермеса создать папку отчёты"
    assert kinds_of(events, "glue")[0]["removed"] == 2
    assert sorter.seen == []


def test_stt_passes_continuation_before_first_sound(tmp_path: Path) -> None:
    sorter = FakeSorter({"отчёты": NOISE, "Угу": NOISE})
    agent, events = sorter_agent(tmp_path, sorter)
    speech = FakeSpeech()
    session = cast(Any, FakeSession(speech))
    message = continuation("отчёты")

    async def main() -> list[stt.SpeechEvent | str]:
        await agent.update_chat_ctx(cut_ctx())
        out = [
            event
            async for event in agent._screen(
                screened_source([final("отчёты")]),
                lambda: talking(session, agent.voiced),
            )
        ]
        await agent.on_user_turn_completed(agent.chat_ctx.copy(), message)
        return out

    out = asyncio.run(main())
    assert texts(out) == ["отчёты"]
    assert message.text_content == "Попроси Гермеса создать папку отчёты"
    assert len(kinds_of(events, "glue")) == 1
    assert sorter.seen == []
    agent.voiced = cast(Any, speech)

    async def voiced() -> list[stt.SpeechEvent | str]:
        return [
            event
            async for event in agent._screen(
                screened_source([final("Угу")]),
                lambda: talking(session, agent.voiced),
            )
        ]

    assert asyncio.run(voiced()) == []
    assert kinds_of(events, "sorter")[-1]["dropped"] is True
