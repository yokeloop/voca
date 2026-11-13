import asyncio
import re

from livekit.agents import utils
from livekit.agents.tokenize import (
    SentenceStream,
    SentenceTokenizer,
    TokenData,
    blingfire,
)

FRAGMENT_BREAK = re.compile(r",(?=\s)|\s[—–-]\s")
FRAGMENT_WORDS = 3
SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def fragment_of(text: str) -> str | None:
    for match in FRAGMENT_BREAK.finditer(text):
        head = text[: match.end()].strip()
        if len(re.findall(r"\w+", head)) >= FRAGMENT_WORDS:
            return head
    return None


def head_end(text: str, head: str) -> int:
    return text.find(head) + len(head)


class FirstFragmentTokenizer(SentenceTokenizer):
    def __init__(self, sentences: SentenceTokenizer | None = None) -> None:
        self._sentences = sentences or blingfire.SentenceTokenizer(retain_format=True)

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        head = fragment_of(text)
        if head is not None:
            end = head_end(text, head)
            stop = SENTENCE_END.search(text)
            if stop is None or stop.start() >= end:
                rest = text[end:].lstrip()
                return [head, *self._sentences.tokenize(rest, language=language)]
        return self._sentences.tokenize(text, language=language)

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return FirstFragmentStream(self._sentences.stream(language=language))


class FirstFragmentStream(SentenceStream):
    def __init__(self, sentences: SentenceStream) -> None:
        super().__init__()
        self._sentences = sentences
        self._segment_id = utils.shortuuid()
        self._buffer = ""
        self._decided = False
        self._task = asyncio.create_task(self._forward())

    async def _forward(self) -> None:
        async for data in self._sentences:
            self._event_ch.send_nowait(data)
        self._event_ch.close()

    def _release(self) -> None:
        if self._buffer:
            self._sentences.push_text(self._buffer)
            self._buffer = ""
            self._decided = True

    def push_text(self, text: str) -> None:
        self._check_not_closed()
        if self._decided:
            self._sentences.push_text(text)
            return
        self._buffer += text
        head = fragment_of(self._buffer)
        end = len(self._buffer) if head is None else head_end(self._buffer, head)
        for stop in SENTENCE_END.finditer(self._buffer):
            if stop.start() < end and stop.end() < len(self._buffer):
                self._release()
                return
        if head is None:
            return
        self._event_ch.send_nowait(TokenData(segment_id=self._segment_id, token=head))
        rest = self._buffer[end:].lstrip()
        self._buffer = ""
        self._decided = True
        if rest:
            self._sentences.push_text(rest)

    def flush(self) -> None:
        self._check_not_closed()
        self._release()
        self._sentences.flush()

    def end_input(self) -> None:
        self._check_not_closed()
        self._release()
        self._sentences.end_input()

    async def aclose(self) -> None:
        await self._sentences.aclose()
        await utils.aio.cancel_and_wait(self._task)
        self._event_ch.close()
