---
tags:
  - voice-agent
  - v3
  - grill
  - architecture
  - index
---

# Voice Agent v3 — Grill Results

> **Date:** 2026-09-02
> **Mode:** Deep-architecture grill (rewrite from scratch)
> **Predecessor:** [[voice-agent-v2-architecture-and-tts-adapters]] (v2, superseded)

## Context

Паша не доволен архитектурой voice-agent-v2: код плохо организован, over-engineering, стек не гибкий, куча бесполезных тестов, баги, всё работает плохо. Решение — создать v3 с нуля.

Цель: тонкий кастомизируемый голосовой ассистент с навыком делегации задач на более продвинутых агентов, максимально близкий к реалтайм общению.

## Decision Summary

| # | Решение | Выбор |
|---|---------|-------|
| 1 | Причина rewrite | Over-engineering + не хватает фич + стек не подходит |
| 2 | audio.cpp | Только TTS (STT/VAD возможно тоже, решить на реализации) |
| 3 | STT | Whisper как сервер с OpenAI-compatible API |
| 4 | LLM proxy | LiteLLM (tracing через LangSmith, доступ другим агентам) |
| 5 | TTS интерфейс | TTSProvider абстракция, audio.cpp server под капотом |
| 6 | Transport | ~~LiveKit~~ → FastRTC (тонкий, без Go SFU, MIT) |
| 7 | Echo cancellation | WebRTC AEC (браузерный, встроенный) |
| 8 | LLM loop | Custom Python loop в пайплайне (не framework) |
| 9 | Delegation protocol | Async streaming (WebSocket/SSE), статусы + промежуточные шаги |
| 10 | Wake/activation Phase 1 | Push-to-talk (кнопка в UI) + VAD turn detection внутри сессии |
| 11 | Wake/activation R&D | Always-on semantic activation (VAD → лёгкий STT → LLM gate) |
| 12 | Session store | SQLite (+ JSONL экспорт), паттерн Hermes state.db |
| 13 | SOUL/user/agent | Конфиг + файлы с hot-reload |
| 14 | UI | Новый с нуля, ПОСЛЕ бэкенда и пайплайна |
| 15 | Язык бэкенда | Python |
| 16 | "Говорить или действовать" | Гибрид: text = говорить, tool call = действовать, промежуточные реплики возможны |
| 17 | Delegate tool | Единый `delegate(agent, task)`, LLM выбирает агента параметром |
| 18 | Fine-tuning | По фазам: prompts → tool calling → conversation behavior |
| 19 | Deployment | Прямой запуск, systemd |
| 20 | Структура | Monorepo |

## Компоненты системы

1. **Audio transport** — FastRTC (WebRTC, VAD, barge-in detection)
2. **STT** — Whisper server (OpenAI-compatible API; бэкенд: audio.cpp или whisper.cpp — решить на реализации)
3. **LLM proxy** — LiteLLM (tracing LangSmith, единый OpenAI API для моделей)
4. **LLM loop** — custom Python loop (tool calling, "говорить или действовать", session context)
5. **TTS** — audio.cpp server (TTSProvider абстракция, легко заменяем)
6. **Delegation gateway** — единый `delegate(agent, task)` tool, async streaming статусов
7. **Session store** — SQLite (history, tool calls, context) + JSONL экспорт
8. **SOUL/user/agent** — config + файлы с hot-reload
9. **UI** — новый, с нуля, после бэкенда
10. **Deployment** — systemd, прямой запуск на хосте

## Фазы

**Phase 1 — MVP pipeline (голос работает)**
- FastRTC transport + VAD + push-to-talk (кнопка)
- STT → LLM → TTS chain (Whisper + LFM-2.5 через LiteLLM + audio.cpp)
- SQLite sessions
- SOUL/user/agent загрузка с hot-reload

**Phase 2 — Agent capabilities (агент действует)**
- LLM loop с tool calling (гибрид text/tool)
- Delegation gateway (Hermes/Pi/OpenClaw)
- Async streaming статусов и промежуточных шагов

**Phase 3 — Realtime polish (естественное общение)**
- Barge-in TTS cancel (ручная реализация поверх FastRTC interrupt)
- Semantic turn detection
- Echo cancellation tuning (AEC калибровка, greeting-first)

**Phase 4 — UI**
- Новый UI с нуля (не перенос v2)
- Avatar, panels, controls — после готового пайплайна

**Phase 5 — R&D**
- Always-on semantic activation (без кнопки): VAD → лёгкий STT → LLM-классификатор "обращаются ли к агенту"
- Fine-tuning LFM-2.5 (tool calling → conversation behavior)
- TTS/STT эксперименты (замена моделей через абстракции)

## Key Architecture Points

### FastRTC вместо LiveKit
- Причины: тонкий Python (не Go SFU), не диктует архитектуру, MIT, AEC через браузерный WebRTC
- Известные минусы: barge-in TTS cancel руками (issue #341), нет session model, latency оверхед vs raw WebSocket
- Data channels для control events — отдельный WebSocket alongside

### Custom LLM loop
- Пайплайн сам управляет: отправка контекста → парсинг tool calls → dispatch → результат обратно → решение "говорить или действовать"
- LFM-2.5 tool call формат парсится в loop
- Промежуточные реплики ("сейчас найду...") — text в ответе с tool call

### Delegation
- Единый tool: `delegate(agent, task)` — LLM выбирает agent параметром
- Gateway маршрутизирует к Hermes/Pi/OpenClaw по параметру
- Async streaming: task ID → статусы (started/working/step/done) → результат re-enters conversation
- Паттерн: delegate_task(background=true) из Hermes

### Session контекст
- SQLite: сессии, messages, tool calls, context
- При reconnect — загрузка из БД
- FTS5 поиск по истории (опционально)

### Model swapping
- LLM: LiteLLM proxy — смена модели = конфиг
- STT: OpenAI-compatible API — смена бэкенда = URL
- TTS: TTSProvider абстракция — смена = новый адаптер

## Hypotheses (проверить на реализации)

1. audio.cpp может покрыть STT + VAD + TTS (вместо отдельных whisper.cpp)
2. FastRTC latency достаточна для realtime (~500ms voice-to-voice target)
3. Barge-in TTS cancel реализуем поверх FastRTC interrupt callback
4. Always-on semantic activation через лёгкий STT + LLM gate (~200-400ms)
5. LFM-2.5 с промптами достаточно для Phase 1-2 (fine-tune нужен к Phase 5)

## Deferred

- Proactivity layer (initiative, suggestions, cron) — из [[grill-proactive-agent-results]] v2, не в скоупе v3 MVP
- Multi-user / multi-session concurrently
- Kiosk mode
- Telephony (FastRTC fastphone)

## Связанные доки

- [[CONTEXT]] — глоссарий
- [[architecture]] — подробная архитектура (docs/architecture.md в репо)
- [[ADR-0001-rewrite-v3]] — решение о rewrite