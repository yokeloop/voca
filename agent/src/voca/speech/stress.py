import os
import re
from collections.abc import Callable
from pathlib import Path

MODELS_DIR = Path(os.environ.get("VOCA_MODELS_DIR", Path.home() / "models"))
ACCENT_DIR = MODELS_DIR / "ruaccent" / "accentuator-b78ae5e"
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
ACUTE = "́"

Stress = Callable[[str], str]


def vowels(word: str) -> int:
    return sum(1 for char in word if char in VOWELS)


def to_tts(marked: str) -> str:
    text = re.sub(r"\+(\w)", lambda m: m.group(1) + ACUTE, marked)

    def word(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if vowels(token) > 1 else token.replace(ACUTE, "")

    return re.sub(r"[\ẃ-]+", word, text)


def place(stress: Stress, text: str) -> str:
    return to_tts(stress(text))


def load_accent(omograph: str = "turbo3.1", workdir: Path = ACCENT_DIR) -> Stress:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import ruaccent

    if not (Path(ruaccent.__file__).parent / "koziev").exists():
        raise RuntimeError("нет ruaccent/koziev: scripts/ruaccent-setup.sh")
    accent = ruaccent.RUAccent()
    accent.load(omograph_model_size=omograph, use_dictionary=True, workdir=str(workdir))
    return lambda text: str(accent.process_all(text))
