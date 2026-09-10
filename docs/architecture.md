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

## Conversation lifecycle

Connection (`connected`/`disconnected`), conversation (`off`/`listening`) и turn — разные оси. `connected` требует работоспособности WebRTC и control WS; потеря любого канала означает `disconnected`. `listening` означает включённый разговор, в том числе пока агент отвечает. Один активный turn: `capturing` → `transcribing` → `generating` → `speaking` → `completed`, либо `interrupted`/`failed`. LLM и TTS могут работать одновременно: `speaking` не означает конец генерации. Для ответа без звука допустим `generating` → `completed` с `playback=not_started`.

| Переход / trigger | Владелец и ресурсы | Наблюдаемый результат |
|---|---|---|
| `disconnected/off` → `connected/off`: кнопка, доступ к микрофону и оба транспорта готовы | Browser получает media permission, устанавливает каналы, до подтверждения start не посылает речь | UI соединён; отправляет `conversation.start` |
| `off` → `listening`: start и валидные инструкции | Runtime разрешает capture; browser включает track | `conversation.state(listening)` |
| Permission denied / невалидный начальный набор инструкций | Browser освобождает track; runtime не начинает разговор | `off`, видимый `error(microphone/permission_denied)` или `error(instructions/invalid)` |
| Нет активного turn → `capturing`: VAD speech onset | Runtime создаёт turn/generation, фиксирует instruction snapshot, transport буферизует новую речь | `turn.state(capturing)` |
| `capturing` → `transcribing`: VAD speech end | Runtime завершает входной сегмент, STT adapter получает аудио | `turn.state(transcribing)` |
| `transcribing` → `generating`: STT final | Runtime сохраняет transcript, собирает context, открывает LLM stream | `transcript.final`, `turn.state(generating)` |
| `generating` → `speaking`: speakable text → TTS chunks → первый слышимый звук | Runtime ставит chunks в audio queue; browser подтверждает начало playback | Assistant text в UI, `playback.started`, `turn.state(speaking)` |
| `speaking` → `completed`: LLM и TTS закончены, playback подтверждён | Runtime закрывает streams, фиксирует terminal turn; capture остаётся доступен | `playback.finished`, `turn.state(completed)`; conversation `listening` |
| Активный turn → `interrupted`: новый speech onset | Runtime инвалидирует старое поколение, выполняет cancellation/flush ниже; сразу создаёт новый `capturing` | `turn.state(interrupted)`, `playback.clear`, затем `turn.state(capturing)` |
| `listening` → `off`: stop кнопкой | Browser сразу прекращает capture/playback; runtime прерывает активный turn, отменяет STT/LLM/TTS и очищает очереди | `conversation.state(off)`, при активном ответе `turn.state(interrupted)` и `playback.clear` |
| `connected` → `disconnected`: потеря WebRTC/WS | Browser локально останавливает звук/микрофон; runtime инвалидирует ответ, закрывает streams, оставляет tasks | UI disconnected/off; terminal `interrupted` и состояние сохраняются для snapshot |
| `disconnected` → `connected/off`: reconnect + resume | Runtime восстанавливает данные; browser создаёт пустой playback, не включает capture | `session.snapshot`, `conversation.state(off)`; включение только новой кнопкой |
| Активный turn → `failed`: ошибка provider | Runtime отменяет остаток ответа, инвалидирует generation, очищает звук | `error`, `turn.state(failed)`, `playback.clear`; conversation остаётся `listening` при живом transport |

Сквозной happy path: нажать включение → permission/каналы → start/listening → произнести фразу → VAD end → STT final → transcript в SQLite/UI → context со snapshot → speakable assistant content → TTS fragments → transport → первый слышимый звук/`playback.started` → generation EOF + playback finished → completed → следующая реплика без удержания кнопки.

### Cancellation and playback boundary

