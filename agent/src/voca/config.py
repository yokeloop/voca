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

from voca.conversation.approval_judge import Rules
from voca.conversation.verdict import KINDS, RELATIONS, REPLIES, SorterPrompt

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
    event_silence_s: float = Field(default=1.5, ge=0)


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    url: str = "http://127.0.0.1:8086"
    model: str = "d1-2B"
    timeout_s: float = Field(default=1.5, gt=0)
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
    sorter_relation_instructions: str = (
        "Ассистент ждёт ответа на запрос подтверждения команды. Как фраза "
        "пользователя связана с этим запросом?"
    )
    sorter_relation_criteria: dict[str, str] = {
        "answers": "отвечает на запрос: разрешает или запрещает выполнить команду",
        "asks": "спрашивает о запросе: что именно будет сделано, зачем, "
        "просит повторить",
        "other": "не о запросе: другая тема, просьба или разговор",
    }
    sorter_answer_instructions: str = (
        "Ассистент ждёт ответа на запрос подтверждения команды. Что пользователь "
        "отвечает этой фразой на запрос?"
    )
    sorter_answer_criteria: dict[str, str] = {
        "approve": "подтверждает: разрешает выполнить команду",
        "deny": "отклоняет: запрещает выполнять команду",
    }
    sorter_target_instructions: str = (
        "Ассистент ждёт ответа на несколько запросов подтверждения команд, они "
        "пронумерованы: запрос 1, запрос 2 и далее. К какому запросу относится "
        "фраза пользователя?"
    )
    sorter_target_request: str = "только к запросу {n}"
    sorter_target_unclear: str = "к нескольким запросам, ни к одному или непонятно"
    threshold_low: float = Field(default=0.45, gt=0, le=1)
    threshold_medium: float = Field(default=0.50, gt=0, le=1)
    threshold_high: float = Field(default=0.50, gt=0, le=1)
    threshold_deny: float = Field(default=0.45, gt=0, le=1)
    clarify_limit: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def complete(self) -> Self:
        if set(self.sorter_criteria) != set(KINDS):
            raise ValueError("decision.sorter_criteria: нужны " + ", ".join(KINDS))
        if set(self.sorter_relation_criteria) != set(RELATIONS):
            raise ValueError(
                "decision.sorter_relation_criteria: нужны " + ", ".join(RELATIONS)
            )
        if set(self.sorter_answer_criteria) != set(REPLIES):
            raise ValueError(
                "decision.sorter_answer_criteria: нужны " + ", ".join(REPLIES)
            )
        texts = {
            "url": self.url,
            "model": self.model,
            "sorter_instructions": self.sorter_instructions,
            **{
                f"sorter_criteria.{name}": text
                for name, text in self.sorter_criteria.items()
            },
            "sorter_relation_instructions": self.sorter_relation_instructions,
            **{
                f"sorter_relation_criteria.{name}": text
                for name, text in self.sorter_relation_criteria.items()
            },
            "sorter_answer_instructions": self.sorter_answer_instructions,
            **{
                f"sorter_answer_criteria.{name}": text
                for name, text in self.sorter_answer_criteria.items()
            },
            "sorter_target_instructions": self.sorter_target_instructions,
            "sorter_target_request": self.sorter_target_request,
            "sorter_target_unclear": self.sorter_target_unclear,
        }
        for name, text in texts.items():
            if not text.strip():
                raise ValueError(f"decision.{name}: пустой текст")
        try:
            self.sorter_target_request.format(n=1)
        except (IndexError, KeyError, ValueError):
            raise ValueError("decision.sorter_target_request: нужен {n}") from None
        if "{n}" not in self.sorter_target_request:
            raise ValueError("decision.sorter_target_request: нужен {n}")
        for word in self.sorter_keep_words:
            if not WORD.match(word):
                raise ValueError(f"decision.sorter_keep_words: одно слово: {word!r}")
        return self

    def sorter_prompt(self) -> SorterPrompt:
        return SorterPrompt(
            instructions=self.sorter_instructions,
            criteria=dict(self.sorter_criteria),
            keep_words=frozenset(
                word.lower().replace("ё", "е") for word in self.sorter_keep_words
            ),
            max_words=self.sorter_max_words,
            min_words=self.sorter_min_words,
            relation_instructions=self.sorter_relation_instructions,
            relation_criteria=dict(self.sorter_relation_criteria),
            answer_instructions=self.sorter_answer_instructions,
            answer_criteria=dict(self.sorter_answer_criteria),
            target_instructions=self.sorter_target_instructions,
            target_request=self.sorter_target_request,
            target_unclear=self.sorter_target_unclear,
        )

    def rules(self) -> Rules:
        return Rules(
            low=self.threshold_low,
            medium=self.threshold_medium,
            high=self.threshold_high,
            deny=self.threshold_deny,
            clarify_limit=self.clarify_limit,
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
    approval_timeout_s: float = Field(default=300.0, gt=0)
    approval_reminder_s: float = Field(default=60.0, ge=0)

    @field_validator("path")
    @classmethod
    def expand_path(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def reminder_before_timeout(self) -> Self:
        if self.approval_reminder_s >= self.approval_timeout_s:
            raise ValueError("delegation: approval_reminder_s < approval_timeout_s")
        return self


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
