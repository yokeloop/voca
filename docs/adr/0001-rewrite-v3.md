---
tags:
  - voice-agent
  - v3
  - adr
status: accepted
---

# ADR-0001: Voice Agent v3 — clean rewrite

## Status

Accepted (2026-09-02)

> **Историческое решение rewrite.** Формулировки и порядок фаз ниже сохранены как свидетельство grill 2026-09-02, не как текущая спецификация реализации. Уточнения lifecycle, Pi-first, полномочий, локального LangSmith и порядка этапов: [ADR-0002 — VOCA-2](0002-conversation-lifecycle-and-roadmap.md); действующие контракты — в [архитектуре](../architecture.md) и [roadmap](../roadmap.md).

## Context

Voice Agent v2 (9 слайсов, 12 ADRs, full architecture doc) работает плохо: код плохо организован, over-engineering (куча контрактов, версионирования, бюрократии), стек не гибкий (зафиксированные модели, нет tool calling), куча бесполезных тестов, баги. Паша принял решение создать v3 с нуля.

Требования к v3:
- Тонкий кастомизируемый пайплайн
- Максимально близко к реалтайм общению
- Лёгкая замена моделей (LLM/STT/TTS) — эксперименты
- S2S по модели STT → LLM with tooling → TTS
- LFM-2.5 как основная LLM (быстрая)
- Делегирование задач агентам (Hermes/Pi/OpenClaw) через custom gateway
- Barge-in, wake word (позже), echo cancellation
- Сессии с историей и контекстом общения + тулколинга
- SOUL.md/user.md/agent.md инструкции
- UI новый с нуля (после бэкенда)

## Decision

Создать v3 как clean rewrite. Ключевые решения:

1. **FastRTC** (не LiveKit) — тонкий Python WebRTC transport. Причины: не нужен Go SFU для 1-пользователя, не диктует архитектуру (custom LLM loop), MIT лицензия, AEC через браузерный WebRTC. Принятые минусы: barge-in TTS cancel руками, нет session model (свой SQLite), control events через отдельный WebSocket.

2. **LiteLLM proxy** для LLM — tracing через LangSmith, отдельные тесты LLM, доступ к модели другим агентам. Смена модели = конфиг.

3. **audio.cpp** для TTS (TTSProvider абстракция). Возможно STT/VAD тоже — hypothesis на реализацию. STT fallback: Whisper как сервер с OpenAI-compatible API.

4. **Custom LLM loop** в Python пайплайне — не framework. Гибрид: text = говорить, tool call = действовать, промежуточные реплики возможны.

5. **Единый delegate tool** — `delegate(agent, task)`, LLM выбирает агента параметром, маршрутизация в реализации tool. Async streaming статусов.

6. **SQLite** для сессий (паттерн Hermes state.db) + JSONL экспорт.

7. **Фазы**: MVP pipeline → agent capabilities → realtime polish → UI → R&D (always-on activation, fine-tuning).

8. **Deployment**: прямой запуск, systemd, monorepo.

## Consequences

- Положительные: тонкий пайплайн, полный контроль над loop, лёгкая замена моделей, независимость от LiveKit ecosystem
- Отрицательные: barge-in TTS cancel — ручной код; FastRTC молодой (0.0.x), API может меняться; нет production war stories у FastRTC
- R&D области: always-on semantic activation, fine-tuning LFM-2.5, audio.cpp для всего аудио

## References

- Grill session 2026-09-02 (index.md)
- FastRTC: https://github.com/gradio-app/fastrtc (MIT, 4.6k stars)
- LiteLLM, LangSmith, audio.cpp (0xShug0), LFM-2.5