Barge-in начинается на **speech onset**, не после паузы. Runtime сначала инвалидирует старый `generation_id`, затем отменяет текущие STT/LLM/TTS запросы, чистит backend audio queue и отправляет `playback.clear` со старым поколением. Новая речь захватывается немедленно, без ожидания cancel acknowledgement. Все callbacks/deltas/audio старого поколения игнорируются даже после нового turn; задачи Pi не отменяются.

Browser должен остановить уже запланированный playback и отбросить старое аудио, включая данные в WebRTC/jitter/playback buffers, а не только очистить Python generator. `playback.cleared` подтверждает именно этот boundary. Одного control-сообщения недостаточно: требуется доказанная привязка media к generation/границе и flush downstream, либо пересоздание соответствующего output transport без потери нового input. Как это реализовать в FastRTC, **ещё не доказано**; это ранний gate, не существующий interrupt callback. До подтверждённой чистой границы новый output не выпускается; input продолжает приниматься. Timeout flush — `error(transport/flush_timeout)` и остановка output, не возобновление старого звука. Запоздалый clear/ack одного поколения не очищает и не завершает другое.

Stop, disconnect и provider failure используют тот же boundary. Если WS уже потерян, browser сам очищает playback; runtime сохраняет invalidation, а reconnect создаёт новое пустое output-соединение. Никакое старое audio не replay-ится из истории.

### Provider failure

| Компонент / ошибка, включая timeout | Что сохраняется и чего не происходит | Завершение и восстановление |
|---|---|---|
| STT | Только факт ошибки и metadata сегмента; нет выдуманного transcript и вызова LLM | `failed`, error с component/code; cancel/flush, новая реплика после восстановления |
| LLM | Transcript и полученный assistant text с отметкой неполноты; неисполненные partial tools отбрасываются | `failed`; cancel TTS/flush, поздние deltas и звук отсекаются |
| TTS | Доступный assistant text остаётся в UI, без отметки «услышан» | `failed`; прекращение звука/flush и отмена остатка ответа, следующая реплика доступна |

Ошибки не выключают исправный микрофон и не включают cloud fallback. Если транспорт тоже упал, применяется `disconnected/off`. Завершённые tool dispatch/tasks сохраняются при любой ошибке голосового ответа.

### Instructions and history

`SOUL.md`, `user.md`, `agent.md` собираются **в этом порядке**. Runtime фиксирует неизменяемый snapshot в начале каждого turn, включая result turn; reload влияет только на следующий turn. Ошибка чтения/валидации показывает `error(instructions/reload_failed)` и оставляет последнюю валидную версию. Без начального валидного набора start отклоняется. Начальная загрузка нужна этапу 1; управляемый reload и пользовательская приёмка истории — этапу 2.

История различает сгенерированный assistant text, начало playback и подтверждённое завершение. Прерванный ответ сохраняется с `interrupted`, `playback=started` либо `not_started`, но не как целиком услышанный. Потерянный ack даёт неизвестную слышимость, не выдуманный `finished`. Context получает эти отметки. Backend EOF или выдача первого TTS chunk не доказывают ни начало, ни завершение слышимости.

## Control contract

Это спецификация сообщений, не существующий API. Везде обязательны `type`, `session_id`, `event_id`. Server `event_id` — монотонный durable cursor внутри session; client `event_id` — уникальный command/ack ID, не двигающий server cursor. Turn-сообщения требуют `turn_id`, `generation_id`; task-сообщения — `task_id` и исходный turn. Browser дедуплицирует server events по ID, применяет их по порядку и хранит последний применённый server cursor. Повтор client command ID не повторяет действие; stop идемпотентен. Для initial connection session ID выдаётся backend при установке сессии до start.

`session.resume.last_event_id` — последний подтверждённый применением в UI server event ID, `null` при первом входе. Resume не включает разговор. Backend выдаёт авторитетный snapshot с cursor, состояниями, историей, task cards и delivery status, затем события строго после cursor. Потерянный/слишком старый cursor восстанавливается snapshot, а не повторным dispatch. Повтор события с тем же ID не создаёт вторую UI-запись. Snapshot также позволяет восстановиться после restart без replay аудио.

