import json
from pathlib import Path
from typing import Any

import pytest

from voca.conversation.approval_judge import (
    Place,
    Request,
    Rules,
    essence_spoken,
    judge,
    mentions,
    objects,
)
from voca.conversation.descriptions import DESCRIPTIONS
from voca.conversation.verdict import DecisionFailure, Sorted
from voca.delegation.danger import Danger, level

CORPUS = Path(__file__).resolve().parents[2] / "bench" / "corpus" / "decision.json"
RULES = Rules()
DIR = Request(
    "1", Danger.HIGH, True, "удаление в корневом каталоге", "rm -r /tmp/voca-48-a"
)
PKG = Request("2", Danger.MEDIUM, True, "удаление пакета", "apt remove ffmpeg")


def answer(
    kind: Any = "reply",
    relation: Any = "answers",
    reply: Any = "approve",
    confidence: float = 1.0,
    target: int | None = None,
    target_confidence: float = 1.0,
) -> Sorted:
    return Sorted(
        kind,
        1.0,
        10,
        relation=relation,
        relation_confidence=1.0,
        answer=reply,
        answer_confidence=confidence,
        target=target,
        target_confidence=target_confidence,
    )


def unread(request: Request) -> Request:
    return Request(request.id, request.danger, False, request.request, request.command)


def test_mentions_stems() -> None:
    for text in ("Да, удаляй", "Пусть удаляет каталог", "удали voca-48-a"):
        assert mentions(text, DIR), text
    for text in ("Да", "Давай", "Разрешаю", "Через минуту"):
        assert not mentions(text, DIR), text
    assert mentions("Пакет удаляй", PKG)
    assert mentions("ffmpeg ставь", PKG)


def test_objects() -> None:
    assert objects("rm -r /tmp/voca-48-a") == {"voca-48-a"}
    assert objects("apt remove voca-48-none -y") == {"voca-48-none"}


def test_essence_spoken() -> None:
    assert essence_spoken("Он хочет удалить каталог voca-48-a. Разрешить?", False)
    assert essence_spoken("Он хочет удалить каталог.", True)
    assert not essence_spoken("Он хочет удал", True)
    assert not essence_spoken("", False)
    assert not essence_spoken("  ", True)


def test_yes_before_essence_passes() -> None:
    verdict = judge("Да, давай", [unread(DIR)], answer(), None, RULES, 0)
    assert verdict.outcome == "pass"
    assert verdict.ids == ()


def test_named_action_after_essence_approves() -> None:
    verdict = judge(
        "Да, удаляй", [DIR], answer(confidence=0.97), Place(("1",), "essence"), RULES, 0
    )
    assert verdict.outcome == "approve"
    assert verdict.binding == "content"
    assert verdict.ids == ("1",)
    assert verdict.confidence == pytest.approx(0.97)
    assert verdict.threshold == 0.50


def test_question_about_request() -> None:
    place = Place(("1",), "essence")
    asked = judge(
        "А что именно там лежит?", [DIR], answer(relation="asks"), place, RULES, 0
    )
    assert (asked.outcome, asked.ids) == ("asks", ("1",))
    denied = judge(
        "Не надо, отклони",
        [DIR],
        answer(reply="deny", confidence=0.7),
        Place(("1",), "asks"),
        RULES,
        0,
    )
    assert (denied.outcome, denied.ids, denied.binding) == ("deny", ("1",), "place")
    assert denied.threshold == 0.45


def test_high_short_yes_confirms() -> None:
    first = judge(
        "Да", [DIR], answer(confidence=0.96), Place(("1",), "essence"), RULES, 0
    )
    assert (first.outcome, first.binding) == ("confirm", "place")
    second = judge(
        "Да", [DIR], answer(confidence=0.96), Place(("1",), "confirm"), RULES, 1
    )
    assert (second.outcome, second.ids) == ("approve", ("1",))


def test_two_requests_unclear() -> None:
    both = [DIR, PKG]
    place = Place(("1", "2"), "essence")
    unclear = judge("Да, разрешаю", both, answer(), place, RULES, 0)
    assert (unclear.outcome, unclear.ids) == ("clarify_which", ("1", "2"))
    mixed = judge(
        "Пакет удаляй, а каталог не трогай", both, answer(target=2), place, RULES, 1
    )
    assert (mixed.outcome, mixed.ids) == ("clarify_which", ("1", "2"))
    left = judge("Да, разрешаю", both, answer(), place, RULES, 2)
    assert (left.outcome, left.ids) == ("left", ("1", "2"))


def test_medium_short_yes_approves() -> None:
    verdict = judge(
        "Да", [PKG], answer(confidence=0.9), Place(("2",), "essence"), RULES, 0
    )
    assert (verdict.outcome, verdict.binding) == ("approve", "place")


def test_named_action_before_reading_approves() -> None:
    verdict = judge(
        "Не надо читать, пусть удаляет каталог",
        [unread(DIR)],
        answer(confidence=0.96),
        None,
        RULES,
        0,
    )
    assert (verdict.outcome, verdict.binding) == ("approve", "content")


