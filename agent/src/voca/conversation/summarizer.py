import re
import time
from dataclasses import dataclass

import openai as openai_sdk

from voca.conversation.context import approx_tokens

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass(frozen=True)
class Summary:
    body: str
    tokens: int
    ms: int


@dataclass(frozen=True)
class Failure:
    reason: str
    ms: int


class Summarizer:
    def __init__(self, client: openai_sdk.AsyncOpenAI | None, *, model: str) -> None:
        self.client = client
        self.model = model

    @property
    def ready(self) -> bool:
        return self.client is not None

    async def summarize(self, prompt: str) -> Summary | Failure:
        if self.client is None:
            return Failure("no_key", 0)
        started = time.monotonic()
        reason: str | None = None
        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}]
            )
        except openai_sdk.APITimeoutError:
            reason = "timeout"
        except openai_sdk.APIConnectionError:
            reason = "connection"
        except openai_sdk.APIStatusError as error:
            reason = f"http_{error.status_code}"
        except openai_sdk.APIError:
            reason = "error"
        ms = round((time.monotonic() - started) * 1000)
        if reason is not None:
            return Failure(reason, ms)
        if not response.choices:
            return Failure("empty", ms)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            return Failure("length", ms)
        if choice.finish_reason == "content_filter" or choice.message.refusal:
            return Failure("refusal", ms)
        body = THINK.sub("", choice.message.content or "").strip()
        if not body:
            return Failure("empty", ms)
        tokens = (
            response.usage.completion_tokens
            if response.usage is not None
            else approx_tokens(body)
        )
        return Summary(body, tokens, ms)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
