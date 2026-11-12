import pytest

from voca.config import Settings


def test_settings_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert settings.agent_port == 8084
    assert settings.llm_thinking_budget == 128
    assert settings.state_dir.name == ".local"
    monkeypatch.setenv("VOCA_AGENT_PORT", "9999")
    assert Settings().agent_port == 9999
