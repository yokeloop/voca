# VOCA — реализация голосового ассистента

**Slug:** voca-implementation
**Тикет:** —
**Сложность:** complex
**Тип:** general

## Task

Реализовать Node.js CLI daemon `voca` поэтапно — от project setup до полного голосового цикла. Каждая фаза даёт работающий результат с проверкой. Семь фаз: setup → sounds+listener → recorder+transcriber → agent+speaker → state machine → bootstrap → daemon mode.

## Context

### Архитектура области

Daemon слушает wake word, записывает речь, транскрибирует, отправляет в OpenClaw agent, озвучивает ответ.

```
Mic (USB plughw:2,0)
  │
  ├── listener.py (openWakeWord + PyAudio) ── JSON lines stdout
  │     {"event":"wake"} / {"event":"stop"}
  │
  └── sox rec (запись WAV с silence detection)
        │
        ▼
  daemon.ts (state machine)
    IDLE → LISTENING → RECORDING → PROCESSING → SPEAKING → IDLE
        │
        ├── transcriber.ts → whisper-stt-wrapper <file> --language ru
        ├── agent.ts → openclaw agent --agent <profile> --session-id <id> --message "<text>" --json
        └── speaker.ts → echo "<text>" | piper --model <m> --output_raw | aplay -D plughw:2,0 ...
```

Daemon сохраняет runtime-данные в `~/.openclaw/assistant/` (config.json, session.json, sounds/, models/, venv/, bin/), вне репозитория.

### Внешние инструменты (проверены)

| Инструмент | Путь | Версия | Статус |
|---|---|---|---|
| Node.js | `/usr/bin/node` | v24.13.1 | OK |
| npm | — | 11.8.0 | OK |
| Python 3 | `/usr/bin/python3` | 3.13.5 | OK (externally-managed, нужен venv) |
| openclaw | `/home/priney/.npm-global/bin/openclaw` | 2026.3.8 | OK |
| whisper-stt | `/usr/local/bin/whisper-stt-wrapper` | faster-whisper 1.2.1 | OK |
| sox | `/usr/bin/sox` | — | OK (запись + silence + генерация звуко��) |
| aplay | `/usr/bin/aplay` | — | OK |
| piper | — | — | НЕ установлен (bootstrap) |
| openWakeWord | — | — | НЕ установлен (bootstrap) |

**Критичные отличия от исходной спеки:**
- whisper-stt: файл ПЕРВЫМ аргументом — `whisper-stt-wrapper <file.wav> --language ru`
- Запись через `sox rec` вместо `arecord` — встроенный silence detection
- Аудиоустройство: HyperX Cloud Flight Wireless = `plughw:2,0` (mic и speakers)

### Файлы для создания

```
~/voca/
├── package.json              # @yokeloop/voca, bin: { "voca": "./dist/cli.js" }
├── tsconfig.json             # target: ES2022, module: NodeNext, strict
├── vitest.config.ts
├── src/
│   ├── types.ts              # VocaConfig, VocaSession, DaemonState, все интерфейсы
│   ├── cli.ts                # Commander entry point
│   ├── daemon.ts             # State machine + I/O orchestration
│   ├── daemon-state.ts       # Чистая state machine (без I/O, тестируемая)
│   ├── listener.ts           # Spawn/manage openWakeWord process
│   ├── recorder.ts           # sox rec child process, WAV + silence detection
│   ├── transcriber.ts        # whisper-stt-wrapper invocation
│   ├── agent.ts              # openclaw agent CLI invocation
│   ├── speaker.ts            # piper | aplay pipe
│   ├── session.ts            # Session/profile management
│   ├── config.ts             # Config read/write ~/.openclaw/assistant/config.json
│   ├── sounds.ts             # Звуковые индикаторы через aplay
│   └── bootstrap.ts          # Interactive setup + dependency installation
├── listener.py               # openWakeWord Python script (PyAudio)
├── sounds/                   # Генерируются через sox при bootstrap
│   ├── wake.wav
│   ├── stop.wav
│   └── error.wav
└── test/
    ├── config.test.ts
    ├── session.test.ts
    ├── daemon-state.test.ts
    ├── transcriber.test.ts
    └── agent.test.ts
```

### Паттерны для повторения

Проект начинается с нуля — переиспользовать код нельзя. Ориентиры:
- whisper-stt venv (`/home/priney/whisper-stt/.venv/`) — образец для создания openWakeWord venv
- openclaw config (`~/.openclaw/openclaw.json`) — хранит timeout 900s, агенты personal/public
- Каждый модуль: отдельный файл, child process через `spawn`/`execFile`, EventEmitter для событий

