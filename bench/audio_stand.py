import codecs
import os
import sys
import termios
import time
import tty
import wave
from pathlib import Path
from typing import Any

import httpx
import openai as openai_sdk
from livekit import rtc
from livekit.plugins import openai
from openai._models import FinalRequestOptions

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / ".local" / "bench"
AUDIO_URL = "http://127.0.0.1:8082/v1"
STT_MODEL = "asr"
TTS_MODEL = "tts"
TTS_SAMPLE_RATE = 44100
API_KEY = "none"
RU_LAYOUT = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.",
)


class SpeechLanguageClient(openai_sdk.AsyncOpenAI):
    def __init__(self, *, speech_language: str, **kwargs: Any) -> None:
        self._speech_language = speech_language
        super().__init__(**kwargs)

    async def _prepare_options(
        self, options: FinalRequestOptions
    ) -> FinalRequestOptions:
        options = await super()._prepare_options(options)
        if options.url == "/audio/speech" and isinstance(options.json_data, dict):
            options.json_data = {
                **options.json_data,
                "language": self._speech_language,
            }
        return options


def make_stt(base_url: str) -> openai.STT:
    return openai.STT(
        model=STT_MODEL, language="ru", base_url=base_url, api_key=API_KEY
    )


def make_tts(base_url: str, voice: str, language: str | None) -> openai.TTS:
    if language is None:
        return openai.TTS(
            model=TTS_MODEL,
            voice=voice,
            response_format="pcm",
            sample_rate=TTS_SAMPLE_RATE,
            base_url=base_url,
            api_key=API_KEY,
        )
    return openai.TTS(
        model=TTS_MODEL,
        voice=voice,
        response_format="pcm",
        sample_rate=TTS_SAMPLE_RATE,
        client=SpeechLanguageClient(
            speech_language=language,
            base_url=base_url,
            api_key=API_KEY,
            max_retries=0,
        ),
    )


def read_wav(path: Path) -> rtc.AudioFrame:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError(f"{path}: нужен PCM16 моно")
        rate = wav.getframerate()
        count = wav.getnframes()
        data = wav.readframes(count)
    return rtc.AudioFrame(
        data=data, sample_rate=rate, num_channels=1, samples_per_channel=count
    )


def write_wav(path: Path, frame: rtc.AudioFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(frame.num_channels)
        wav.setsampwidth(2)
        wav.setframerate(frame.sample_rate)
        wav.writeframes(bytes(frame.data))


def trim(frame: rtc.AudioFrame, seconds: float) -> rtc.AudioFrame:
    count = min(frame.samples_per_channel, int(frame.sample_rate * seconds))
    width = 2 * frame.num_channels
    return rtc.AudioFrame(
        data=bytes(frame.data)[: count * width],
        sample_rate=frame.sample_rate,
        num_channels=frame.num_channels,
        samples_per_channel=count,
    )


async def synthesize(tts: openai.TTS, text: str) -> tuple[rtc.AudioFrame, float, float]:
    start = time.perf_counter()
    ttfb = 0.0
    frames: list[rtc.AudioFrame] = []
    async with tts.synthesize(text) as stream:
        async for audio in stream:
            if not frames:
                ttfb = time.perf_counter() - start
            frames.append(audio.frame)
    total = time.perf_counter() - start
    return rtc.combine_audio_frames(frames), ttfb, total


def health(url: str) -> dict[str, Any]:
    base = url.removesuffix("/v1")
    try:
        response = httpx.get(f"{base}/health", timeout=5)
    except httpx.HTTPError:
        raise SystemExit("audiocpp_server не отвечает: scripts/up.sh audio")
    if response.status_code != 200:
        raise SystemExit("audiocpp_server не отвечает: scripts/up.sh audio")
    data: dict[str, Any] = response.json()
    return data


def read_line(prompt: str) -> str:
    print(prompt, end="", flush=True)
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        utf8 = termios.tcgetattr(fd)
        utf8[0] |= termios.IUTF8
        termios.tcsetattr(fd, termios.TCSANOW, utf8)
        try:
            line = sys.stdin.buffer.readline()
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
    else:
        line = sys.stdin.buffer.readline()
    if not line:
        raise EOFError
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


def read_answer(prompt: str) -> str:
    return read_line(prompt).strip().lower().translate(RU_LAYOUT)


def flush_input() -> None:
    if sys.stdin.isatty():
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)


def read_key(prompt: str, choices: list[str]) -> str:
    if not sys.stdin.isatty():
        while True:
            answer = read_answer(prompt)
            if answer in choices:
                return answer
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    flush_input()
    print(prompt, end="", flush=True)
    try:
        tty.setcbreak(fd)
        while True:
            data = os.read(fd, 1)
            if not data:
                raise EOFError
            key = decoder.decode(data).lower().translate(RU_LAYOUT)
            if key in choices:
                print(key)
                return key
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