В примерах `s1/t1/g1/k1` — условные IDs. Поля в payload обязательны; `null` разрешён только там, где показан. Для session-level error `turn_id`/`generation_id` отсутствуют; для turn error обязательны.

| Сообщение | Направление | Пример payload |
|---|---|---|
| `conversation.start` | Browser → runtime | `{"type":"conversation.start","session_id":"s1","event_id":"c1"}` |
| `conversation.stop` | Browser → runtime | `{"type":"conversation.stop","session_id":"s1","event_id":"c2"}` |
| `session.resume` | Browser → runtime | `{"type":"session.resume","session_id":"s1","event_id":"c3","last_event_id":"e8"}` |
| `session.snapshot` | Runtime → browser | `{"type":"session.snapshot","session_id":"s1","event_id":"e9","connection":"connected","conversation":"off","active_turn":null,"messages":[],"tasks":[],"deliveries":[]}` |
| `conversation.state` | Runtime → browser | `{"type":"conversation.state","session_id":"s1","event_id":"e10","state":"listening"}` |
| `turn.state` | Runtime → browser | `{"type":"turn.state","session_id":"s1","event_id":"e11","turn_id":"t1","generation_id":"g1","state":"generating"}` |
| `transcript.final` | Runtime → browser | `{"type":"transcript.final","session_id":"s1","event_id":"e12","turn_id":"t1","generation_id":"g1","language":"ru","text":"Привет"}` |
| `playback.clear` | Runtime → browser | `{"type":"playback.clear","session_id":"s1","event_id":"e13","turn_id":"t1","generation_id":"g1","reason":"barge_in"}` |
| `playback.cleared` | Browser → runtime | `{"type":"playback.cleared","session_id":"s1","event_id":"c4","turn_id":"t1","generation_id":"g1","clear_event_id":"e13"}` |
| `task.update` | Runtime → browser | `{"type":"task.update","session_id":"s1","event_id":"e14","turn_id":"t1","generation_id":"g1","task_id":"k1","state":"running","result":null}` |
| `error` | Runtime → browser (или локально в UI при потере канала) | `{"type":"error","session_id":"s1","event_id":"e15","turn_id":"t1","generation_id":"g1","component":"tts","code":"timeout","message":"Синтез недоступен"}` |

Минимальные дополнительные сообщения для наблюдаемой текстовой истории и playback acknowledgement:

| Сообщение | Направление | Пример payload |
|---|---|---|
| `assistant.text` | Runtime → browser | `{"type":"assistant.text","session_id":"s1","event_id":"e16","turn_id":"t1","generation_id":"g1","text":"Привет!","final":true}` |
| `playback.started` | Browser → runtime | `{"type":"playback.started","session_id":"s1","event_id":"c5","turn_id":"t1","generation_id":"g1"}` |
| `playback.finished` | Browser → runtime | `{"type":"playback.finished","session_id":"s1","event_id":"c6","turn_id":"t1","generation_id":"g1"}` |

`assistant.text` передаёт актуальный накопленный пользовательский текст этого turn (не append delta); `final=false` — промежуточный снимок. Snapshot messages используют тот же turn/generation/text и terminal/playback status. `playback.finished` допустим только после end-of-audio marker и фактического опустошения browser playback, не временной паузы между chunks. ACK-и становятся durable playback facts и отражаются в turn state/snapshot. Local permission/connection error показывается даже без доставки серверу и не притворяется server cursor.

## Delegation

Внешний инструмент — `delegate(agent, task)`. В первом реализуемом наборе `agent` допускает **только `pi`**; enum строится из подключённых адаптеров, а не из списка желаемых продуктов. Пользователь до поручения выбирает конкретный экземпляр Pi и рабочий каталог. Модель задаёт только задачу; текст задания не меняет instance, cwd, конфигурацию или полномочия.