### Тесты

Создай тесты с нуля. Фреймворк: vitest (devDependency).

Покрытие:
- `daemon-state.ts` — все переходы state machine
- `config.ts` — read/write/default
- `session.ts` — new/increment/reset при смене профиля
- `transcriber.ts` — парсинг stdout, обрезка "stop"/"стоп"
- `agent.ts` — парсинг JSON ответа, обработка ошибок (mock child process)

## Requirements

### Фаза 1 — Project setup + Config + Session CLI

1. Создать `package.json` с `"name": "@yokeloop/voca"`, `"type": "module"`, `"bin": { "voca": "./dist/cli.js" }`, scripts: build (tsc), dev (tsx src/cli.ts), test (vitest).
2. Создать `tsconfig.json`: target ES2022, module NodeNext, moduleResolution NodeNext, strict true, outDir dist.
3. Реализовать `src/config.ts` — readConfig, writeConfig, ensureConfigDir, defaultConfig. Путь: `~/.openclaw/assistant/config.json`.
4. Реализовать `src/session.ts` — readSession, writeSession, newSession, generateSessionId (`asst-<unix-ts>`), incrementMessageCount. Смена профиля сбрасывает сессию.
5. Реализовать `src/cli.ts` через commander: `session new`, `session info`, `profile list`, `profile use <id>`.
6. Написать тесты для config и session.

**Проверка:** `npm run build` компилируется. `npx tsx src/cli.ts session new` создаёт session. `npx tsx src/cli.ts profile use public` переключает профиль и сбрасывает сессию. `npm test` проходит.

### Фаза 2 — Sounds + Listener skeleton

7. Реализовать `src/sounds.ts` — playSound(type, opts) через `aplay -D <device> <wav-file>`.
8. Сгенерировать звуковые файлы через sox: wake.wav (880Hz 0.1s), stop.wav (880Hz 0.1s x2), error.wav (220Hz 0.3s).
9. Реализовать `src/listener.ts` — spawnListener возвращает ListenerHandle с on('wake'/'stop'), pause() (SIGSTOP), resume() (SIGCONT), kill().
10. Создать `listener.py` skeleton: читает stdin, при вводе "wake" → `{"event":"wake"}`, при "stop" → `{"event":"stop"}`. Для тестирования без реальных моделей.

**Проверка:** `npx tsx -e "import('./src/sounds.js').then(m => m.playSound('wake', {...}))"` — слышен beep. listener.py в stdin-режиме эмитит JSON lines.

### Фаза 3 — Recorder + Transcriber

11. Реализовать `src/recorder.ts` — startRecording через `sox rec`. Sox автоматически останавливает запись при тишине >30s (silence detection). Максимальная длительность 2 min. Возвращает RecorderHandle: filePath, stop(), cancel().
12. Реализовать `src/transcriber.ts` — transcribe(filePath, opts) через `execFile(whisperBin, [filePath, '--language', language])`. Обрежь trailing "stop"/"стоп" из текста.
13. Написать тесты для transcriber (mock execFile, проверка обрезки).

**Проверка:** запись 3s → файл WAV создан. `transcribe('/tmp/test.wav', {language: 'ru'})` возвращает текст.

### Фаза 4 — Agent + Speaker

14. Реализовать `src/agent.ts` — queryAgent(opts) через `execFile(openclaw, ['agent', '--agent', agentId, '--session-id', sessionId, '--message', message, '--json', '--timeout', String(timeoutS)])`. Парсить JSON ответ. Timeout: 900s. При ошибке — throw AgentError.
15. Реализовать `src/speaker.ts` — speak(opts) через pipe: `echo text → piper --model M --output_raw → aplay -D device -r 22050 -f S16_LE -c 1`. Без промежуточных файлов. await на завершение aplay. Пауза 500ms после.
16. Написать тест для agent (mock child process, проверка парсинга).

**Проверка:** `queryAgent({...message: 'привет'...})` возвращает ответ от OpenClaw. `speak({text: 'Привет мир', ...})` озвучивает текст (требует установленный piper).

### Фаза 5 — State Machine

17. Реализовать `src/daemon-state.ts` — чистая функция transition(state, event) → newState. Типы: State = IDLE|LISTENING|RECORDING|PROCESSING|SPEAKING. Events: WAKE, STOP, RECORD_CANCEL, PROCESSING_DONE, SPEAKING_DONE, ERROR.
18. Реализовать `src/daemon.ts` — класс VocaDaemon extends EventEmitter. Метод start() запускает listener, входит в IDLE. Метод stop() — graceful shutdown. Оркестрирует все модули по state machine.
19. Написать тесты daemon-state: все валидные переходы, отклонение невалидных.

