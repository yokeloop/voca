import json
from pathlib import Path

import pydantic
import pytest

from voca.config import Settings
from voca.conversation.verdict import VERDICTS

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.toml"


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("VOCA_CONFIG", str(path))
    return path


def test_defaults_without_file(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert settings.agent_port == 8084
    assert settings.state_dir.name == ".local"
    assert settings.instructions_dir == Path.home() / ".voca" / "instructions"
    agent = settings.agent
    assert agent.tts_voice == "F4"
    assert agent.vad_min_silence == 0.3
    assert agent.interruption_min_duration == 0.5
    assert agent.interruption_min_words == 0
    assert agent.aec_warmup == 3.0
    assert agent.endpointing_min_delay == 0.5
    assert agent.preemptive_generation is False
    model = settings.model
    assert model.thinking_budget == 128
    assert model.context_tokens == 16384
    assert model.temperature is None
    assert model.top_k is None
    assert model.top_p is None
    assert model.repeat_penalty is None
    assert settings.trim_to_tokens() == 6553
    assert settings.trim_at_tokens() == 13926
    assert settings.session.path == Path.home() / ".voca" / "sessions.sqlite3"
    assert settings.compression.model.url == "https://litellm.priney.com/v1"
    assert settings.litellm_key is None
    assert settings.delegation.path == Path.home() / ".voca" / "tasks.sqlite3"
    assert settings.delegation.run_timeout == 900
    assert settings.hermes_url == ""
    assert settings.hermes_key is None
    monkeypatch.setenv("VOCA_AGENT_PORT", "9999")
    assert Settings().agent_port == 9999
    monkeypatch.setenv("VOCA_INSTRUCTIONS_DIR", "~/x")
    assert Settings().instructions_dir == Path.home() / "x"


def test_file_overrides_defaults_env_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        tmp_path,
        monkeypatch,
        "[compression]\nthreshold = 0.5\ntrim_to = 0.3\n"
        '[agent]\ntts_voice = "M1"\n'
        "[compression.model]\ntimeout_s = 9\n",
    )
    settings = Settings()
    assert settings.compression.threshold == 0.5
    assert settings.compression.trim_to == 0.3
    assert settings.agent.tts_voice == "M1"
    assert settings.compression.model.timeout_s == 9
    monkeypatch.setenv("VOCA_COMPRESSION__THRESHOLD", "0.08")
    settings = Settings()
    assert settings.compression.threshold == 0.08
    assert settings.compression.trim_to == 0.3
    assert Settings(compression={"threshold": 0.07}).compression.threshold == 0.07


def test_key_only_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOCA_LITELLM_KEY", "abc")
    key = Settings().litellm_key
    assert key is not None
    assert key.get_secret_value() == "abc"
    monkeypatch.setenv("VOCA_LITELLM_KEY", "")
    assert Settings().litellm_key is None
    monkeypatch.delenv("VOCA_LITELLM_KEY")
    write_config(tmp_path, monkeypatch, '[compression.model]\napi_key = "x"\n')
    with pytest.raises(pydantic.ValidationError):
        Settings()
    write_config(tmp_path, monkeypatch, 'api_key = "x"\n')
    with pytest.raises(pydantic.ValidationError):
        Settings()
    write_config(tmp_path, monkeypatch, 'litellm_key = "x"\n')
    with pytest.raises(ValueError, match="VOCA_LITELLM_KEY"):
        Settings()


def test_delegation_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOCA_DELEGATION__RUN_TIMEOUT", "20")
    assert Settings().delegation.run_timeout == 20
    monkeypatch.setenv("VOCA_DELEGATION__PATH", "~/t.sqlite3")
    assert Settings().delegation.path == Path.home() / "t.sqlite3"
    monkeypatch.delenv("VOCA_DELEGATION__RUN_TIMEOUT")
    monkeypatch.delenv("VOCA_DELEGATION__PATH")
    write_config(tmp_path, monkeypatch, "[delegation]\nrun_timeout = 0\n")
    with pytest.raises(ValueError):
        Settings()
    write_config(tmp_path, monkeypatch, "[delegation]\nunknown = 1\n")
    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_hermes_key_only_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOCA_HERMES_KEY", "abc")
    key = Settings().hermes_key
    assert key is not None
    assert key.get_secret_value() == "abc"
    monkeypatch.setenv("VOCA_HERMES_KEY", "")
    assert Settings().hermes_key is None
    monkeypatch.delenv("VOCA_HERMES_KEY")
    monkeypatch.setenv("VOCA_HERMES_URL", "https://h.example")
    assert Settings().hermes_url == "https://h.example"
    monkeypatch.delenv("VOCA_HERMES_URL")
    write_config(tmp_path, monkeypatch, 'hermes_key = "x"\n')
    with pytest.raises(ValueError, match="VOCA_HERMES_KEY"):
        Settings()


