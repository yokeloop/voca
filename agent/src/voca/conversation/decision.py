import asyncio
import time

import httpx

from voca.conversation.verdict import (
    DecisionFailure,
    Sorted,
    SorterPrompt,
    read_sort,
    sorter_body,
)

PATH = "/v1/systemone"


class DecisionClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None,
        *,
        model: str,
        sorter: SorterPrompt | None = None,
        sorter_timeout_s: float = 0.3,
    ) -> None:
        self.client = client
        self.model = model
        self.sorter = sorter
        self.sorter_timeout_s = sorter_timeout_s

    async def sort(
        self, state: dict[str, str], requests: int = 0
    ) -> Sorted | DecisionFailure:
        if self.client is None or self.sorter is None:
            return DecisionFailure("disabled", 0)
        started = time.monotonic()
        reason: str | None = None
        data: object = None
        try:
            async with asyncio.timeout(self.sorter_timeout_s):
                response = await self.client.post(
                    PATH,
                    json=sorter_body(self.sorter, state, self.model, requests),
                    timeout=self.sorter_timeout_s,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.TimeoutException, TimeoutError):
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
        return read_sort(data, ms, requests)

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
