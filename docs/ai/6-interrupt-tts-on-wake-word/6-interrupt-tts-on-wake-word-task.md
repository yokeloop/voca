# Interrupt TTS playback on wake word during SPEAKING

**Slug:** 6-interrupt-tts-on-wake-word
**Тикет:** https://github.com/yokeloop/voca/issues/6
**Сложность:** medium
**Тип:** general

## Task

Позволить пользователю прервать воспроизведение TTS произнесением wake-word: убить `piper | aplay`, перейти прямо в `RECORDING` и принять новую команду.

## Context

### Архитектура области

Daemon — событийная state-машина. `listener.py` (Python, openWakeWord + PyAudio) держит микрофон всю жизнь процесса и пишет JSON-события в stdout. `daemon.ts` подписывается на события и управляет listener'ом через сигналы:

- `SIGUSR1` → `start_recording()` в listener.py
- `SIGUSR2` → `stop_recording_save()` в listener.py
- `SIGTERM` → shutdown

Data flow во время SPEAKING: `piper stdout → aplay stdin`, `aplay.on('close')` ресолвит промис `speak()`. Во время SPEAKING listener.py **не паузится** (вопреки CLAUDE.md) — он продолжает крутить модель, но daemon игнорирует `wake`/`stop` события через гард `if (this.state !== 'IDLE')` в `handleWake` (`daemon.ts:121`).

Проблема: (а) `handleWake` молча дропает wake во время SPEAKING, (б) `speak()` не сохраняет PID пайплайна, убить нечем.

### Файлы для изменения

- `src/daemon-state.ts:13-24` — таблица переходов. Добавить `SPEAKING+WAKE → RECORDING`.
- `src/daemon.ts:120-143` — `handleWake`. Снять гард `state !== 'IDLE'`, добавить ветку для SPEAKING: вызвать `speaker.interrupt()`, обновить состояние в `RECORDING`, отправить `SIGUSR1` в listener.py для старта записи. Без `playSound('wake')` в этой ветке.
- `src/daemon.ts:224-235` — вызов `speak()`. Нужен способ получить handle (canceller) для последующего interrupt, либо хранить активный handle в поле `this.activeSpeech`.
- `src/speaker.ts:14-81` — `speak()` сейчас возвращает `Promise<void>`. Изменить: возвращать `{ done: Promise<void>; interrupt(): void }` (или экспортировать отдельный `SpeechHandle`). `interrupt()` убивает `piper` и `aplay` через `SIGTERM`, ресолвит `done` без ошибки.
- `src/listener.ts:60-74` — `ListenerHandle`. Добавить методы `speakingStart()` (SIGRTMIN) и `speakingEnd()` (SIGRTMIN+1). Сохранить существующие `pause/resume/kill`.
- `listener.py:94-96, 153-172, 230-242` — добавить флаг `speaking` и обработчики `SIGRTMIN`/`SIGRTMIN+1` (`signal.SIGRTMIN` в Python). В ветке wake-detection (строки 230-242): если `speaking=True`, использовать `SPEAKING_THRESHOLD` вместо `THRESHOLD` для `wake_key`. Stop-word во время SPEAKING игнорировать.
- `test/daemon.test.ts` — новый сценарий: wake во время SPEAKING → interrupt → RECORDING.
- `test/daemon-state.test.ts:19-24` — добавить валидный переход, из invalid-списка удалить если присутствует.

### Паттерны для повторения

- Сигнальная коммуникация daemon↔listener: `listener.ts:64-72` + `listener.py:158-172`. Повтори ту же схему для двух новых сигналов.
- Управление child-процессом через `SIGTERM`: `listener.ts:71`, `recorder.ts:66,75`. `speaker.interrupt()` делает `piper.kill('SIGTERM')` и `aplay.kill('SIGTERM')`, выставляет флаг `settled=true`, ресолвит промис.
- Settled-гард в `speaker.ts:40` — сохраняется, interrupt выставляет `settled=true` до того как `aplay.on('close')` успеет отреджектить.
- Таблица переходов как единственный источник правды (`daemon-state.ts:13-24`). Новое событие — новое имя в `DaemonEvent` в `src/types.ts`, новая строка в таблице.

### Тесты

Существующие: `test/daemon.test.ts:125-200` покрывают happy path и stop-while-recording. `test/daemon-state.test.ts:19-52` покрывает валидные и невалидные переходы. Покрытия для interrupt нет — добавить:

