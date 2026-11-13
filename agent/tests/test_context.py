from voca.conversation.context import Item, approx_tokens, trim_chat_ctx


def message(id: str, role: str, tokens: int) -> Item:
    return Item(id=id, kind="message", role=role, call_id=None, tokens=tokens)


def call(id: str, kind: str, call_id: str, tokens: int) -> Item:
    return Item(id=id, kind=kind, role=None, call_id=call_id, tokens=tokens)


def turns(count: int, tokens: int) -> list[Item]:
    items: list[Item] = []
    for n in range(1, count + 1):
        items.append(message(f"u{n}", "user", tokens // 2))
        items.append(message(f"a{n}", "assistant", tokens - tokens // 2))
    return items


def test_below_high_returns_none() -> None:
    items = [message("s", "system", 10), *turns(3, 100)]
    assert trim_chat_ctx(items, high=310, low=100) is None


def test_trims_oldest_turns_to_low() -> None:
    items = [message("s", "system", 10), *turns(6, 100)]
    trim = trim_chat_ctx(items, high=500, low=300)
    assert trim is not None
    assert trim.drop == frozenset({"u1", "a1", "u2", "a2", "u3", "a3", "u4", "a4"})
    assert trim.estimate_before == 610
    assert trim.estimate_after <= 300
    assert "s" not in trim.drop


def test_keeps_function_call_pairs() -> None:
    items = [
        message("s", "system", 10),
        message("u1", "user", 50),
        call("fc1", "function_call", "c1", 50),
        message("u2", "user", 50),
        call("fo1", "function_call_output", "c1", 50),
        message("a1", "assistant", 50),
        message("ux", "user", 50),
        message("ax", "assistant", 50),
        message("u3", "user", 50),
        message("a3", "assistant", 50),
    ]
    trim = trim_chat_ctx(items, high=300, low=150)
    assert trim is not None
    assert ("fc1" in trim.drop) == ("fo1" in trim.drop)
    assert {"u1", "fc1", "u2", "fo1", "a1"} <= trim.drop
    assert not {"u3", "a3"} & trim.drop


def test_keeps_newest_turn() -> None:
    items = [message("s", "system", 10), message("u1", "user", 5000)]
    assert trim_chat_ctx(items, high=500, low=300) is None


def test_instructions_alone_cannot_reach_low() -> None:
    items = [message("s", "system", 400), *turns(2, 50)]
    trim = trim_chat_ctx(items, high=300, low=100)
    assert trim is not None
    assert trim.drop == frozenset({"u1", "a1"})
    assert trim.estimate_after > 100


def test_keeps_config_items() -> None:
    config = Item(
        id="cfg", kind="agent_config_update", role=None, call_id=None, tokens=0
    )
    items = [message("s", "system", 10), *turns(2, 100), config, *turns(6, 100)[4:]]
    trim = trim_chat_ctx(items, high=300, low=150)
    assert trim is not None
    assert "cfg" not in trim.drop
    assert "u1" in trim.drop


def test_approx_tokens() -> None:
    assert approx_tokens("абвгде") == 2
