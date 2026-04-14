# Implementation Plan — Interrupt TTS on wake word

**Slug:** 6-interrupt-tts-on-wake-word
**Тикет:** https://github.com/yokeloop/voca/issues/6
**Сложность:** medium
**Тип:** general
**Routing:** `sub-agents`, parallel=true — 7 атомарных задач, три естественных слоя: types → (speaker ‖ listener.ts ‖ listener.py ‖ daemon-state) → daemon → tests.

## Design Decisions

### D1. Новый event `WAKE_INTERRUPT`, не переиспользовать `WAKE`

**Выбор:** добавить `'WAKE_INTERRUPT'` в `DaemonEvent` (types.ts:26-33).
**Почему:** таблица переходов (`daemon-state.ts:13-24`) маппит `(state, event) → state`. Если переиспользовать `WAKE`, придётся писать `IDLE+WAKE → LISTENING` и `SPEAKING+WAKE → RECORDING` — семантика одного события раздваивается. Явный новый event читается в логах и делает gard в `handleWake` однозначным.
**Отвергнуто:** передать `WAKE` в `transition()` с условной веткой по state внутри функции — разрушает чистоту transition-table.

### D2. `SpeechHandle` с синхронным `interrupt()`

**Выбор:** `speak()` возвращает `SpeechHandle = { done: Promise<void>; interrupt(): void }`.
**Почему:** `process.kill()` синхронный (`listener.ts:65,68,71`, `recorder.ts:66,75` — тот же паттерн). Асинхронная часть (ресолв `done`) приезжает через `aplay.on('close')`. `handleWake` не должен ждать interrupt — он сразу идёт в `RECORDING`.
**Отвергнуто:** `speak()` возвращает `AbortController` — избыточная обвязка; `Promise<void>` с побочным глобалом — нарушает инкапсуляцию.

### D3. Хранить активный `SpeechHandle` в поле `VocaDaemon.activeSpeech`

**Выбор:** приватное поле `activeSpeech: SpeechHandle | null = null` в `VocaDaemon`.
**Почему:** одна речь в один момент (state-машина это гарантирует). Доступ из `handleWake` и очистка в `finally` вокруг `speak()` в `onRecorderDone`. Без глобалов, без утечек.
**Отвергнуто:** Map/стек — нет сценария множественных речей.

### D4. IPC через SIGRTMIN / SIGRTMIN+1

**Выбор:** `speakingStart()` шлёт `SIGRTMIN`, `speakingEnd()` — `SIGRTMIN+1`.
**Почему:** решение пользователя (см. задача). SIGUSR1/2 заняты под запись. SIGRTMIN переносимо (Python `signal.SIGRTMIN`), real-time-сигналы не коалесцируются.
**Отвергнуто:** JSON-команды в stdin — требует нового reader-loop в listener.py.

### D5. Митигация самотриггера — повышение порога в listener.py

**Выбор:** константа `SPEAKING_THRESHOLD = 0.85` рядом с `THRESHOLD = 0.5` (listener.py:95). Применяется только к wake-key в ветке не-recording (listener.py:230-242), и только когда `speaking=True`. Stop-детекция во время SPEAKING отключена.
**Почему:** решение пользователя. Простейшая митигация без внешних зависимостей (AEC).
**Отвергнуто:** mute микрофона — сложнее и ломает низкоуровневую детекцию.

### D6. Не играть `wake.wav` при прерывании

**Выбор:** ветка SPEAKING в `handleWake` не вызывает `playSound('wake', ...)`.
**Почему:** требование 8 (latency ~200ms) и решение пользователя по acceptance criteria. Wake-beep добавил бы 100-200ms задержки.

### D7. Сохранить существующий guard «wake ignored when RECORDING/LISTENING/PROCESSING»

