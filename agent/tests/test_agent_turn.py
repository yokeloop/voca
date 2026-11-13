import asyncio
import json
import shutil
from pathlib import Path

from livekit.agents import llm

from voca.clientio.cards import CardState
from voca.conversation.agent import VocaAgent, refusal
from voca.conversation.instructions import InstructionLoader
from voca.telemetry.jsonl import JsonlLog

EXAMPLE = Path(__file__).resolve().parents[2] / "instructions.example"


def make_agent(
    tmp_path: Path, *, ctx_high: int = 9830, ctx_low: int = 6553
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
