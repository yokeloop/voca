# CLAUDE.md

## Проект

VOCA (Voice Operated Claw Assistant) — Node.js CLI-демон для голосового управления [OpenClaw](https://github.com/yokeloop/openclaw). Слушает wake word, записывает речь, транскрибирует через Whisper, отправляет в OpenClaw agent, озвучивает ответ через Piper TTS. Написан на TypeScript, публикуется как `@yokeloop/voca`.

## Архитектура

```
src/
  cli.ts          # Точка входа (commander) — команды bootstrap/start/stop/status/session/profile
  daemon.ts       # State machine: IDLE → LISTENING → RECORDING → PROCESSING → SPEAKING → IDLE
  listener.ts     # Запуск и управление listener.py (openWakeWord child process)
  recorder.ts     # arecord child process, запись в временный WAV
  transcriber.ts  # Вызов whisper-stt
  agent.ts        # Вызов openclaw agent CLI
  speaker.ts      # Пайп piper | aplay (без промежуточных файлов)
  session.ts      # Управление сессиями и профилями
  config.ts       # Чтение/запись ~/.openclaw/assistant/config.json
  sounds.ts       # Звуковые индикаторы (beep/double-beep/error tone)
  bootstrap.ts    # Интерактивная настройка: mic, speaker, зависимости
listener.py       # Python-скрипт openWakeWord — эмитирует JSON lines на stdout
sounds/           # Дефолтные звуки: wake.wav, stop.wav, error.wav
```

**Data flow:** Mic → `listener.py` (JSON lines) → `daemon.ts` state machine → `recorder.ts` (WAV) → `transcriber.ts` (текст) → `agent.ts` (OpenClaw :18789) → `speaker.ts` (piper | aplay)

**Runtime data** хранится в `~/.openclaw/assistant/` (не в репозитории):
```
~/.openclaw/assistant/
  config.json     # inputDevice, outputDevice, profile, wakeWord, stopWord, piper model
  session.json    # sessionId (asst-<unix-ts>), messageCount, profile, createdAt
  sounds/         # Копируется при bootstrap
  models/         # Wake word .onnx модели
  venv/           # Python venv для openWakeWord
  bin/            # piper бинарник + модели
```

## Команды

Проект ещё не имеет `package.json`. После создания:

```bash
npm install                          # установить зависимости
npm run build                        # tsc → dist/
npm test                             # unit-тесты (state machine, config, session)
npm run lint                         # eslint + prettier (будут настроены после npm init)
```

CLI после глобальной установки:

```bash
npm publish --access public          # публикация пакета @yokeloop/voca в npm registry
npm install -g @yokeloop/voca

voca bootstrap                       # интерактивная настройка mic/speaker/зависимостей
voca start                           # запустить демон (foreground)
voca start --daemon                  # запустить как фоновый процесс
voca stop                            # остановить демон
voca status                          # текущее состояние: IDLE/LISTENING/RECORDING/PROCESSING/SPEAKING

voca session new                     # новый sessionId, сброс счётчика сообщений
voca session info                    # sessionId, профиль, количество сообщений

voca profile list                    # список профилей: personal, public
voca profile use personal            # переключить профиль (сбрасывает сессию)
```

Интеграционные инструменты (уже установлены):

```bash
openclaw agent --agent personal --session-id asst-<ts> --message "<text>" --json
whisper-stt --language ru <file.wav>
echo "<text>" | piper --model ru_RU-irina-medium --output_raw | aplay -r 22050 -f S16_LE -c 1
# Примечание: piper не установлен глобально — устанавливается в ~/.openclaw/assistant/bin/ через `voca bootstrap`
```


## Conventions

- TypeScript, Node.js 20+, ES modules
- Именование: camelCase для переменных/функций, PascalCase для классов
- Файлы: camelCase (`cli.ts`, `daemon.ts`, `listener.ts`)
- Зависимости минимальны: только `commander`, `tsx` (dev) — без express, socket.io и т.п.
- Коммиты: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`)

## Non-obvious

- **listener.py не перезапускается между итерациями** — процесс живёт всё время работы демона. Во время состояния SPEAKING он паузируется (SIGSTOP/SIGCONT или пауза через stdin), чтобы не сработал на собственный голос.

- **OpenClaw интегрируется только через CLI**, не через API напрямую. Путь бинаря: `/home/priney/.npm-global/bin/openclaw`. Флаг `--json` возвращает ответ как JSON. Таймаут 900s (не дефолтный 600s) — агент может долго думать.

- **TTS без промежуточных файлов** — `piper` пайпится напрямую в `aplay` (`--output_raw | aplay -r 22050 -f S16_LE -c 1`). После окончания воспроизведения — пауза 500ms перед возобновлением openWakeWord.

- **Запись отменяется** без отправки в агент при тишине >30s или длительности >2min — возврат в IDLE. "stop" на конце транскрипции обрезается перед отправкой.

- **Смена профиля сбрасывает сессию** — `voca profile use public` создаёт новый `sessionId`. Session format: `asst-<unix-ts>`.

- **Bootstrap устанавливает зависимости поэтапно** — piper (если отсутствует), Python venv в `~/.openclaw/assistant/venv/`, openWakeWord, ONNX-модели. Перед каждой установкой запрашивается подтверждение.

- **Звуковые индикаторы**: один beep — wake word, два beep — stop-фраза, низкий тон — ошибка. Файлы по умолчанию в `sounds/`, копируются в `~/.openclaw/assistant/sounds/` при bootstrap.