def test_short_yes_after_other_phrase_passes() -> None:
    verdict = judge("Да", [DIR], answer(), None, RULES, 0)
    assert (verdict.outcome, verdict.ids) == ("pass", ())


def test_other_reply_with_place_passes_with_ids() -> None:
    verdict = judge(
        "Подожди, а сколько сейчас времени?",
        [DIR],
        answer(relation="other"),
        Place(("1",), "essence"),
        RULES,
        0,
    )
    assert (verdict.outcome, verdict.ids) == ("pass", ("1",))


@pytest.mark.parametrize(
    ("danger", "threshold"),
    [(Danger.LOW, 0.45), (Danger.MEDIUM, 0.50), (Danger.HIGH, 0.50)],
)
def test_thresholds_by_danger(danger: Danger, threshold: float) -> None:
    request = Request("1", danger, True, "удаление в корневом каталоге", None)
    below = judge(
        "Да, удаляй", [request], answer(confidence=threshold - 0.01), None, RULES, 0
    )
    assert (below.outcome, below.threshold) == ("clarify_sure", threshold)
    at = judge("Да, удаляй", [request], answer(confidence=threshold), None, RULES, 0)
    assert (at.outcome, at.threshold) == ("approve", threshold)


def test_deny_threshold() -> None:
    low = judge("Нет", [DIR], answer(reply="deny", confidence=0.44), None, RULES, 0)
    assert (low.outcome, low.ids) == ("clarify_sure", ("1",))
    enough = judge("Нет", [DIR], answer(reply="deny", confidence=0.45), None, RULES, 0)
    assert (enough.outcome, enough.binding) == ("deny", "none")


def test_target_multiplies() -> None:
    both = [DIR, PKG]
    place = Place(("1", "2"), "essence")
    verdict = judge(
        "Да", both, answer(target=2, target_confidence=0.6), place, RULES, 0
    )
    assert (verdict.outcome, verdict.ids) == ("approve", ("2",))
    assert verdict.confidence == pytest.approx(0.6)
    weak = judge("Да", both, answer(target=2, target_confidence=0.4), place, RULES, 0)
    assert (weak.outcome, weak.ids) == ("clarify_sure", ("2",))
    named = judge(
        "Пакет убирай", both, answer(target=2, target_confidence=0.6), None, RULES, 0
    )
    assert (named.outcome, named.binding, named.ids) == ("approve", "content", ("2",))
    assert named.confidence == pytest.approx(0.6)


def test_target_disagrees_with_content() -> None:
    both = [DIR, PKG]
    verdict = judge("Пакет убирай", both, answer(target=1), None, RULES, 0)
    assert (verdict.outcome, verdict.ids) == ("clarify_which", ("1", "2"))
    unsure = judge("Пакет убирай", both, answer(target=None), None, RULES, 0)
    assert (unsure.outcome, unsure.ids, unsure.confidence) == ("approve", ("2",), 1.0)


def test_failure_never_approves() -> None:
    for sorted_ in (DecisionFailure("timeout", 300), None, Sorted("reply", 1.0, 5)):
        verdict = judge("Да, удаляй", [DIR, PKG], sorted_, None, RULES, 0)
        assert (verdict.outcome, verdict.ids) == ("unavailable", ("1", "2"))


def test_noise_kind_passes() -> None:
    verdict = judge(
        "Да-да", [DIR], answer(kind="noise"), Place(("1",), "essence"), RULES, 0
    )
    assert (verdict.outcome, verdict.ids) == ("pass", ("1",))


def test_deny_without_binding_with_two_requests_passes() -> None:
    verdict = judge("Нет", [DIR, PKG], answer(reply="deny", target=1), None, RULES, 0)
    assert (verdict.outcome, verdict.ids) == ("pass", ())


def corpus_cases(expect: list[str]) -> list[tuple[str, Request]]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    requests = {item["id"]: item for item in data["requests"]}
    english = {text: name for name, text in DESCRIPTIONS.items()}
    cases = []
    for case in data["cases"]:
        if len(case["requests"]) != 1 or case["expect"] != expect:
            continue
        item = requests[case["requests"][0]]
        danger = level(english[item["description"]])
        request = Request("1", danger, True, item["description"], item["command"])
        cases.append((case["phrase"], request))
    assert cases
    return cases


def test_corpus_phrases() -> None:
    for phrase, request in corpus_cases(["approve"]):
        named = mentions(phrase, request)
        read = judge(phrase, [request], answer(), Place(("1",), "essence"), RULES, 0)
        assert read.outcome in ("approve", "confirm"), phrase
        if read.outcome == "confirm":
            assert request.danger == Danger.HIGH and not named, phrase
        before = judge(phrase, [unread(request)], answer(), None, RULES, 0)
        assert before.outcome == ("approve" if named else "pass"), phrase
    for phrase, request in corpus_cases(["deny"]):
        verdict = judge(phrase, [request], answer(reply="deny"), None, RULES, 0)
        assert verdict.outcome == "deny", phrase
