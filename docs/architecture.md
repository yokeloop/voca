---
tags:
  - voice-agent
  - v3
  - architecture
  - spec
---

# VOCA v3 — Architecture

> Спецификация будущей реализации, не описание работающей установки. VOCA-2 — только документация; runtime, inference и измерения не выполнялись.

## System Overview

**Решено:** один Python/FastAPI backend, один пользователь и одна активная голосовая беседа; локальные inference endpoints без автоматического cloud fallback. **Подтверждено источником** означает только наличие описанного API/поведения в указанной версии, не работоспособность VOCA. **Не измерено на целевом хосте:** качество русского, cancellation/browser flush, latency, совместная память и размещение LangSmith.

```text
Browser: microphone + AEC request + playback + minimal UI
  | WebRTC audio                         | WebSocket control/status
  v                                      v
One Python/FastAPI backend
  Audio transport (FastRTC, VAD) <--> Conversation runtime
                                      | sole active turn/context/event queue owner
                                      | custom LLM loop
                                      +--> STT adapter --> local Whisper server
                                      +--> LLM adapter --> local LiteLLM --> llama.cpp / LFM2.5
                                      +--> TTSProvider --> local audio.cpp
                                      |                     |
  WebRTC audio <-- backend audio queue <---------------------+
                                      +--> delegation adapter --> selected Pi instance
                                      +--> SQLite store
                                      +--> redacted trace queue --> local self-hosted LangSmith
Local LiteLLM ------------------------------------------------> same local LangSmith
```

Inference servers, LiteLLM и LangSmith — обслуживающие процессы, не дополнительные бизнес-бэкенды. SQLite хранит состояние VOCA; базы LangSmith обслуживают tracing, не runtime. Отдельный брокер, workflow engine и общий агентский gateway не вводятся.

## Components

### Audio transport and activation

FastRTC — стартовый WebRTC transport. Browser запрашивает `getUserMedia({audio:{echoCancellation:true}})`; это запрос AEC, не гарантия отсутствия эха. VAD разделяет реплики. Кнопка включает/выключает разговор, удерживать её не нужно. Минимальный UI входит в первый этап: переключатель, transcript/assistant text, состояние соединения и разговора, видимая ошибка. Control/status идут по отдельному WebSocket, не внутри текста LLM и не через аудио.

Transport передаёт runtime аудиофрагменты с format/sample rate, начало/конец речи и ошибки соединения. Runtime определяет turn и поколение ответа, владеет очередями и отменой; transport исполняет его команды. Реальный browser flush FastRTC ещё должен пройти раннюю проверку.

### Provider contracts

Все вызовы коррелируются с `session_id`, `turn_id`, `generation_id`. Адаптер объявляет capabilities: языки, форматы/частоты, streaming, cancellation и особенности модели; неподдерживаемая возможность — явная ошибка до dispatch. URL-совместимость не означает совместимость языка, модели или streaming. Никакой автоматической облачной подмены при ошибке.

| Граница / владелец адаптации | Вход | Выход в runtime | Завершение, отмена и ошибка |
|---|---|---|---|
| STT adapter → Whisper server, OpenAI-compatible transcription API | Завершённый аудиосегмент, format/sample rate, language hint `ru` | Итоговый transcript и язык (распознанный либо явно заданный), metadata запроса | Один final либо timeout/error, пустой результат не выдумывается. Cancel закрывает запрос; поздний final старого поколения отбрасывается |
| LLM adapter → LiteLLM → локальный llama.cpp / LFM2.5 | Instruction snapshot, история, сообщения, schemas доступных tools | Раздельные потоки assistant content, reasoning, нормализованные `tool_calls`; finish reason | EOF с валидным finish либо timeout/error; cancel upstream и отсечение всех поздних deltas |
| TTSProvider → audio.cpp | Только пользовательский текст фрагментами, язык и выбранный voice/model | Последовательные audio chunks с encoding, sample rate, channels и признаком конца | Конец текста/аудио, cancel стрима, timeout/error; runtime очищает очередь, отсекает поздние chunks |
| Audio transport → Browser playback | Audio chunks активного поколения, согласованный playback boundary | Первый playback и подтверждение завершения/очистки | Сбой транспорта инвалидирует ответ; backend EOF не означает, что пользователь всё услышал |

Стартовый STT — Whisper server с OpenAI-compatible transcription API (не обещание, что голый whisper.cpp `/inference` уже совместим). Стартовый LLM — LFM2.5 в llama.cpp через LiteLLM. Стартовый TTS — audio.cpp через `TTSProvider`; пригодный русский checkpoint ещё не выбран по измерениям. Unified STT/TTS audio.cpp — отдельный эксперимент, не prerequisite. MiniCPM5-2B — кандидат сравнения, не выбранная замена.

Каждый адаптер до интеграции фиксирует конечные request/idle timeout и поведение при cancel, включая случай, когда сервер физически продолжает inference. Runtime никогда не ждёт освобождения старого inference для захвата следующей речи. Cancel запроса не выдаётся за подтверждённое освобождение GPU.

### Custom LLM loop

Runtime собирает context → вызывает LLM → принимает типизированные deltas → произносит только speakable assistant content → проверяет complete tool calls → dispatch → добавляет tool result в context → продолжает при необходимости. Для `delegate` немедленный tool result — durable task ID, не ожидание внешнего агента.

Контракт tool call: `{"id":"call-1","function":{"name":"delegate","arguments":"{\"agent\":\"pi\",\"task\":\"Исследуй структуру проекта\"}"}}`. `function.arguments` — полная JSON-строка, разбираемая в объект по schema. Loop проверяет уникальный ID, известное имя, типы/обязательные поля и доступного агента **до** dispatch. Неполный, невалидный или неизвестный вызов не исполняется: фиксируется tool validation error. Повтор ID возвращает прежний результат dispatch.

Pythonic LFM и XML MiniCPM разбирает serving layer llama.cpp; loop не получает новый модельный парсер. Нормализация должна быть доказана для выбранных версий server/template/model. Reasoning, tool arguments/results, RPC events и служебные маркеры никогда не направляются в TTS. Assistant content рядом с tool call допускается только как отдельная пользовательская промежуточная реплика, например «Исследую проект»; это не озвучивание сырого stream. Правила speakable content задаёт `agent.md`, а типизированная граница TTS удерживает изоляцию независимо от промпта.

## Deployment

Приложение предполагает прямой запуск на хосте; systemd возможен при будущей реализации. Это не обещание «всё без контейнеров»: официальный способ установки self-hosted LangSmith проверяется отдельно. Целевой бюджет: i5-10600KF, RTX 4070 **12 GB VRAM**, около **32 GB RAM** на ОС, runtime, одновременно резидентные STT/LLM/TTS и LangSmith вместе. Достаточность бюджета не установлена.

## Out of scope

VOCA-2 не создаёт runtime, UI, migrations, configs, services или тестовую инфраструктуру; не запускает эксперименты и не устанавливает модели/LangSmith. Ранние этапы v3 исключают multi-user, telephony, kiosk и proactivity. Cloud inference/fallback не входит в выбранную цепочку. Режим always-on, дополнительные агенты и замена моделей не prerequisite базового разговора.
