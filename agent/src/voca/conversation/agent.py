from collections.abc import AsyncGenerator

from livekit.agents import FlushSentinel, ToolError, function_tool, llm
from livekit.agents.voice import Agent, ModelSettings

from voca.speech.markup import scan

INSTRUCTIONS = (
    "Ты — VOCA, голосовой ассистент. Отвечай по-русски. Твой ответ произносится "
    "вслух: отвечай одной–тремя короткими фразами, первая фраза — самая короткая. "
    "Не используй Markdown, списки, таблицы, формулы и эмодзи. Подробно — только "
    "если просят. Числа можно писать цифрами. Поручения, которые требуют работы "
    "с проектами, файлами, поиском или долгой работы, передавай инструментом "
    "delegate. Если неясно, что поручить, задай уточняющий вопрос."
)


class VocaAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool
    async def delegate(self, task: str) -> str:
        """Поручить задачу основному агенту Hermes: работа с проектами, файлами, поиском, заметками и всё, что требует времени. task — полная формулировка поручения по-русски."""
        raise ToolError("Делегирование сейчас недоступно")

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[llm.ChatChunk | str | FlushSentinel]:
        buffer = ""
        inside = False
        async for item in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(item, str):
                text, buffer, inside = scan(buffer + item, inside)
                if text:
                    yield text
            elif (
                isinstance(item, llm.ChatChunk)
                and item.delta is not None
                and item.delta.content
            ):
                text, buffer, inside = scan(buffer + item.delta.content, inside)
                if text or item.delta.tool_calls or item.usage is not None:
                    yield llm.ChatChunk(
                        id=item.id,
                        delta=llm.ChoiceDelta(
                            role=item.delta.role,
                            content=text,
                            tool_calls=item.delta.tool_calls,
                        ),
                        usage=item.usage,
                    )
            else:
                yield item
        if buffer and not inside:
            yield buffer
