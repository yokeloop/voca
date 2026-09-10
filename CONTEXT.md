---
tags:
  - voice-agent
  - v3
  - context
  - glossary
---

# Voice Agent v3 — Domain Glossary

- **Voice turn** — одна реплика пользователя и ответ агента (завершение, прерывание или ошибка).
- **Barge-in** — перебивание агента пользователем во время речи агента.
- **TTS cancel** — остановка TTS стрима и очистка буфера при barge-in (ручная логика поверх FastRTC interrupt).
- **AEC** — acoustic echo cancellation, браузерный WebRTC механизм; агент не слышит сам себя.
- **Turn detection** — определение окончания реплики пользователя (VAD/semantic); когда агенту отвечать.
- **Semantic activation** — (R&D) always-on слушание с семантическим решением "обращаются ли к агенту".
- **LLM loop** — custom цикл пайплайна: context → LLM → парсинг (text/tool) → dispatch → результат → повтор.
- **Промежуточная реплика** — text в ответе LLM вместе с tool call ("сейчас найду..."); правила в agent.md.
- **STT provider** — интерфейс к серверу транскрипции (OpenAI-compatible API); смена = URL.
- **TTS provider** — абстракция синтеза речи; смена = адаптер.
- **LLM proxy** — LiteLLM; единый OpenAI API, LangSmith tracing, доступ другим агентам.
- **Delegation gateway** — компонент, маршрутизирующий `delegate(agent, task)` к Hermes/Pi/OpenClaw.
- **Delegate task** — асинхронная задача делегированная агенту; статусы стримятся, результат re-enters conversation.
- **Session** — период общения с агентом; история + tool calls + контекст в SQLite.
- **Session store** — SQLite хранилище сессий (паттерн Hermes state.db) + JSONL экспорт.
- **SOUL.md / user.md / agent.md** — файлы инструкций, hot-reload, вставляются в system prompt.
- **FastRTC** — Python WebRTC библиотека (transport + VAD + turn taking), тонкая альтернатива LiveKit.
- **Control channel** — WebSocket alongside FastRTC для tool статусов, state changes, metadata.