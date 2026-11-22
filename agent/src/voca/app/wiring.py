import asyncio
import concurrent.futures
import time
from collections.abc import Callable
from typing import Any

import httpx
import openai as openai_sdk
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    stt,
    tts,
    utils,
    vad,
)
from livekit.agents.voice import AgentSession, SessionConnectOptions
from livekit.agents.voice.transcription.text_transforms import TextTransforms
from livekit.plugins import openai
from openai._models import FinalRequestOptions

from voca.config import Settings
from voca.conversation.compaction import Limits
from voca.conversation.decision import DecisionClient
from voca.conversation.summarizer import Summarizer
from voca.speech.fragments import FirstFragmentTokenizer
from voca.speech.markup import strip_latex_and_tables
from voca.speech.prepare import Prepared, prepare
from voca.speech.stress import Stress
from voca.telemetry.jsonl import JsonlLog

OnPrepared = Callable[[str, Prepared, float], None]

# the framework closes the session after this many consecutive component failures
MAX_UNRECOVERABLE_ERRORS = 1_000_000

TTS_TEXT_TRANSFORMS: list[TextTransforms] = [
    "filter_markdown",
    "filter_emoji",
    strip_latex_and_tables,
]

SAMPLING = ("temperature", "top_k", "top_p", "repeat_penalty")


