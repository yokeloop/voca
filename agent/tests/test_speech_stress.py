from voca.speech.stress import place, to_tts


def test_plus_becomes_acute() -> None:
    assert to_tts("з+амок") == "за́мок"


def test_single_vowel_word_loses_stress() -> None:
    assert to_tts("д+ом и з+амок") == "дом и за́мок"


def test_place_uses_given_stress() -> None:
    def stress(text: str) -> str:
        return text.replace("замок", "з+амок")

    assert place(stress, "старый замок") == "старый за́мок"
