import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

BENCH = Path(__file__).resolve().parents[2] / "bench"


def load() -> ModuleType:
    sys.path.insert(0, str(BENCH))
    path = BENCH / "tts_ru.py"
    spec = importlib.util.spec_from_file_location("tts_ru", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tts_ru"] = module
    spec.loader.exec_module(module)
    return module


bench = load()


def test_asr_score_ignores_stress_marks() -> None:
    assert bench.asr_score("По́езд пришёл.", "поезд пришел") == 1.0


def test_asr_score_of_different_phrases_is_low() -> None:
    assert bench.asr_score("По́езд пришёл.", "завтра будет дождь") < 0.5


def test_global_options_reach_render() -> None:
    args = bench.build_parser().parse_args(
        [
            "--model",
            "qwen-0.6b",
            "--sample-rate",
            "24000",
            "--voice",
            "clone",
            "--tag",
            "q06-norm",
            "--variants",
            "ru",
            "--corpus",
            "norm",
            "render",
        ]
    )
    assert args.tts_model == "qwen-0.6b"
    assert args.sample_rate == 24000
    assert args.tag == "q06-norm"


def test_voiceprobe_model_is_unchanged() -> None:
    args = bench.build_parser().parse_args(["voiceprobe"])
    assert args.model == "tts-f16"


def test_load_texts() -> None:
    assert len(bench.load_texts("norm")) == 30
    assert [number for number, _ in bench.load_texts("stress")] == [1, 2, 4]


def test_out_dir_without_tag_is_tts_dir() -> None:
    assert bench.out_dir(argparse.Namespace(tag="")) == bench.TTS_DIR