**Выбор:** `handleWake` обрабатывает только `IDLE` (как сейчас) и `SPEAKING` (новое). Остальные состояния — лог и return.
**Почему:** тест `test/daemon.test.ts:203-217` опирается на это поведение; wake во время RECORDING/PROCESSING смысла не имеет.

## Decomposition

### T1 — Типы: WAKE_INTERRUPT event + ListenerHandle методы

**Files:** `src/types.ts`
**What:**
- В `DaemonEvent` (строка 26-33) добавить `| 'WAKE_INTERRUPT'`.
- В `ListenerHandle` (строки 35-40) добавить `speakingStart(): void;` и `speakingEnd(): void;`.
**Scope:** S (2-3 строки).
**Depends on:** —
**Verify:** `npm run build` → tsc без ошибок.

### T2 — State-машина: переход SPEAKING+WAKE_INTERRUPT → RECORDING

**Files:** `src/daemon-state.ts`, `test/daemon-state.test.ts`
**What:**
- В `transitionTable` (daemon-state.ts:13-24) добавить `'SPEAKING+WAKE_INTERRUPT': 'RECORDING'`.
- В `test/daemon-state.test.ts:19-24` (validCases) добавить `['SPEAKING', 'WAKE_INTERRUPT', 'RECORDING']`. Если в invalidCases (строки 26-52) есть `['SPEAKING', 'WAKE']` — оставить (этот case остаётся невалидным: `WAKE`, не `WAKE_INTERRUPT`).
**Scope:** S.
**Depends on:** T1.
**Verify:** `npm test test/daemon-state.test.ts` → зелёный.

### T3 — Speaker: SpeechHandle с interrupt()

**Files:** `src/speaker.ts`
**What:**
- Экспортировать type `SpeechHandle = { done: Promise<void>; interrupt(): void }`.
- Рефакторить `speak()` (speaker.ts:14-81): вместо `Promise<void>` вернуть `SpeechHandle`. Внутри сохранить ссылки на `piper` и `aplay` в замыкании; создать промис `done` как сейчас.
- Реализовать `interrupt()`: если `settled` — no-op; иначе `settled = true`, `piper.kill('SIGTERM')`, `aplay.kill('SIGTERM')`, `resolve()`. Гард `settled` уже есть (speaker.ts:40) — `aplay.on('close')` увидит settled и выйдет.
- Пробросить `opts.text` в piper.stdin как сейчас (строки 78-79).
**Scope:** S-M (~20 строк).
**Depends on:** T1 (косвенно — не требует, но по порядку после типов).
**Verify:** `npm run build`; unit-тест в T7 проверит interrupt.

### T4 — Listener.ts: speakingStart / speakingEnd

**Files:** `src/listener.ts`
**What:**
- В объект, возвращаемый из `spawnListener` (listener.ts:60-73), добавить методы:
  - `speakingStart(): void { if (child.pid) process.kill(child.pid, 'SIGRTMIN'); }`
  - `speakingEnd(): void { if (child.pid) process.kill(child.pid, 'SIGRTMIN+1'); }`
- В Node.js `process.kill` принимает строку `'SIGRTMIN'` — проверить через `os.constants.signals.SIGRTMIN`; если строка не поддерживается, использовать числовой код: `process.kill(child.pid, os.constants.signals.SIGRTMIN)` / `+ 1`.
**Scope:** S (6-8 строк).
**Depends on:** T1.
**Verify:** `npm run build`; `npm test` — моки в daemon.test подхватят новый интерфейс после T7.

### T5 — listener.py: speaking flag + SIGRTMIN handlers + SPEAKING_THRESHOLD

**Files:** `listener.py`
**What:**
- Рядом с `THRESHOLD = 0.5` (строка 95) добавить `SPEAKING_THRESHOLD = 0.85`.
- В блок состояния (строки 82-92) добавить `speaking = False`.
- Добавить обработчики после `handle_sigusr2` (строки 165-168):
  ```python
  def handle_sigrtmin(signum, frame):
      nonlocal speaking
      speaking = True

  def handle_sigrtmin_end(signum, frame):
      nonlocal speaking
      speaking = False
  ```
