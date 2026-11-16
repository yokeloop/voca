from voca.conversation.agent import SPEECH_LIMIT, clip, delivery_text
from voca.delegation.model import Task, TaskStatus


def done(result: str | None) -> Task:
    return Task("k", "t", TaskStatus.SUCCEEDED, created_at=0.0, result=result)


def failed(error: str) -> Task:
    return Task("k", "t", TaskStatus.FAILED, created_at=0.0, error=error)


def sentence(letter: str, length: int) -> str:
    return letter * (length - 1) + "."


def test_delivery_text() -> None:
    first, second, third = (sentence(letter, 120) for letter in "абв")
    text = delivery_text(done(f"{first} {second} {third}"))
    assert text == f"Готово по поручению: {first} {second}"
    clipped = clip(f"{first} {second} {third}")
    assert len(clipped) <= SPEECH_LIMIT
    assert clipped.endswith(".")
    long = " ".join(["слово"] * 70)
    cut = clip(long)
    assert len(cut) <= SPEECH_LIMIT
    assert long.startswith(cut)
    assert not cut.endswith(" ")
    assert len(cut) > SPEECH_LIMIT - 10
    assert clip("раз\nдва\n\n  три") == "раз два три"
    assert (
        delivery_text(done("строка один\nстрока два"))
        == "Готово по поручению: строка один строка два"
    )
    assert (
        delivery_text(failed("timeout"))
        == "Поручение не выполнено: не дождался результата."
    )
    assert (
        delivery_text(failed("interrupted by gateway restart"))
        == "Поручение не выполнено: Hermes перезапускался."
    )
    for error in ("lost", "lost before launch", "unavailable", "HTTP 500 boom"):
        spoken = delivery_text(failed(error))
        assert spoken == "Поручение не выполнено: Hermes вернул ошибку."
        assert "boom" not in spoken
    assert delivery_text(done(None)) == "Готово по поручению."
    assert delivery_text(done("")) == "Готово по поручению."