class SpeechLanguageClient(openai_sdk.AsyncOpenAI):
    def __init__(
        self,
        *,
        speech_language: str,
        speech_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._speech_language = speech_language
        self._speech_options = speech_options or {}
        super().__init__(**kwargs)

    async def _prepare_options(
        self, options: FinalRequestOptions
    ) -> FinalRequestOptions:
        options = await super()._prepare_options(options)
        if options.url == "/audio/speech" and isinstance(options.json_data, dict):
            options.json_data = {
                **options.json_data,
                **self._speech_options,
                "language": self._speech_language,
            }
        return options


class PreparedTTS(tts.TTS[Any]):
    def __init__(
        self,
        inner: tts.TTS[Any],
        stress: Stress,
        executor: concurrent.futures.Executor,
        on_prepared: OnPrepared,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=inner.sample_rate,
            num_channels=inner.num_channels,
        )
        self.inner = inner
        self.stress = stress
        self.executor = executor
        self.on_prepared = on_prepared

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "PreparedStream":
        return PreparedStream(tts=self, input_text=text, conn_options=conn_options)


class PreparedStream(tts.ChunkedStream):
    def __init__(
        self, *, tts: PreparedTTS, input_text: str, conn_options: APIConnectOptions
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._prepared_tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        owner = self._prepared_tts
        start = time.perf_counter()
        prepared = await asyncio.get_running_loop().run_in_executor(
            owner.executor, prepare, self.input_text, owner.stress
        )
        owner.on_prepared(
            self.input_text, prepared, (time.perf_counter() - start) * 1000
        )
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=owner.sample_rate,
            num_channels=owner.num_channels,
            mime_type="audio/pcm",
        )
        async with owner.inner.synthesize(
            prepared.text, conn_options=APIConnectOptions(max_retry=0)
        ) as stream:
            async for audio in stream:
                output_emitter.push(audio.frame.data.tobytes())
        output_emitter.flush()


def make_stt(settings: Settings, vad: vad.VAD) -> stt.StreamAdapter:
    return stt.StreamAdapter(
        stt=openai.STT(
            model="asr", language="ru", base_url=settings.audio_url, api_key="none"
        ),
        vad=vad,
    )


def make_llm(settings: Settings) -> openai.LLM:
    extra_body: dict[str, Any] = {
        "thinking_budget_tokens": settings.model.thinking_budget
    }
    for name in SAMPLING:
        value = getattr(settings.model, name)
        if value is not None:
            extra_body[name] = value
    return openai.LLM(
        model=settings.llm_profile,
        base_url=settings.llm_url,
        api_key="none",
        extra_body=extra_body,
    )


def compaction_limits(settings: Settings) -> Limits:
    window = settings.model.context_tokens
    c = settings.compression
    return Limits(
        threshold=int(c.threshold * window),
        tail_budget=int(c.tail_ratio * c.threshold * window),
        tail_turns_min=c.tail_turns_min,
        idle_hours=c.idle_hours,
        daily_at=c.daily_at,
    )


def make_summarizer(settings: Settings) -> Summarizer:
    if settings.litellm_key is None:
        return Summarizer(None, model=settings.compression.model.model)
    return Summarizer(
        openai_sdk.AsyncOpenAI(
            api_key=settings.litellm_key.get_secret_value(),
            base_url=settings.compression.model.url,
            timeout=settings.compression.model.timeout_s,
            max_retries=0,
        ),
        model=settings.compression.model.model,
    )


def make_decider(settings: Settings) -> DecisionClient:
    decision = settings.decision
    if not decision.enabled:
        return DecisionClient(
            None,
            model=decision.model,
            sorter=decision.sorter_prompt(),
            sorter_timeout_s=decision.sorter_timeout_s,
        )
    return DecisionClient(
        httpx.AsyncClient(
            base_url=decision.url.rstrip("/"), timeout=decision.timeout_s
        ),
        model=decision.model,
        prompt=decision.prompt(),
        sorter=decision.sorter_prompt(),
        sorter_timeout_s=decision.sorter_timeout_s,
    )


async def check_context(
    settings: Settings, log: JsonlLog, *, timeout: float = 1.0
) -> None:
    url = settings.llm_url.rstrip("/").removesuffix("/v1") + "/props"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            n_ctx = response.json()["default_generation_settings"]["n_ctx"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return
    if not isinstance(n_ctx, int):
        return
    if n_ctx != settings.model.context_tokens:
        log.write(
            "context_mismatch",
            n_ctx=n_ctx,
            context_tokens=settings.model.context_tokens,
        )


def make_tts(
    settings: Settings,
    stress: Stress,
    executor: concurrent.futures.Executor,
    on_prepared: OnPrepared,
) -> tts.StreamAdapter:
    inner = openai.TTS(
        model="tts",
        voice=settings.agent.tts_voice,
        response_format="pcm",
        sample_rate=44100,
        client=SpeechLanguageClient(
            speech_language="ru",
            base_url=settings.audio_url,
            api_key="none",
            max_retries=0,
        ),
    )
    return tts.StreamAdapter(
        tts=PreparedTTS(inner, stress, executor, on_prepared),
        sentence_tokenizer=FirstFragmentTokenizer(),
    )


def build_session(
    settings: Settings,
    *,
    vad: vad.VAD,
    stress: Stress,
    executor: concurrent.futures.Executor,
    on_prepared: OnPrepared,
) -> AgentSession[None]:
    return AgentSession(
        vad=vad,
        stt=make_stt(settings, vad),
        llm=make_llm(settings),
        tts=make_tts(settings, stress, executor, on_prepared),
        tts_text_transforms=TTS_TEXT_TRANSFORMS,
        conn_options=SessionConnectOptions(
            stt_conn_options=APIConnectOptions(max_retry=1, timeout=5.0),
            llm_conn_options=APIConnectOptions(max_retry=0, timeout=5.0),
            tts_conn_options=APIConnectOptions(max_retry=1, timeout=5.0),
            max_unrecoverable_errors=MAX_UNRECOVERABLE_ERRORS,
        ),
        aec_warmup_duration=settings.agent.aec_warmup,
        turn_handling={
            "turn_detection": "vad",
            "endpointing": {"min_delay": settings.agent.endpointing_min_delay},
            "interruption": {
                "mode": "vad",
                "min_duration": settings.agent.interruption_min_duration,
                "min_words": settings.agent.interruption_min_words,
            },
            "preemptive_generation": {"enabled": settings.agent.preemptive_generation},
        },
    )
