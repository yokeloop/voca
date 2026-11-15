import asyncio
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from livekit.agents import llm

from voca.clientio.cards import CardState
from voca.conversation.agent import VocaAgent, records, refusal
from voca.conversation.compaction import Limits, estimate
from voca.conversation.instructions import InstructionLoader
from voca.conversation.summarizer import Failure, Summarizer, Summary
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"


def make_agent(
    tmp_path: Path,
    *,
    ctx_high: int = 13926,
    ctx_low: int = 6553,
    chat_ctx: llm.ChatContext | None = None,
    limits: Limits | None = None,
    summarizer: Summarizer | None = None,
    clock: Callable[[], float] = time.monotonic,
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
