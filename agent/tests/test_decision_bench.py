import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "bench" / "decision_ru.py"
    spec = importlib.util.spec_from_file_location("decision_ru", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["decision_ru"] = module
    spec.loader.exec_module(module)
    return module


bench = load()
REPLY = {
    "kind": ("reply", 0.9),
    "relation": ("answers", 0.9),
    "answer": ("deny", 0.6),
}


def test_after_rule_keeps_stop_command() -> None:
    item = {"phrase": "Хватит.", "requests": [], "expect": ["other"]}
    result = bench.score("b", item, {"kind": ("noise", 0.7)})
    assert result["picked"] == "noise"
    assert result["passed"] is False
    assert result["after"] == "other"
    assert result["after_passed"] is True


def test_after_rule_drops_noise() -> None:
    item = {"phrase": "Угу.", "requests": [], "expect": ["other"]}
    result = bench.score("b", item, {"kind": ("noise", 0.7)})
    assert result["after"] == "noise"


def test_after_guard_question() -> None:
    item = {"phrase": "Безопасно ли это?", "requests": ["r01"], "expect": ["ask"]}
    result = bench.score("b", item, REPLY)
    assert result["picked"] == "deny"
    assert result["false_deny"] is True
    assert result["after"] == "ask"
    assert result["after_false_deny"] is False


def test_after_guard_keeps_marked_deny() -> None:
    item = {"phrase": "Не надо.", "requests": ["r01"], "expect": ["deny"]}
    result = bench.score("b", item, REPLY)
    assert result["after"] == "deny"
    assert result["after_passed"] is True
