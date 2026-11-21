import json

from voca.conversation.glue import Entry, Glued, glue

FRAGMENT = Entry(
    "u1", "message", role="user", text="Попроси Гермеса создать папку", stopped_at=100.0
)
CUT = Entry("a1", "message", role="assistant", text="Сейчас", interrupted=True)


def not_sent(call_id: str) -> list[Entry]:
    output = json.dumps({"id": "t1", "event": "not_sent"})
    return [
        Entry(call_id, "function_call", call_id=call_id, name="delegate"),
        Entry(
            f"{call_id}_out",
            "function_call_output",
            call_id=call_id,
            name="delegate",
            output=output,
        ),
    ]


def test_fragment_without_answer_glued() -> None:
    assert glue([FRAGMENT], started_at=101.5, window_s=3.0) == Glued(
        frozenset({"u1"}), "Попроси Гермеса создать папку", 1.5
    )


def test_interrupted_before_first_sentence_glued() -> None:
    glued = glue([FRAGMENT, CUT], started_at=101.5, window_s=3.0)
    assert glued is not None
    assert glued.drop == frozenset({"u1", "a1"})
    assert glued.fragment == "Попроси Гермеса создать папку"


def test_heard_first_sentence_not_glued() -> None:
    heard = Entry(
        "a1", "message", role="assistant", text="Сейчас. Попрошу", interrupted=True
    )
    assert glue([FRAGMENT, heard], started_at=101.5, window_s=3.0) is None


def test_uninterrupted_answer_not_glued() -> None:
    answer = Entry("a1", "message", role="assistant", text="Сейчас")
    assert glue([FRAGMENT, answer], started_at=101.5, window_s=3.0) is None


def test_not_sent_delegate_pair_dropped() -> None:
    glued = glue([FRAGMENT, *not_sent("c1"), CUT], started_at=101.5, window_s=3.0)
    assert glued is not None
    assert glued.drop == frozenset({"u1", "c1", "c1_out", "a1"})


def test_other_tool_pair_blocks() -> None:
    sent = [
        Entry("c1", "function_call", call_id="c1", name="delegate"),
        Entry(
            "c1_out",
            "function_call_output",
            call_id="c1",
            name="delegate",
            output=json.dumps({"id": "t1", "event": "sent"}),
        ),
    ]
    assert glue([FRAGMENT, *sent], started_at=101.5, window_s=3.0) is None
    other = [
        Entry("c2", "function_call", call_id="c2", name="list_tasks"),
        Entry(
            "c2_out",
            "function_call_output",
            call_id="c2",
            name="list_tasks",
            output="[]",
        ),
    ]
    assert glue([FRAGMENT, *other], started_at=101.5, window_s=3.0) is None
    system = Entry("s1", "message", role="system", text="Факт")
    assert glue([FRAGMENT, system], started_at=101.5, window_s=3.0) is None


def test_code_facts_ignored() -> None:
    facts = [
        Entry("f1", "function_call", call_id="voca_1", name="list_tasks"),
        Entry("f1_out", "function_call_output", call_id="voca_1", name="list_tasks"),
        Entry("f2", "function_call", call_id="delivery_1", name="task_result"),
        Entry(
            "f2_out", "function_call_output", call_id="delivery_1", name="task_result"
        ),
    ]
    glued = glue([FRAGMENT, *facts, CUT], started_at=101.5, window_s=3.0)
    assert glued is not None
    assert glued.drop == frozenset({"u1", "a1"})


def test_outside_window_not_glued() -> None:
    assert glue([FRAGMENT, CUT], started_at=103.5, window_s=3.0) is None
    assert glue([FRAGMENT, CUT], started_at=103.0, window_s=3.0) is not None


def test_missing_times_not_glued() -> None:
    assert glue([FRAGMENT, CUT], started_at=None, window_s=3.0) is None
    bare = Entry("u1", "message", role="user", text="Попроси Гермеса создать папку")
    assert glue([bare, CUT], started_at=101.5, window_s=3.0) is None


def test_no_user_message() -> None:
    assert glue([CUT], started_at=101.5, window_s=3.0) is None
    empty = Entry("u2", "message", role="user", text="", stopped_at=100.0)
    assert glue([empty, CUT], started_at=101.5, window_s=3.0) is None