Полномочия: **«Чтение, исследование и использование тулов/mcp подключенных к конкретно этому Pi инстансу»**.

Первое поручение открывает **отдельную новую сессию** выбранного Pi в выбранном каталоге, не управляет его существующей беседой. Адаптер сохраняет выбранный executable/version, config root (`PI_CODING_AGENT_DIR`), пользовательское окружение запуска по ссылке, project resources и уже установленные этим экземпляром ограничения. Наследуются его tools/MCP/extensions/settings; секреты не копируются в task payload или trace. Нельзя запускать пустой Pi с потерянным окружением, подставлять конфигурацию VOCA/другого экземпляра, добавлять общий запрет tools/MCP либо разрешать произвольные изменения. VOCA не добавляет approvals и не обходит существующие ограничения.

### Pi RPC contract and sources

Проверено документально **2026-09-10**, установленный официальный пакет `@earendil-works/pi-coding-agent` **0.85.1**: полностью прочитаны README, `docs/rpc.md` (включая Types, Error Handling и Extension UI Protocol), `docs/settings.md`, `docs/environment-variables.md`, `docs/packages.md`; просмотрен связанный `examples/rpc-extension-ui.ts`. Официальные ссылки: [RPC](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md), [settings](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/settings.md), [environment](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/environment-variables.md), [packages](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/packages.md). Ссылки main изменяемы; утверждения привязаны к прочитанному пакету 0.85.1. При реализации сверить документацию версии **выбранного** экземпляра заново. Никакой пользовательский Pi для проверки не запускался.

- Запуск выбранного Pi в `--mode rpc`, stdin/stdout — JSONL с разделителем **LF**; Unicode separators внутри JSON не разделяют records, CR перед LF допустим. stderr — отдельный канал диагностики после redaction.
- Новая сессия, без `--continue`, `--resume`, `--fork` и выбора чужого session file. `get_state` возвращает `sessionId`/`sessionFile`; адаптер сохраняет их вместе с идентификатором запуска/процесса до prompt. PID сам по себе не устойчивый ID после restart.
- RPC не показывает project trust prompt; `ask`/`never` без сохранённого доверия игнорируют project resources. Поэтому совпадение конфигурации/доверия с выбранным экземпляром — preflight gate. Если это не обеспечено, видимая ошибка до запуска поручения; нельзя «исправить» это автоматическим `--approve` или `--no-tools`.
- Отправка: `{"id":"k1:prompt","type":"prompt","message":"Исследуй структуру проекта"}`. Адаптер отображает request ID → `task_id=k1`. `response(success=true)` означает **принятие**, не окончание. `success=false` до принятия — known failed; после принятия ошибки приходят events/messages.
- Agent events обычно не содержат request ID. Отдельный процесс/сессия с одним outstanding поручением даёт однозначное process/session → task соответствие. `message_update` содержит раздельные text/thinking/toolcall deltas; `message_end.message` — авторитетный результат сборки. `tool_execution_start/update/end` коррелируются через `toolCallId`, `partialResult` — накопленный snapshot, не append delta. Доступные MCP события видны как события tools/extension, а не гарантированная внутренняя трасса MCP-сервера.
- `agent_end` завершает низкоуровневый run, после него возможны retry, compaction и queued continuations. Окончание поручения определяется **`agent_settled`**, после которого анализируются окончательные messages/error/stopReason. `agent_settled` сам по себе не означает success: `error`/`aborted` → failed, валидный финальный результат → succeeded; неоднозначное завершение без результата — видимая ошибка протокола, не пустой success. Промежуточный tool error может быть обработан агентом и не равен провалу всей задачи.
- `extension_error`, auto-retry status и tool errors сохраняются/показываются. Штатный `extension_ui_request` передаётся пользователю и ответ возвращается по его ID, если этого требует существующая конфигурация. Это отображение уже существующего взаимодействия Pi, не новый слой approvals; никакого автоматического «да». TUI-only extension без RPC-эквивалента — явный blocker совместимости выбранного instance.
- RPC `abort` отменяет операцию Pi и ждёт idle; voice barge-in/stop/disconnect **не посылают abort** и не завершают процесс. Отдельная отмена поручения не входит в первый voice-контракт.