def test_invalid_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for text in (
        "[compression\n",
        "[compression]\nhard_limit = 0.3\n",
        "[compression]\ntrim_to = 0\n",
        "[compression]\nhard_limit = 1.0\n",
        "[compression]\nthreshold = 0.85\n",
        '[compression]\ndaily_at = "25:00"\n',
    ):
        write_config(tmp_path, monkeypatch, text)
        with pytest.raises(ValueError):
            Settings()
    write_config(tmp_path, monkeypatch, '[compression]\ndaily_at = ""\n')
    assert Settings().compression.daily_at == ""
    write_config(tmp_path, monkeypatch, "[compression]\nthreshold = 0\n")
    assert Settings().compression.threshold == 0


def test_example_config_is_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Settings().model_dump()
    monkeypatch.setenv("VOCA_CONFIG", str(EXAMPLE))
    assert EXAMPLE.is_file()
    assert Settings().model_dump() == defaults


def test_approval_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approval = Settings().approval
    assert approval.confirm == ["Подтверждаю"]
    assert approval.deny == ["Отклоняю"]
    assert approval.echo_words == 3
    monkeypatch.setenv("VOCA_APPROVAL__CONFIRM", '["Принимаю", "Согласен"]')
    vocabulary = Settings().approval.vocabulary()
    assert vocabulary.confirm == ("Принимаю", "Согласен")
    monkeypatch.delenv("VOCA_APPROVAL__CONFIRM")
    write_config(
        tmp_path,
        monkeypatch,
        '[approval]\ndeny = ["Запрещаю"]\necho_words = 4\n',
    )
    vocabulary = Settings().approval.vocabulary()
    assert vocabulary.deny == ("Запрещаю",)
    assert vocabulary.echo_words == 4
    for text in ('[approval]\nconfirm = ["Да ладно"]\n', "[approval]\nconfirm = []\n"):
        write_config(tmp_path, monkeypatch, text)
        with pytest.raises(pydantic.ValidationError):
            Settings()


@pytest.mark.parametrize(
    "text",
    [
        '[approval]\nconfirm = ["Подтверждаю"]\ndeny = ["подтверждаю"]\n',
        '[approval]\nconfirm = ["Подтверждаю"]\ndeny = ["ПОДТВЕРЖДАЮ"]\n',
        '[approval]\nrefusal_words = ["нет"]\n',
        "[approval]\nnear = 2\n",
        "[approval]\necho_words = 0\n",
    ],
)
def test_approval_settings_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    write_config(tmp_path, monkeypatch, text)
    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_approval_words_disjoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        tmp_path, monkeypatch, '[approval]\nconfirm = ["Ёлка"]\ndeny = ["елка"]\n'
    )
    with pytest.raises(pydantic.ValidationError):
        Settings()
    write_config(
        tmp_path, monkeypatch, '[approval]\nconfirm = ["Да"]\ndeny = ["Дай"]\n'
    )
    assert Settings().approval.vocabulary().deny == ("Дай",)


def test_decision_defaults() -> None:
    decision = Settings().decision
    assert decision.enabled is True
    assert decision.url == "http://127.0.0.1:8086"
    assert decision.model == "d1-2B"
    assert decision.timeout_s == 1.5
    prompt = decision.prompt()
    assert prompt.threshold == 0.6
    assert prompt.extra_threshold == 0.5
    assert set(prompt.verdicts) == set(VERDICTS)
    assert all(text.strip() for text in prompt.verdicts.values())
    assert prompt.instructions.strip()
    assert prompt.extra.strip()


def test_decision_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOCA_DECISION__URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("VOCA_DECISION__ENABLED", "false")
    verdicts = {name: f"свой текст {name}" for name in VERDICTS}
    monkeypatch.setenv("VOCA_DECISION__VERDICTS", json.dumps(verdicts))
    decision = Settings().decision
    assert decision.url == "http://127.0.0.1:9999"
    assert decision.enabled is False
    assert decision.prompt().verdicts == verdicts


@pytest.mark.parametrize(
    "text",
    [
        '[decision.verdicts]\napprove = "a"\ndeny = "d"\nrepeat = "r"\nunrelated = "u"\n',
        (
            '[decision.verdicts]\napprove = "a"\ndeny = "d"\nrepeat = "r"\n'
            'unrelated = "u"\nunclear = "c"\nmaybe = "m"\n'
        ),
        (
            '[decision.verdicts]\napprove = " "\ndeny = "d"\nrepeat = "r"\n'
            'unrelated = "u"\nunclear = "c"\n'
        ),
        '[decision]\ninstructions = ""\n',
        '[decision]\nextra = " "\n',
        '[decision]\nurl = ""\n',
        '[decision]\nmodel = ""\n',
        "[decision]\nthreshold = 0\n",
        "[decision]\nthreshold = 1.5\n",
        "[decision]\nextra_threshold = 1\n",
        "[decision]\ntimeout_s = 0\n",
    ],
)
def test_decision_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    write_config(tmp_path, monkeypatch, text)
    with pytest.raises(pydantic.ValidationError):
        Settings()