**Проверка:** `npm test` — тесты state machine зелёные. `npx tsx src/cli.ts start` — daemon работает с listener.py в stdin-режиме. Ввод "wake" �� beep → запись. Ввод "stop" → double-beep → transcription → agent → TTS.

### Фаза 6 — Bootstrap + полный listener.py

20. Реализовать `src/bootstrap.ts` — интерактивный setup: выбор mic (из `sox --help` или `arecord -l`), speaker, профиля, wake/stop words. Установка piper (скачать aarch64 бинарь + ru_RU-irina-medium модель). Создание venv + `pip install openwakeword pyaudio`. Скачивание ONNX-моделей (hey_jarvis, stop). Копирование sounds/. Подтверждай каждую установку.
21. Реализуй `listener.py` — реальный openWakeWord: загрузка моделей, захват аудио через PyAudio, inference, вывод JSON lines. Обе модели (wake + stop) работают одновременно.

**Проверка:** `npx tsx src/cli.ts bootstrap` — устанавливает piper, venv, openWakeWord, модели. `npx tsx src/cli.ts start` — произносим "hey jarvis" → wake word detection срабатывает.

### Фаза 7 — Daemon mode + полный CLI + публикация

22. Добавить `start --daemon` — fork процесса, запись PID в `~/.openclaw/assistant/daemon.pid`, статус в `~/.openclaw/assistant/daemon-state.json`.
23. Реализовать `stop` — читает PID-файл, посылает SIGTERM. `status` — читает daemon-state.json.
24. Graceful shutdown по SIGINT/SIGTERM: kill всех child processes (listener.py, sox, piper, aplay, openclaw).
25. Подготовить к публикации: `.npmignore`, обновить README.md.

**Проверка:** полный голосовой цикл — `voca start --daemon` → "hey jarvis" → beep → фраза → "stop" → double-beep → ответ озвучен. `voca status` покажет состояние. `voca stop` завершит daemon. `npm test` — все тесты зелёные. Ctrl+C завершит процесс чисто.

## Constraints

- Не модифицировать openclaw — интеграция только через `openclaw agent` CLI
- Runtime данные (`config.json`, `session.json`, `sounds/`, `models/`, `venv/`) — в `~/.openclaw/assistant/`, не в репозитории
- Зависимости минимальны: `commander` (runtime), `tsx` + `vitest` + `typescript` (dev). Без express, socket.io
- Python venv для openWakeWord — в `~/.openclaw/assistant/venv/`, не системный pip
- TTS без промежуточных файлов — piper pipe в aplay
- listener.py — long-lived process, SIGSTOP/SIGCONT для паузы (не перезапуск)
- Запись через sox rec (не arecord) — встроенный silence detection
- Звуковые файлы генерируются через sox (не скачиваются)
- whisper-stt: файл первым аргументом — `whisper-stt-wrapper <file.wav> --language ru`

## Verification

- `npm run build` — компиляция без ошибок
- `npm test` — все unit-тесты зелёные (state machine, config, session, transcriber, agent)
- `voca bootstrap` — интерактивно настраивает mic/speaker, устанавливает piper, создаёт venv с openWakeWord
- `voca start` — daemon запускается, listener.py стартует, состояние IDLE
- Произнести "hey jarvis" → beep, переход в RECORDING
- Произнести фразу + "stop" → double-beep, транскрипция, отправка в openclaw agent, озвучивание ответа
- `voca status` — текущее состояние, sessionId, profile
- `voca session new` — новый sessionId, счётчик сброшен
- `voca profile use public` — профиль переключён, сессия сброшена
- Тишина >30s при записи — отмена записи, возврат в IDLE
- Ошибка openclaw gateway — low tone, озвучивание "Сервер недоступен"
- Ctrl+C при `voca start` — чистое завершение всех child processes

## Материалы

- `docs/ai/claw-assistant-voice-daemon/claw-assistant-voice-daemon-task.md` — полная спецификация проекта
- `openclaw agent --help` — интерфейс CLI
- `whisper-stt-wrapper --help` — интерфейс STT (файл первым аргументом)
- `/home/priney/.openclaw/openclaw.json` — конфигурация openclaw (timeout 900s, агенты)
- `/home/priney/whisper-stt/.venv/` — образец Python venv для reference
