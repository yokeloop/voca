import time

import httpx

from voca.conversation.verdict import (
    Decision,
    DecisionFailure,
    Prompt,
    Situation,
    read_answer,
    request_body,
)

PATH = "/v1/systemone"


class DecisionClient:
    def __init__(
        self, client: httpx.AsyncClient | None, *, model: str, prompt: Prompt
    ) -> None:
        self.client = client
        self.model = model
        self.prompt = prompt

    async def decide(self, situation: Situation) -> Decision | DecisionFailure:
        if self.client is None:
            return DecisionFailure("disabled", 0)
        started = time.monotonic()
        reason: str | None = None
        data: object = None
        try:
            response = await self.client.post(
                PATH, json=request_body(self.prompt, situation, self.model)
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            reason = "timeout"
        except httpx.HTTPStatusError as error:
            reason = f"http_{error.response.status_code}"
        except httpx.HTTPError:
            reason = "connection"
        except ValueError:
            reason = "bad_answer"
        ms = round((time.monotonic() - started) * 1000)
        if reason is not None:
            return DecisionFailure(reason, ms)
        return read_answer(data, self.prompt, ms)

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