- Зарегистрировать: `signal.signal(signal.SIGRTMIN, handle_sigrtmin)`, `signal.signal(signal.SIGRTMIN + 1, handle_sigrtmin_end)` (после строки 172).
- В ветке wake-detection (строки 230-242): если `speaking`, использовать `SPEAKING_THRESHOLD` для `wake_key`, а stop-детекцию пропустить:
  ```python
  wake_thr = SPEAKING_THRESHOLD if speaking else THRESHOLD
  if scores.get(wake_key, 0) > wake_thr:
      print(json.dumps({"event": "wake"}), flush=True)
      model.reset()
  if not speaking and stop_key is not None and scores.get(stop_key, 0) > THRESHOLD:
      ...
  ```
**Scope:** M (~20-25 строк).
**Depends on:** —
**Verify:** `python3 -c "import py_compile; py_compile.compile('listener.py', doraise=True)"`; ручной smoke — `voca start`, отправить SIGRTMIN процессу, убедиться что wake не триггерится на тихом TTS.

### T6 — Daemon: activeSpeech + speakingStart/End + handleWake SPEAKING branch

**Files:** `src/daemon.ts`
**What:**
- Добавить приватное поле `private activeSpeech: SpeechHandle | null = null;` рядом со `listener` (daemon.ts:26-28). Импортировать `SpeechHandle` из `./speaker.js`.
- В `onRecorderDone` в блоке вокруг `speak()` (daemon.ts:224-235):
  ```typescript
  this.listener?.speakingStart();
  try {
    this.activeSpeech = speak({...});
    await this.activeSpeech.done;
  } finally {
    this.activeSpeech = null;
    this.listener?.speakingEnd();
  }
  ```
  Семантика `speak()` меняется (T3): теперь синхронный вызов возвращает handle, ждать `.done`.
- Модифицировать `handleWake` (daemon.ts:120-143):
  ```typescript
  if (this.state === 'IDLE') {
    // существующая ветка без изменений
  } else if (this.state === 'SPEAKING') {
    this.activeSpeech?.interrupt();
    this.state = transition(this.state, 'WAKE_INTERRUPT');
    this.writeStateFile();
    console.log(`[daemon] state: ${this.state} (interrupted TTS)`);
    this.listener?.pause(); // SIGUSR1 → start_recording
    // нет playSound('wake')
  } else {
    console.log(`[daemon] ignoring wake in state ${this.state}`);
    return;
  }
  ```
  Оставить общий `try/catch` как сейчас.
**Scope:** M (~40 строк изменений).
**Depends on:** T1, T2, T3, T4.
**Verify:** `npm run build`.

### T7 — Тесты: interrupt сценарии + обновление mocks

**Files:** `test/daemon.test.ts`
**What:**
- Расширить `mockListenerHandle` (daemon.test.ts:10-14): добавить `speakingStart: vi.fn(), speakingEnd: vi.fn()`.
- Обновить mock `speak` (строка 37-39): вместо `vi.fn(async () => {})` возвращать `vi.fn(() => ({ done: Promise.resolve(), interrupt: mockInterrupt }))`, где `mockInterrupt = vi.fn()` — экспортированный в scope для проверок.
- В happy-path тесте (строка 124-162) после `speak` ожидания: `expect(mockListenerHandle.speakingStart).toHaveBeenCalled()` и `expect(mockListenerHandle.speakingEnd).toHaveBeenCalled()`.
- Новый describe «interrupt TTS during SPEAKING»:
  ```typescript
  it('interrupts speak and transitions to RECORDING on wake during SPEAKING', async () => {
    // arrange: let speak() pend so daemon stays in SPEAKING
    let resolveDone: () => void;
    (speak as any).mockImplementation(() => ({
      done: new Promise<void>((r) => { resolveDone = r; }),
      interrupt: mockInterrupt,
    }));

    await daemon.start();
    mockListenerHandle.emit('wake'); await flush();
    mockListenerHandle.emit('recorded', '/tmp/x.wav'); await flush();
    // now in SPEAKING, speak() pending
    expect(daemon.getState()).toBe('SPEAKING');

    (playSound as any).mockClear();
    mockListenerHandle.emit('wake'); await flush();

    expect(mockInterrupt).toHaveBeenCalled();
    expect(mockListenerHandle.pause).toHaveBeenCalledTimes(2); // first wake + interrupt
    expect(playSound).not.toHaveBeenCalledWith('wake', expect.anything());
    expect(daemon.getState()).toBe('RECORDING');

    resolveDone!(); // cleanup
  });
  ```
