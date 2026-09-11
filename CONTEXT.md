---
tags:
  - voice-agent
  - v3
  - context
  - glossary
---

# VOCA v3 — Domain Glossary

Термины будущего голосового runtime и реализуемой VOCA-13 session-основы; её `backend/`/`frontend/` границы описаны в [code architecture](docs/code-architecture.md). Точный текущий subset описан в [code architecture](docs/code-architecture.md); будущие контракты — в [architecture](docs/architecture.md), порядок — в [roadmap](docs/roadmap.md).

- **Session** — в VOCA-13 durable SQLite запись с уникальным `session_id` и cursor; будущая реализация расширит её историей разговора, turns, инструкциями, tool calls и tasks. Session живёт дольше соединения или включённого микрофона.
- **Connection** — работоспособность WebRTC и control WS: `connected` или `disconnected`; потеря любого канала инвалидирует активный ответ.
- **Conversation** — режим приёма речи внутри session: `off` или `listening`. Кнопка переключает режим без удержания; `listening` сохраняется и пока агент отвечает. Reconnect восстанавливает данные, не включает микрофон.
- **Conversation runtime** — единственный владелец активного turn, context и очереди событий внутри одного Python/FastAPI backend.
- **Voice turn** — захват пользовательской реплики и ответ; состояния `capturing`, `transcribing`, `generating`, `speaking`, затем `completed`, `interrupted` или `failed`. `speaking` может перекрываться с LLM/TTS generation.
- **Result turn** — отдельный turn для пользовательской реплики о результате task; ждёт паузы включённой исходной session, уступает пользовательской речи. Это не чтение сырого tool/RPC payload.
- **Generation** — идентичность исполнения ответа (`generation_id`) для cancellation и отсечения поздних callbacks/audio. Инвалидация поколения не отменяет связанные durable tasks.
- **Barge-in** — обнаружение начала новой речи, отменяющее текущий voice/result ответ немедленно на speech onset, не только после паузы. Новый capture не ждёт старый inference.
- **TTS cancel** — отмена synthesis stream вместе с invalidation текущего ответа и очисткой backend audio queue. Отмена запроса не доказывает освобождение GPU и сама не очищает browser.
- **Playback flush** — прекращение уже запланированного/буферизованного старого звука на клиенте, включая downstream WebRTC/playback boundary; `playback.clear` / `playback.cleared` коррелируются со старым generation. Реализация FastRTC должна пройти H1, готовый interrupt callback не предполагается.
- **Playback facts** — отдельно generated text, `not_started`, `started`, подтверждённое `finished` либо неизвестная слышимость. Прерванный ответ не считается полностью услышанным.
- **AEC** — acoustic echo cancellation, запрашиваемый у браузера механизм; наличие флага не гарантирует отсутствие эха и ложных turns.
- **VAD / turn detection** — определение начала и окончания речи; базовый режим кнопка + VAD. Semantic turn detection — последующий проверяемый кандидат для различения пауз внутри реплики и её конца.
- **Semantic activation** — отложенный always-on эксперимент: определить, адресована ли речь ассистенту. До проверки false/missed activation и latency не заменяет кнопку.
- **LLM loop** — custom context → LLM → разделённые content/reasoning/tool calls → валидация/dispatch → tool result/context цикл; не модельный Pythonic/XML parser.
- **Normalized tool call** — `id`, `function.name`, полная JSON `function.arguments`; serving layer llama.cpp нормализует модельную форму, loop проверяет имя/schema/доступность и дедуплицирует до dispatch.
- **Промежуточная реплика** — отдельный speakable assistant content рядом с действием («Исследую проект»). Reasoning, arguments/results, RPC events и служебные markers не являются репликой для TTS.
- **Provider** — adapter с контрактом входа/выхода, языков/форматов/streaming, окончания/cancel/timeout/error. Совместимый URL не доказывает совместимость модели или языка.
- **STT provider** — audio segment + language hint → final transcript + language; старт — локальный Whisper server с OpenAI-compatible transcription API, unified audio.cpp только эксперимент.
- **LLM provider / proxy** — локальный LiteLLM → llama.cpp/LFM2.5; separate content/reasoning/tool streams. MiniCPM — кандидат сравнения. Автоматического cloud fallback нет.
- **TTSProvider** — пользовательский текст фрагментами → audio chunks с encoding/rate/channels, старт — audio.cpp. Русский checkpoint выбирается по качеству/ресурсам, не по одному списку поддерживаемых языков.
- **Delegation adapter** — mapping `delegate(agent, task)` к протоколу подключённого агента; сначала только Pi. Hermes и OpenClaw будут независимыми adapters, не общим готовым gateway.
- **Pi instance** — выбранные пользователем executable/configuration/окружение и рабочий каталог; отдельная новая сессия наследует tools/MCP и ограничения именно этого экземпляра, не управляет его текущей беседой. Полномочия: **«Чтение, исследование и использование тулов/mcp подключенных к конкретно этому Pi инстансу»**.
- **Task** — durable поручение с `task_id`, исходной session/tool call и выбранным instance: `queued` → `running` → `succeeded`/`failed`; `unknown` — неизвестный outcome после потери процесса/restart, без автоматического повторного поручения. Task ID сохраняется до dispatch и возвращается loop сразу.
- **Pi settled** — RPC `agent_settled`, завершение всех автоматических continuations/retries; после него проверяется terminal outcome. Первый `agent_end` или prompt accepted ещё не success.
- **Result delivery** — terminal result и его постановка в conversation queue сохраняются атомарно, context insertion дедуплицируется по task ID. При выключенном разговоре ждёт включения исходной session; прерванный result turn не переозвучивается автоматически. Физическое exactly-once прослушивание не обещается.
- **Session store** — SQLite как источник истины для sessions/messages/turns/snapshots/tool calls/tasks/deliveries/events. JSONL — экспорт, не второй источник истины.
- **Instruction snapshot / reload** — неизменяемый набор SOUL.md → user.md → agent.md в начале turn; reload действует со следующего. При ошибке остаётся last valid set с видимой ошибкой; без начального valid set start запрещён.
- **Control channel** — отдельный WebSocket для управления, states, transcript, task/result metadata и playback acknowledgements; не audio и не TTS text.
- **Event cursor** — server `event_id`, уникальный упорядоченный cursor внутри session; resume передаёт последний применённый ID, authoritative snapshot восстанавливает данные без повторного UI append/dispatch. Client command/ack IDs не двигают server cursor.
- **Observability** — полная доступная связанная trace voice → task → result с IDs, timing, полезным redacted содержимым и errors. Обязателен локальный self-hosted LangSmith с этапа 1; LiteLLM callback покрывает лишь LLM участок. Credentials удаляются до экспорта; license egress продукта не равен cloud trace fallback.
- **Gate** — проверка с заранее заданным сценарием/критерием и решением при провале. 500 мс — latency-цель отдельно от функциональной приёмки; неизвестные license/resource/browser flush не выдаются за пройденные gates.