Hermes и OpenClaw — будущие отдельные адаптеры, а не взаимозаменяемый готовый protocol: Hermes runs/events SSE/stop/approval и OpenClaw WS agent/runId/agent.wait требуют собственных mappings и проверок.

## Session Store

SQLite — единственный источник истины VOCA; JSONL — экспорт, не синхронная вторая база. Не SQL-миграция, а обязательные связи:

| Запись | Связи / уникальности |
|---|---|
| sessions | Уникальный `session_id`, текущий server event cursor |
| turns | Уникальный `turn_id`, FK session, instruction snapshot, generation и terminal state; result turn с FK task |
| instruction snapshots | Уникальный snapshot ID/version, порядок SOUL → user → agent, валидное содержимое; turn ссылается на неизменяемую версию |
| messages | FK session/turn, role/text, generated/interrupted/error и playback facts отдельно; task result уникален по task ID |
| tool calls | FK session/turn, уникальность `(session_id, tool_call_id)`, нормализованные arguments, dispatch outcome/task ID |
| tasks | Уникальный task ID, FK originating session/tool call, уникальность originating tool call; выбранный instance/cwd, process/session identity, state, result/error |
| result delivery / events | Уникальная запись результата и постановки в conversation queue по task ID; event ID уникален в session; context insertion и result turn claim отмечаются durable |

До внешнего dispatch одна транзакция сохраняет tool call, `task_id`, `queued` и немедленный tool result с этим ID. Перед возможной отправкой prompt сохраняется launch intent/`running`: crash в неопределённом окне не разрешает повторный prompt. Повтор того же tool call возвращает прежний task ID. Только очередь, о которой известно, что launch intent ещё не было, допускает первый запуск.

Терминальный task result и запись о его постановке в conversation queue сохраняются **атомарно**. Consumer добавляет результат в историю/context ровно один раз по task ID и отмечает это в той же транзакции; повтор terminal event — no-op, конфликтующий terminal result — видимая protocol error, не вторая запись. Runtime восстанавливает pending deliveries из SQLite после restart. Result turn claim сохраняется до начала речи; при потере процесса claimed turn не проигрывается автоматически повторно. Нельзя потерять durable result из-за выключенного микрофона или browser disconnect.

## Task lifecycle

| Переход | Trigger / владелец | Durable результат и UI |
|---|---|---|
| → `queued` | Валидный delegate, runtime transaction | Task ID возвращён loop немедленно, `task.update(queued)` |
| `queued` → `running` | Adapter фиксирует launch intent, запускает отдельный Pi и prompt | Process/session mapping, `task.update(running)`; микрофон не блокируется |
| `running` → `succeeded` | `agent_settled` + валидный окончательный результат | Result + delivery атомарно, `task.update(succeeded, result)` |
| `queued/running` → `failed` | Известный отказ запуска/принятия или подтверждённая terminal ошибка | Error + terminal delivery атомарно, `task.update(failed)` |
| `running` → `unknown` | Потеря процесса/канала, restart с неопределённым outcome | UI сообщает неизвестный исход, task ID сохраняется; **нет автоматического повтора** |
| `unknown` → `succeeded/failed` | Надёжное восстановление исхода той же сессии, не новый dispatch | Обычная дедуплицированная terminal transaction; иначе остаётся unknown |

Task card обновляется сразу после durable result, если браузер подключён, иначе при snapshot. Результат один раз входит в context **исходной сессии**. Runtime ставит отдельный result turn в свою очередь, не вклинивая речь в пользовательский turn. Пользовательский speech onset имеет приоритет. Если разговор выключен/браузер отключён, озвучивание pending result ждёт следующего включения этой сессии, не другой активной сессии. Если result turn уже начал выполняться и был прерван barge-in/stop/disconnect, автоматического переозвучивания нет: результат остаётся в истории/card и доступен по запросу. Exactly-once относится к записи и постановке, **не** к физическому прослушиванию при потере связи.