- Проверка `speakingEnd()` вызывается и при ошибке speak:
  ```typescript
  it('calls speakingEnd even when speak rejects', async () => {
    (speak as any).mockImplementation(() => ({
      done: Promise.reject(new Error('piper died')),
      interrupt: vi.fn(),
    }));
    // ... прогон до speak, проверить speakingEnd вызван
  });
  ```
- Тест «wake ignored when RECORDING» (строка 203-217) остаётся без изменений — подтверждает D7.
**Scope:** M (~60 строк).
**Depends on:** T1-T6.
**Verify:** `npm test test/daemon.test.ts`.

## File Intersection Matrix

|       | T1 | T2 | T3 | T4 | T5 | T6 | T7 |
|-------|----|----|----|----|----|----|----|
| T1    | —  | —  | —  | —  | —  | —  | —  |
| T2    | types.ts? (типы только импортит) | — | — | — | — | — | — |
| T3    | импорт типов | нет | — | — | — | — | — |
| T4    | импорт типов | нет | нет | — | — | — | — |
| T5    | нет | нет | нет | нет | — | — | — |
| T6    | **types.ts import** | **daemon-state import** | **speaker import** | **listener import** | нет | — | — |
| T7    | types | state | speak mock | listener mock | нет | daemon | — |

**Пересечения файлов:** нет — каждый task правит отдельный файл (кроме T2 который трогает пару state+state-test). T6 и T7 оба трогают `daemon.ts` и `daemon.test.ts` соответственно, но файлы разные.

## Execution Order (DAG)

```
T1 (types) ────┬──→ T2 (state + state-test)  ──┐
               ├──→ T3 (speaker)               ├──→ T6 (daemon) ──→ T7 (daemon tests)
               ├──→ T4 (listener.ts)           │
               │                               │
               └── (T5 listener.py parallel) ──┘
```

**Parallel groups:**
- Group A (после T1): **T2, T3, T4, T5** — все независимы по файлам.
- Group B: T6 — после завершения A.
- Group C: T7 — после T6.

**Sequential cuts:** T1 → Group A → T6 → T7.

## Verification (из task-файла)

- `npm test` → все тесты зелёные (включая новые interrupt-кейсы).
- `npm run build` → tsc без ошибок.
- `python3 -m py_compile listener.py` → успех.
- Ручной smoke: `voca start`, wake → длинный ответ → wake во время TTS → звук обрезается ~200ms, записывается новая команда.
- Edge cases покрыты в тестах: interrupt×2 no-op (через T3 settled guard), speakingEnd при ошибке speak (T7).

## Materials

- [GitHub Issue #6](https://github.com/yokeloop/voca/issues/6)
- `docs/ai/6-interrupt-tts-on-wake-word/6-interrupt-tts-on-wake-word-task.md`
- `src/types.ts:19-40`
- `src/daemon-state.ts:13-24`
- `src/speaker.ts:14-81`
- `src/listener.ts:60-73`
- `src/daemon.ts:120-143,224-235`
- `listener.py:82-172,230-242`
- `test/daemon.test.ts:9-49,113-231`
- `test/daemon-state.test.ts:19-52`
