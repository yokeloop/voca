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

## Deployment

Приложение предполагает прямой запуск на хосте; systemd возможен при будущей реализации. Это не обещание «всё без контейнеров»: официальный способ установки self-hosted LangSmith проверяется отдельно. Целевой бюджет: i5-10600KF, RTX 4070 **12 GB VRAM**, около **32 GB RAM** на ОС, runtime, одновременно резидентные STT/LLM/TTS и LangSmith вместе. Достаточность бюджета не установлена.

## Out of scope

VOCA-2 не создаёт runtime, UI, migrations, configs, services или тестовую инфраструктуру; не запускает эксперименты и не устанавливает модели/LangSmith. Ранние этапы v3 исключают multi-user, telephony, kiosk и proactivity. Cloud inference/fallback не входит в выбранную цепочку. Режим always-on, дополнительные агенты и замена моделей не prerequisite базового разговора.
