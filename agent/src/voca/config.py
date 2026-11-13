from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# equal to -c in scripts/llama.sh
LLM_CONTEXT_TOKENS = 16384


def default_state_dir() -> Path:
    return Path(__file__).resolve().parents[3] / ".local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOCA_")

    web_host: str = "127.0.0.1"
    web_port: int = 8083
    public_livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    livekit_url: str = "ws://127.0.0.1:7880"
    audio_url: str = "http://127.0.0.1:8082/v1"
    llm_url: str = "http://127.0.0.1:8081/v1"
    llm_profile: str = "lfm2.5-2.6b"
    tts_voice: str = "F4"
    llm_thinking_budget: int = 128
    vad_min_silence: float = 0.3
    interruption_min_duration: float = 0.5
    interruption_min_words: int = 0
    aec_warmup: float = 3.0
    endpointing_min_delay: float = 0.5
    preemptive_generation: bool = False
    agent_port: int = 8084
    state_dir: Path = Field(default_factory=default_state_dir)
    instructions_dir: Path = Path("~/.voca/instructions")
    ctx_trim_high: float = 0.60
    ctx_trim_low: float = 0.40

    @field_validator("instructions_dir")
    @classmethod
    def expand_instructions_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def check_trim(self) -> Self:
        if not 0 < self.ctx_trim_low < self.ctx_trim_high < 1:
            raise ValueError("VOCA_CTX_TRIM_LOW < VOCA_CTX_TRIM_HIGH, both in (0, 1)")
        return self

    def ctx_high_tokens(self) -> int:
        return int(self.ctx_trim_high * LLM_CONTEXT_TOKENS)

    def ctx_low_tokens(self) -> int:
        return int(self.ctx_trim_low * LLM_CONTEXT_TOKENS)
