---
tags:
  - voice-agent
  - v3
  - grill
  - architecture
  - index
---

# VOCA v3 — Documentation and Grill Provenance

> **Historical date:** 2026-09-02
> **Mode:** Deep-architecture grill (rewrite from scratch)
> **Predecessor:** `[[voice-agent-v2-architecture-and-tts-adapters]]` — историческая Obsidian-ссылка из внешней базы, **не файл этого репозитория**.

## Current specification

[VOCA-13](https://github.com/yokeloop/voca/issues/13) adds a small working session/control foundation. The voice stack remains planned: VOCA-2 supplies its contracts and roadmap, while VOCA-13 does not claim runtime inference or experiments.

| Документ | Что читать |
|---|---|
| [README](../README.md) | Краткий статус и стартовый стек |
| [Code architecture](code-architecture.md) | Implemented session/control modules, owners and dependency direction |
| [Testing](testing.md) | Risk matrix and repeatable foundation checks |
| [Diagnostics](diagnostics.md) | Safe local JSONL policy and future diagnostic map |
| [Architecture](architecture.md) | Future voice/provider boundaries, lifecycle/control/Pi/delivery, LangSmith preflight, hypotheses H1–H7 |
| [Roadmap](roadmap.md) | Разговор → контекст/инструкции → Pi → остальные возможности; gates и приёмка |
| [Glossary](../CONTEXT.md) | Единые определения conversation/session, turn/generation, task/playback |
| [ADR-0002](adr/0002-conversation-lifecycle-and-roadmap.md) | Accepted documentary VOCA-2 decisions |
| [ADR-0003](adr/0003-code-and-test-foundation.md) | Agreed foundation decision, executable result pending review |
| [MiniCPM research](research/minicpm5-2b.md) | Официальный GGUF, пределы benchmark evidence и будущий протокол сравнения |
| [ADR-0001](adr/0001-rewrite-v3.md) | Историческое решение rewrite от 2026-09-02 |

Актуальная архитектура: один Python/FastAPI runtime, browser WebRTC/FastRTC плюс control WS, локальный Whisper server → LiteLLM/llama.cpp/LFM2.5 → audio.cpp/TTSProvider. Кнопка включает разговор без удержания, VAD разделяет реплики. Minimal UI, реальный browser flush и self-hosted LangSmith на той же машине входят уже в первый голосовой этап. Следом история/reload, затем Pi-first с отдельной сессией выбранного экземпляра, durable tasks и результатом в исходную session. Stop/barge-in не отменяют поручения, reconnect не включает микрофон, unknown не повторяет dispatch. MiniCPM и unified audio.cpp не prerequisite.

Развёртывание LangSmith и совместная память **не доказаны**: Enterprise entitlement/image access и single-host/resource условия требуют проверки; официальные рекомендации выше целевого бюджета 32 GB RAM. Около 500 мс — цель end-of-speech → first audible, не гарантия. Все источники/границы подтверждения — в архитектуре.

## Historical grill summary — not the current implementation contract

На grill 2026-09-02 инженер решил начать clean rewrite voice-agent-v2: прежняя архитектура считалась плохо организованной, перегруженной и недостаточно гибкой. Цель сохранилась: тонкий кастомизируемый голосовой ассистент, близкий к realtime, с делегацией более сильным агентам.

Grill выбрал Python/custom loop, FastRTC вместо LiveKit, LiteLLM, Whisper-compatible STT, audio.cpp/TTSProvider, SQLite/JSONL и SOUL/user/agent. Обсуждались делегация Hermes/Pi/OpenClaw, systemd, monorepo и новый UI вместо переноса v2. Исторический список не доказывал совместимость, русский голос, cancellation, realtime или достаточность памяти.

Уточнения, которые **заменяют старые текущие рекомендации**, а не переписывают дату решения:

| Историческая формулировка / пробел | Действующий контракт |
|---|---|
| «Push-to-talk» и UI после backend | Переключатель без удержания, VAD и минимальный UI в этапе 1: [conversation lifecycle](architecture.md#conversation-lifecycle) |
| TTS cancel «поверх interrupt callback», interrupt в polish | Callback/browser flush не доказан, speech-onset invalidation и client boundary — ранний [H1](architecture.md#h1--browser-barge-in) |
| «LFM формат парсится в loop», «text = говорить» | Serving layer нормализует tools; loop валидирует; только speakable content в TTS: [loop](architecture.md#custom-llm-loop) |
| «Смена STT = URL», «смена LLM = конфиг» | Язык, streaming, cancel и модельные formats проверяются по [provider contracts](architecture.md#provider-contracts) |
| Единый gateway на три агента | Сначала выбранный Pi со своими tools/MCP/ограничениями; отдельные последующие adapters: [delegation](architecture.md#delegation) |
| SQLite history без outcome/restart контракта | Durable task/delivery, unknown и dedup: [task lifecycle](architecture.md#task-lifecycle) |
| Прямой запуск всех компонентов | Приложение отдельно от официальных требований LangSmith: [observability](architecture.md#observability-and-self-hosted-langsmith) |
| Фазы MVP → capabilities → polish → UI → R&D | Действует [roadmap](roadmap.md); fine-tuning/always-on только по будущим проверкам |

## Deferred provenance

Multi-user, kiosk, telephony и proactivity не входят в ранний VOCA. `[[grill-proactive-agent-results]]` — ещё одна историческая внешняя Obsidian-ссылка, не локальный документ. Always-on и прежняя оценка 200–400 мс, semantic turn detection и необходимость fine-tuning не доказаны: правила допуска находятся в [H6](architecture.md#h6--deferred-semantic-activation-turn-detection-and-fine-tuning).
