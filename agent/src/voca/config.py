import os
import re
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from voca.conversation.approval import Vocabulary, normalize
from voca.conversation.verdict import KINDS, VERDICTS, Prompt, SorterPrompt

WORD = re.compile(r"^\w+$")
DAILY_AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def default_state_dir() -> Path:
    return Path(__file__).resolve().parents[3] / ".local"


def config_path() -> Path:
    return Path(os.environ.get("VOCA_CONFIG", "~/.voca/config.toml")).expanduser()


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # equal to -c in scripts/llama.sh
    context_tokens: int = Field(default=16384, gt=0)
    thinking_budget: int = Field(default=128, ge=0)
    temperature: float | None = None
    top_k: int | None = None
    top_p: float | None = None
    repeat_penalty: float | None = None


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tts_voice: str = "F4"
    vad_min_silence: float = 0.3
    interruption_min_duration: float = 0.5
    interruption_min_words: int = 0
    aec_warmup: float = 3.0
    endpointing_min_delay: float = 0.2
    preemptive_generation: bool = True
    glue_window_s: float = Field(default=3.0, gt=0)
    action_silence_s: float = Field(default=0.5, ge=0)


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: list[str] = Field(default=["Подтверждаю"], min_length=1)
    deny: list[str] = Field(default=["Отклоняю"], min_length=1)
    echo_words: int = Field(default=3, ge=1)

    @field_validator("confirm", "deny")
    @classmethod
    def single_words(cls, value: list[str]) -> list[str]:
        for word in value:
            if not WORD.match(word):
                raise ValueError(f"ключевое слово — одно слово: {word!r}")
        return value

    @model_validator(mode="after")
    def distinct_words(self) -> Self:
        for confirm in self.confirm:
            for deny in self.deny:
                if normalize(confirm) == normalize(deny):
                    raise ValueError(f"confirm {confirm!r} и deny {deny!r} совпадают")
        return self

    def vocabulary(self) -> Vocabulary:
        return Vocabulary(
            confirm=tuple(self.confirm),
            deny=tuple(self.deny),
            echo_words=self.echo_words,
        )


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    url: str = "http://127.0.0.1:8086"
    model: str = "d1-2B"
    timeout_s: float = Field(default=1.5, gt=0)
    threshold: float = Field(default=0.6, gt=0, le=1)
    instructions: str = (
        "Ассистент озвучил запрос подтверждения команды и ждёт ответа "
        "пользователя. Что пользователь отвечает этой фразой на запрос?"
    )
    verdicts: dict[str, str] = {
        "approve": "Пользователь разрешает выполнить команду: подтверждает, "
        "соглашается, разрешает, и больше ничего не просит",
        "approve_more": "Пользователь разрешает выполнить команду, и во фразе "
        "есть ещё вопрос, просьба или поручение",
        "deny": "Пользователь запрещает команду: отклоняет, отказывается, "
        "просит не выполнять её, и больше ничего не просит",
        "deny_more": "Пользователь запрещает команду, и во фразе есть ещё "
        "вопрос, просьба или поручение",
        "repeat": "Пользователь просит повторить или ещё раз озвучить запрос",
        "unrelated": "Фраза не отвечает на запрос: вопрос, просьба, новое "
        "поручение или разговор о другом",
        "unclear": "Ответ неясен: колебание, оговорка, противоречие или обрывок, "
        "по которому решение не понять",
    }
    sorter_instructions: str = (
        "Ассистент разговаривает с пользователем голосом. В состоянии — последняя "
        "фраза ассистента, ожидающие запросы подтверждения команд и фраза "
        "пользователя в виде распознанной речи. Что это за фраза пользователя?"
    )
    sorter_criteria: dict[str, str] = {
        "noise": "шум, обрывок, поддакивание или междометие без смысла: "
        "«угу», «ага», «yeah», «okay», «ну»",
        "reply": "осмысленная реплика: ответ, вопрос, просьба или мысль",
    }
    sorter_timeout_s: float = Field(default=0.3, gt=0)
    sorter_keep_words: list[str] = Field(
        default=["нет", "не", "ни", "стоп", "да", "давай"], min_length=1
    )
    sorter_max_words: int = Field(default=4, ge=1)
    sorter_min_words: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def complete(self) -> Self:
        if set(self.verdicts) != set(VERDICTS):
            raise ValueError("decision.verdicts: нужны " + ", ".join(VERDICTS))
        if set(self.sorter_criteria) != set(KINDS):
            raise ValueError("decision.sorter_criteria: нужны " + ", ".join(KINDS))
        texts = {
            "instructions": self.instructions,
            "url": self.url,
            "model": self.model,
            **{f"verdicts.{name}": text for name, text in self.verdicts.items()},
            "sorter_instructions": self.sorter_instructions,
            **{
                f"sorter_criteria.{name}": text
                for name, text in self.sorter_criteria.items()
            },
        }
        for name, text in texts.items():
            if not text.strip():
                raise ValueError(f"decision.{name}: пустой текст")
        for word in self.sorter_keep_words:
            if not WORD.match(word):
                raise ValueError(f"decision.sorter_keep_words: одно слово: {word!r}")
        return self

    def prompt(self) -> Prompt:
        return Prompt(
            instructions=self.instructions,
            verdicts=dict(self.verdicts),
            threshold=self.threshold,
        )

    def sorter_prompt(self) -> SorterPrompt:
        return SorterPrompt(
            instructions=self.sorter_instructions,
            criteria=dict(self.sorter_criteria),
            keep_words=frozenset(
                word.lower().replace("ё", "е") for word in self.sorter_keep_words
            ),
            max_words=self.sorter_max_words,
            min_words=self.sorter_min_words,
        )


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path = Field(default=Path("~/.voca/sessions.sqlite3"), validate_default=True)
    history_on_join: int = Field(default=50, ge=0)

    @field_validator("path")
    @classmethod
    def expand_path(cls, value: Path) -> Path:
        return value.expanduser()


class CompressionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "https://litellm.priney.com/v1"
    model: str = "glm-5.3-flash"
    timeout_s: float = Field(default=60, gt=0)


class CompressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(default=0.60, ge=0)
    hard_limit: float = 0.85
    trim_to: float = 0.40
    tail_ratio: float = Field(default=0.20, gt=0, lt=1)
    tail_turns_min: int = Field(default=2, ge=0)
    idle_hours: float = Field(default=4, ge=0)
    daily_at: str = "04:00"
    model: CompressionModel = Field(default_factory=CompressionModel)

    @field_validator("daily_at")
    @classmethod
    def check_daily_at(cls, value: str) -> str:
        if value != "" and not DAILY_AT.match(value):
            raise ValueError("daily_at: HH:MM или пустая строка")
        return value

    @model_validator(mode="after")
    def check_limits(self) -> Self:
        if (
            not 0 < self.trim_to < self.hard_limit < 1
            or self.threshold >= self.hard_limit
        ):
            raise ValueError(
                "compression: нужно 0 < trim_to < hard_limit < 1 "
                "и threshold < hard_limit"
            )
        return self


class DelegationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path = Field(default=Path("~/.voca/tasks.sqlite3"), validate_default=True)
    run_timeout: float = Field(default=900.0, gt=0)

    @field_validator("path")
    @classmethod
    def expand_path(cls, value: Path) -> Path:
        return value.expanduser()


class ConfigFile(TomlConfigSettingsSource):
    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        if "litellm_key" in data:
            raise ValueError(
                f"{config_path()}: litellm_key в файле не читается — "
                "ключ только в VOCA_LITELLM_KEY"
            )
        if "hermes_key" in data:
            raise ValueError(
                f"{config_path()}: hermes_key в файле не читается — "
                "ключ только в VOCA_HERMES_KEY"
            )
        return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VOCA_", env_nested_delimiter="__", extra="forbid"
    )

    web_host: str = "127.0.0.1"
    web_port: int = 8083
    public_livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    livekit_url: str = "ws://127.0.0.1:7880"
    audio_url: str = "http://127.0.0.1:8082/v1"
    llm_url: str = "http://127.0.0.1:8081/v1"
    llm_profile: str = "bolt-e4b"
    agent_port: int = 8084
    state_dir: Path = Field(default_factory=default_state_dir)
    instructions_dir: Path = Path("~/.voca/instructions")
    litellm_key: SecretStr | None = None
    hermes_url: str = ""
    hermes_key: SecretStr | None = None
    model: ModelConfig = Field(default_factory=ModelConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    delegation: DelegationConfig = Field(default_factory=DelegationConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)

    @field_validator("instructions_dir")
    @classmethod
    def expand_instructions_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("litellm_key", "hermes_key", mode="before")
    @classmethod
    def empty_key_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            ConfigFile(settings_cls, toml_file=config_path()),
        )

    def trim_at_tokens(self) -> int:
        return int(self.compression.hard_limit * self.model.context_tokens)

    def trim_to_tokens(self) -> int:
        return int(self.compression.trim_to * self.model.context_tokens)