- state-машина: `SPEAKING+WAKE → RECORDING` валиден.
- daemon: эмитим `wake` в состоянии SPEAKING → `speaker.interrupt()` вызван, listener получил `SIGUSR1` (старт записи), новый wake-sound **не** играется.
- speaker: `interrupt()` убивает оба процесса, `done` ресолвится без исключения.

## Requirements

1. Новое событие `DaemonEvent = 'WAKE_INTERRUPT'` (или переиспользовать `WAKE`) и переход `SPEAKING+WAKE_INTERRUPT → RECORDING` в `daemon-state.ts`.
2. `speak()` возвращает `SpeechHandle` с методом `interrupt(): void`. `interrupt()` посылает `SIGTERM` piper и aplay, выставляет `settled=true`, ресолвит `done` без ошибки. Повторный вызов — no-op.
3. `daemon.ts` хранит активный `SpeechHandle` в поле инстанса на время SPEAKING; после `SPEAKING_DONE` очищает его.
4. `handleWake` при `state === 'SPEAKING'`: вызывает `activeSpeech.interrupt()`, переходит `SPEAKING → RECORDING` через новый event, шлёт `SIGUSR1` листенеру, **не** играет `wake.wav`.
5. `listener.ts` экспортирует методы `speakingStart()` и `speakingEnd()` на `ListenerHandle`. Шлют `SIGRTMIN` и `SIGRTMIN+1` процессу listener.py.
6. `listener.py` держит флаг `speaking`. Обработчик `SIGRTMIN` ставит `speaking=True`, `SIGRTMIN+1` — `False`. Пока `speaking=True`, порог детекции wake повышен до `SPEAKING_THRESHOLD = 0.85` (новая константа рядом с `THRESHOLD = 0.5`); stop-детекция в этой ветке отключена.
7. `daemon.ts` вызывает `listener.speakingStart()` перед стартом `speak()` и `listener.speakingEnd()` в finally после завершения (включая interrupt и ошибку).
8. Интервал от произнесения wake до реального `SIGTERM` piper+aplay — не более ~200ms (прерывание мгновенное, без `playSound('wake')`).
9. Тесты покрывают: state-переход, interrupt speaker'а, отсутствие wake-sound при прерывании, корректный `speakingEnd()` даже при ошибке speak.

## Constraints

- Не удалять и не переименовывать существующие `pause/resume/kill` — они нужны для записи и shutdown.
- Не трогать `sounds.ts` — по решению, wake/stop/error beep'ы считаются достаточно непохожими на wake-word, их самотриггер не митигируем.
- Не вводить AEC, не менять модели openWakeWord, не добавлять PyAudio-фильтры. Единственная митигация — повышение порога во время SPEAKING.
- Не использовать SIGSTOP/SIGCONT для listener.py — mic должен продолжать слушать, иначе wake не детектится во время TTS.
- Не ломать контракт `speak()` вне voca: вызов из daemon — единственный; если внешних потребителей нет, менять сигнатуру свободно.
- `piper` и `aplay` стартуют через `spawn()` в pipe-паре; при `interrupt()` гасить оба, иначе piper зависнет на `stdout.pipe(dead_stdin)` (см. риск в findings).

## Verification

- `npm test` → все существующие тесты зелёные + новые тесты для interrupt проходят.
- `npm run build` → tsc без ошибок типов.
- Ручной прогон в реальном окружении: `voca start`, произнести wake, получить длинный ответ, произнести wake во время TTS → звук обрезается за ~200ms, beep записи (stop.wav играется на завершении) в конце; записанная команда отправляется агенту как обычно.
- Edge case: wake-word звучит в самом TTS (например, ответ содержит «jarvis») → при пороге 0.85 не триггерится на типичном TTS-голосе; если триггерится — не регрессия, а известный компромисс threshold-подхода.
- Edge case: piper крашится сам по себе во время SPEAKING → `aplay.on('close')` реджектит → `speakingEnd()` вызывается в finally, состояние восстанавливается через `recoverFromError`.
- Edge case: interrupt вызван дважды → второй вызов no-op, промис уже ресолвлен.

## Материалы

- [GitHub Issue #6](https://github.com/yokeloop/voca/issues/6)
- `src/daemon.ts:120-143,224-235`
- `src/daemon-state.ts:13-24`
- `src/speaker.ts:14-81`
- `src/listener.ts:60-74`
- `listener.py:82-172,230-242`
- `src/types.ts` (DaemonEvent, ListenerHandle)
- `test/daemon.test.ts:125-200`
- `test/daemon-state.test.ts:19-52`