### Task walkthroughs

1. **Поручение → продолжение разговора → late result.** `delegate(pi, research)` → SQLite k1 queued → немедленный tool result k1 → Pi running. Следующий голосовой turn идёт независимо. Settled/result → atomic result+delivery → task card → context insertion → после пользовательского turn отдельная краткая пользовательская реплика result turn → TTS. Сырой RPC/tool result в TTS не идёт.
2. **Barge-in во время поручения.** Voice g1 инвалидирован, audio flushed, g2 capturing; k1 остаётся running в своём процессе. Result приходит независимо, ждёт свободного conversation slot. Barge-in result turn прекращает звук и не ставит его назад в очередь.
3. **Результат без браузера → reconnect.** Conversation off, k1 завершается, result+pending delivery сохранены. Resume даёт snapshot/card/history без микрофона и старого аудио. Новая кнопка разрешает ещё не начатый result turn в паузе, с приоритетом речи пользователя.
4. **Restart backend при неизвестном исходе.** Незавершённый launch intent/running → unknown, показываем k1 и выбранную Pi session. Не запускаем новый процесс с тем же поручением. Только подтверждение исхода прежней сессии может закрыть unknown; иначе нужно решение пользователя, а не автоматический retry.
5. **Дубликаты.** Второй `tool_call_id` с тем же исходным ID возвращает k1; повтор `agent_settled`/terminal result не создаёт новую delivery/message/result turn. После restart pending delivery продолжает потребление, а не новый Pi dispatch; уже claimed/interrupted playback не возобновляется.

## Observability and self-hosted LangSmith

LangSmith обязателен **с первого голосового вертикального этапа**, self-hosted **на той же машине**, не opt-in и не SaaS. Приложение и LiteLLM явно получают локальный endpoint; отсутствие/невалидность endpoint — видимая configuration error, а не использование SDK SaaS default. Trace exporter не создаётся с неявным cloud URL. Это не равнозначно отсутствию license egress самого продукта.

### Official requirements and installation blockers

Проверка источников: **2026-09-10**, документальная, без установки, токенов и покупки лицензии. В таблице каждое утверждение ограничено указанным источником; изменяемые страницы не являются зафиксированной версией будущей установки.

