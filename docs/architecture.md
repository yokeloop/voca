---
tags:
  - voice-agent
  - v3
  - architecture
  - spec
---

# Voice Agent v3 — Architecture Spec

> **Status:** Working document (post-grill 2026-09-02)
> **Goal:** тонкий кастомизируемый голосовой ассистент с делегацией задач на агентов, близкий к реалтайм общению

## 1. System Overview

```
[Browser UI]
    ↕ WebRTC (FastRTC) + WebSocket (control events)
[Python Backend (FastAPI)]
    ├── FastRTC Stream (audio in/out, VAD, turn detection)
    ├── LLM Loop (custom)
    │     ├── STT client → Whisper server / audio.cpp (OpenAI-compatible API)
    │     ├── LLM client → LiteLLM proxy → LFM-2.5 (llama.cpp / cloud)
    │     └── TTS client → audio.cpp server (TTSProvider)
    ├── Delegation Gateway
    │     └── delegate(agent, task) → Hermes / Pi / OpenClaw (async streaming)
    ├── Session Store (SQLite + JSONL export)
    └── Config (SOUL.md / user.md / agent.md, hot-reload)
```

## 2. Components

### 2.1 Audio Transport — FastRTC

- WebRTC connection браузер ↔ backend
- `getUserMedia({ audio: { echoCancellation: true }})` — браузерный AEC
- Built-in Silero VAD + turn taking (`ReplyOnPause`, настраиваемые thresholds)
- `can_interrupt=True` — barge-in detection
- **Ручная работа:** TTS cancel при barge-in (остановить стрим, очистить буфер) — коллбек на interrupt
- **Control events:** отдельный WebSocket alongside FastRTC (tool статусы, state changes, metadata)
- **Activation Phase 1:** push-to-talk кнопка в UI
- Известный latency оверхед vs raw WebSocket — принять, WebRTC даёт AEC

### 2.2 STT

- Интерфейс: OpenAI-compatible `/v1/audio/transcriptions` endpoint
- Бэкенд (hypothesis, решить на реализации):
  - audio.cpp ASR (если качество/скорость достаточны)
  - whisper.cpp server (`/inference` + обёртка) или faster-whisper-server
- Смена STT = смена URL в конфиге

### 2.3 LLM — LiteLLM proxy

- LiteLLM между пайплайном и моделью
- LFM-2.5 (Q4_K_M на llama.cpp GPU) — основная, быстрый инферс
- LangSmith tracing всех запросов
- Отдельные LLM тесты (eval)
- Доступ к модели другим агентам через тот же endpoint
- Смена модели = конфиг (без изменения пайплайна)

### 2.4 LLM Loop (custom, Python)

Ядро пайплайна. Пайплайн сам управляет:

```
loop:
  1. Собрать context (system prompt = SOUL + user + agent, history, tool results)
  2. Отправить в LLM (через LiteLLM)
  3. Парсинг ответа:
     - text → отправить в TTS (говорить)
     - tool calls → dispatch (действовать)
     - text + tool calls → text = промежуточная реплика, tool = действие
  4. Tool result → обратно в context → goto 2
  5. Финальный text (без tool calls) → TTS → конец turn
```

- Правила "когда говорить промежуточно, когда молчать" — в agent.md
- LFM-2.5 tool call формат парсится в loop
- Context compression (если нужно) — Phase 3+

### 2.5 TTS — audio.cpp server

- TTSProvider абстракция в пайплайне
- audio.cpp server под капотом (supertonic, vibevoice, voxcpm2, qwen3_tts, fish_audio и др. — экспериментировать)
- Смена TTS = новый адаптер, пайплайн не трогается

### 2.6 Delegation Gateway

- Единый tool: `delegate(agent, task)`
  - `agent`: "hermes" | "pi" | "openclaw" — LLM выбирает параметром (правила в agent.md)
  - `task`: описание задачи
- Gateway маршрутизирует по параметру `agent`
- **Протокол:** async streaming
  - Вызов → task ID сразу
  - Gateway пушит статусы: started → working → step completed → done/failed
  - WebSocket/SSE, промежуточные шаги стримятся
  - Результат re-enters conversation как новый turn
- LLM loop решает: сказать "работаю над этим" (голосом) и ждать, или молчать
- Паттерн: `delegate_task(background=true)` из Hermes

### 2.7 Session Store — SQLite

- Паттерн: Hermes `state.db` (SQLite + FTS5)
- Схема: sessions, messages (user/assistant/tool), tool_calls, context snapshots
- При reconnect — загрузка контекста из БД
- JSONL экспорт (human-readable backup)
- Поиск по истории (FTS5, опционально)

### 2.8 Инструкции — SOUL.md / user.md / agent.md

- Файлы лежат в config directory
- Пути в конфиге
- **Hot-reload** при изменении (правка → сразу тест, без перезапуска)
- Вставляются в system prompt

### 2.9 UI — новый с нуля

- **После** готового пайплайна и бэкенда
- Phase 1: минимальный (кнопка активации, transcript, статус)
- Дальше: avatar, panels, controls — дизайн с нуля, НЕ перенос v2

## 3. Phases

| Phase | Скоуп | Выход |
|-------|-------|-------|
| 1 — MVP pipeline | FastRTC + push-to-talk, STT→LLM→TTS, LiteLLM, SQLite, SOUL/user/agent | Голосовой разговор работает |
| 2 — Agent capabilities | LLM loop + tool calling, delegation gateway, async статусы | Агент делегирует и докладывает |
| 3 — Realtime polish | Barge-in TTS cancel, semantic turn detection, AEC tuning | Естественное общение |
| 4 — UI | Новый UI с нуля | Полный продукт |
| 5 — R&D | Always-on activation, fine-tuning LFM-2.5, STT/TTS эксперименты | Без кнопки, tuned модель |

## 4. Hypotheses (gate на реализации)

1. audio.cpp покрывает STT + VAD + TTS одним сервером (vs отдельный whisper)
2. FastRTC latency ≤ ~500ms voice-to-voice
3. Barge-in TTS cancel реализуем поверх FastRTC interrupt callback
4. Always-on semantic activation: VAD → лёгкий STT → LLM gate (~200-400ms)
5. LFM-2.5 + prompts достаточно для Phase 1-2

## 5. Deployment

- Прямой запуск на хосте (не Docker — минимум слоёв для latency)
- systemd services: backend, audio.cpp, LiteLLM
- Monorepo: `src/` (backend), `web/` (UI), `docs/`, `config/`
- PC: i5-10600KF, RTX 4070, 32GB (canonical host из v2)

## 6. Out of scope (v3 MVP)

- Proactivity layer (deferred)
- Multi-user
- Kiosk
- Telephony
- Cloud fallback (только явный конфиг, как в v2 принципах)