| Предмет | Официальный источник (проверен 2026-09-10) | Подтверждено / предел подтверждения |
|---|---|---|
| License / entitlement | [Self-hosted LangSmith](https://docs.langchain.com/langsmith/self-hosted), [pricing](https://www.langchain.com/pricing) как ссылка из руководства | Self-host — add-on Enterprise; trial/license key через sales. Наличие entitlement у пользователя **не подтверждено**, публичные docs не дают лицензию |
| Доступные artifacts | [Официальный Helm repository](https://github.com/langchain-ai/helm), [chart](https://github.com/langchain-ai/helm/blob/03818bf6b3caf842869ad1ccda1dd6dc0e6e2620/charts/langsmith/Chart.yaml) | Chart/config доступны публично; на прочитанном commit chart `0.17.0-rc.25`, app `0.17.20rc1` — snapshot источника, **не выбор production версии**. Pull образов и доступ по лицензии на хосте не проверялись; будущая установка фиксирует stable release и image digests |
| Single-host способ | [Kubernetes guide](https://docs.langchain.com/langsmith/kubernetes), [старый Docker URL](https://docs.langchain.com/langsmith/docker) | Подтверждён Helm/Kubernetes; Minikube/Kind тестируются **для development**, поэтому локальный single-host dev путь документирован. Docker URL сейчас отдаёт общий self-host guide с переходом к Kubernetes, не отдельную поддерживаемую Compose-инструкцию. Production single-host и native/systemd LangSmith **не подтверждены** |
| Сервисы | [Состав установки](https://docs.langchain.com/langsmith/self-hosted) | Frontend, backend API, platform backend, queue, Playground, ACE; PostgreSQL (operational data), ClickHouse (traces/feedback), Redis (queue/cache). Blob storage опционален, рекомендован для production. VOCA SQLite их не заменяет |
| Версии инфраструктуры | [Dependency versions](https://docs.langchain.com/langsmith/self-host-dependency-versions) | PostgreSQL ≥14, Redis ≥6.2 (либо Valkey ≥8), ClickHouse не ниже версии выбранного chart; Helm ≥3, поддерживаемый upstream Kubernetes. Это требования продукта, не установленный набор VOCA |
| CPU/RAM | [Kubernetes prerequisites](https://docs.langchain.com/langsmith/kubernetes#kubernetes-cluster-requirements) | Рекомендуются ≥16 vCPU, 64 GB memory available. Default ClickHouse требует node с ≥4 vCPU / 16 GB **allocatable**. Это опубликованная рекомендация и default request, не доказанный минимум для одного пользователя |
| Storage | [Kubernetes prerequisites](https://docs.langchain.com/langsmith/kubernetes#kubernetes-cluster-requirements), [chart values](https://github.com/langchain-ai/helm/blob/03818bf6b3caf842869ad1ccda1dd6dc0e6e2620/charts/langsmith/values.yaml) | Для in-cluster DB требуются PV/dynamic provisioner; рекомендованы SSD, 7000 IOPS / 1000 MiB/s, расширение томов и backups. Универсальная достаточная ёмкость для VOCA не опубликована: зависит от payload/retention/rate; свободное место и throughput хоста не измерены |
| Network/license | [Egress](https://docs.langchain.com/langsmith/self-host-egress) | Для ≥0.9.0 без offline license обязателен HTTPS к `beacon.langchain.com`: проверка лицензии и usage billing. Offline/air-gapped license согласуется с sales, не получается выключением флага. Operational telemetry с 0.11 и usage telemetry включены по умолчанию, отдельно отключаемы; это не отключает billing |
| Локальный API | [Self-host usage](https://docs.langchain.com/langsmith/self-host-usage) | UI `http(s)://<host>`, API `http(s)://<host>/api/v1`; `LANGSMITH_ENDPOINT` либо SDK `api_url` задаются явно. Конкретные URL/auth VOCA ещё не настроены и в документ не копируются |

Целевой i5 (6 cores/12 threads) и около 32 GB RAM не соответствуют опубликованной рекомендации 16 vCPU/64 GB **даже до** ОС/inference. Hyperthreads не выдаются за свободные vCPU Kubernetes. RTX 4070 12 GB отдельно разделяется между STT/LLM/TTS. Нельзя объявлять этот host достаточным по уменьшенным на глаз requests или наличию GPU.

**Блокеры начала зависимой реализации этапа 1:** (1) подтвердить Enterprise self-host/trial entitlement и доступ к образам выбранного release; (2) согласовать документированный single-host dev профиль Minikube/Kind либо получить подтверждение другого поддерживаемого профиля, затем доказать совместное размещение в 32 GB RAM с inference; (3) выделить persistent storage/retention budget и подтвердить его ресурсно; (4) выбрать разрешённый license egress или получить offline entitlement. До этого этап не объявляется готовым. Документация VOCA-2 завершима с этими блокерами; LangSmith не заменяется продуктом/облаком. Ничего из перечисленного не установлено этим тикетом.

При будущем развёртывании отключаются необязательные operational/usage exports; VOCA traces остаются локальными. Не включать Engine, отправляющий содержимое во внешнюю LangSmith Intelligence, или cloud inference в Playground/других дополнительных функциях. Сетевые/license требования не скрывать под словом «локально».

### Trace map

Один voice turn — root run с `session_id`, `turn_id`, `generation_id`, trace/run IDs; task сохраняет исходные IDs и `task_id`, отдельный долгоживущий task run связан с originating trace, result turn ссылается на task run и исходную session. После завершения голосового trace связь с late result не теряется.

| Участок | Содержимое / наблюдаемая граница |
|---|---|
| Browser/transport/VAD | Capture start/end, speech onset/end, AEC request/settings, timestamps и clock domain, transport delay; browser audio boundary и ACK |
| STT | Input segment metadata и доступный локальный audio reference, language, redacted transcript, provider/model/version, timings/errors |
| Context | Порядок и snapshot version инструкций, redacted assembled messages/history и отметки interrupted/playback |
| LLM/LiteLLM | Redacted input/output, assistant content **отдельно** от reasoning, нормализованные tool calls, finish reason и first speakable text timing |
| Dispatch/Pi | Validated call, task ID, selected instance identity без credentials, prompt acceptance, доступные RPC/tool/MCP progress/errors/result, settled и durable delivery |
| TTS/playback | Пользовательский TTS input, output metadata (encoding/rate/chunks), first audio/first audible, queue depth, end marker, playback started/finished/cleared |
| Runtime | Queue enqueue/dequeue/claim, invalidation/cancel request/ack, отброшенные late events, disconnect/reconnect/snapshot, component errors |

LiteLLM callback покрывает LLM, **не всю трассу**. Приложение инструментирует остальные участки и связывает их IDs. Внутренности Pi/MCP, отсутствующие в их публичных events, отмечаются unavailable, а не придумываются. Для межпроцессных времён сохраняются clock domain/offset и погрешность; first audible фиксируется клиентом, не серверным timestamp выдачи PCM.

### Content, redaction and failure policy

Полезные inputs/outputs сохраняются для исследования, но это чувствительные тексты/результаты даже после redaction. Credentials удаляются **до** записи в экспортную очередь/trace: Authorization/Cookie headers, API keys, tokens, секреты в tool payloads/errors и свободном тексте. Не дампить окружение, auth configs, полные header maps. Идентификаторы/версии вместо содержимого конфигураций авторизации. Неизвестный неочищаемый payload не экспортируется; видимый redaction failure обозначает пропуск, а не молчаливую утечку.

Будущий redaction gate: синтетический маркер `VOCA_SYNTHETIC_SECRET` в заголовках, nested arguments/results и error strings; в локальной trace/очереди/повторной доставке маркера нет, полезный безопасный текст и IDs остаются. Реальные секреты для проверки не используются.

Недоступный LangSmith вызывает видимый `error(tracing/unavailable)` и запись в **ограниченную локальную очередь** повторной доставки (ограничения числа/байтов/возраста фиксируются до испытаний, с проверкой переполнения). Network export асинхронен и не блокирует микрофон. При переполнении/исчерпании retries фиксируются dropped count, затронутые event/run IDs и видимое tracing degraded; восстановление не скрывает потерянные spans. Повторная отправка использует прежние run/event IDs. Никакого SaaS fallback. Приложение может функционально продолжать разговор, но **неполная трасса не проходит gate наблюдаемости**.

## Deployment

Приложение предполагает прямой запуск на хосте; systemd возможен при будущей реализации. Это не обещание «всё без контейнеров»: официальный способ установки self-hosted LangSmith проверяется отдельно. Целевой бюджет: i5-10600KF, RTX 4070 **12 GB VRAM**, около **32 GB RAM** на ОС, runtime, одновременно резидентные STT/LLM/TTS и LangSmith вместе. Достаточность бюджета не установлена.

## Out of scope

VOCA-2 не создаёт runtime, UI, migrations, configs, services или тестовую инфраструктуру; не запускает эксперименты и не устанавливает модели/LangSmith. Ранние этапы v3 исключают multi-user, telephony, kiosk и proactivity. Cloud inference/fallback не входит в выбранную цепочку. Режим always-on, дополнительные агенты и замена моделей не prerequisite базового разговора.
