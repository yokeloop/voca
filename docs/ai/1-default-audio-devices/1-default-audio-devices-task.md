# Use system default audio input/output devices on startup

**Slug:** 1-default-audio-devices
**Тикет:** https://github.com/yokeloop/voca/issues/1
**Сложность:** medium
**Тип:** general

## Task

Сделать `inputDevice` и `outputDevice` в `~/.openclaw/assistant/config.json` опциональными; если они не заданы — daemon берёт системный default для микрофона (PyAudio) и динамика (aplay без `-D`). Заодно починить баг в `src/daemon.ts:53`: listener.py получает жёстко зашитый `deviceIndex: 0` вместо значения из конфига.

## Context

### Архитектура области

Поток микрофона: `daemon.ts:49-54` спавнит `listener.py` через `spawnListener()` (`src/listener.ts:14-74`). `listener.py:73-80` открывает PyAudio-стрим с `input_device_index=<int|None>`; при `None` PyAudio сам берёт системный default.

Поток динамика: два потребителя ALSA `aplay -D <device>`:

- `src/speaker.ts:28-35` — piper → aplay для TTS (используется в `daemon.ts:225-230`).
- `src/sounds.ts:18-28` — `playSound('wake'|'stop'|'error')` (используется в `daemon.ts:131, 163, 259`).

`voca start` читает config один раз через `readConfig()` (`src/config.ts:23-34`), передаёт в `VocaDaemon` (`src/cli.ts:93-94`, `src/daemon.ts:30-33`) и раскладывает по потребителям.

### Файлы для изменения

- `src/types.ts:1-10` — сделать `inputDevice?: string`, `outputDevice?: string`; добавить `inputDeviceIndex?: number` (для PyAudio).
- `src/config.ts:8-17` — убрать `'plughw:2,0'` из `defaultConfig`; поля `inputDevice`, `outputDevice`, `inputDeviceIndex` в дефолтах отсутствуют.
- `src/daemon.ts:49-54` — заменить `deviceIndex: useStub ? undefined : 0` на `deviceIndex: useStub ? undefined : this.config.inputDeviceIndex` (`listener.ts:22-24` корректно пропускает undefined).
- `src/speaker.ts:14-35` — сделать `device?: string` в `opts`; при `undefined` не передавать `-D` в `aplay`. Массив аргументов собирать через условный spread.
- `src/sounds.ts:18-28` — сделать `device?: string`; при `undefined` не передавать `-D`.
- `src/bootstrap.ts:146-179` — в `selectDevice()` первым пунктом поставить `Use system default (recommended)`. При выборе удалить поле из `config` через `delete config[opts.field]`. Перед списком вывести одной строкой предупреждение: ALSA-идентификатор `plughw:X,Y` может измениться после перезагрузки или переподключения USB — тогда явный выбор сломается.
- `src/cli.ts` — в действии команды `start` ничего не менять; daemon уже читает опциональный конфиг.

### Паттерны для повторения

- Условные CLI-аргументы: `src/listener.ts:22-24` — `if (!opts.stub && opts.deviceIndex !== undefined) args.push(...)`. Повторить паттерн в `speaker.ts`/`sounds.ts` для `-D`.
- `VocaConfig` уже использует опциональные поля через spread с defaults (`src/config.ts:27`). Новые опциональные поля merge не ломают.
- Интерактивный `select()` в `src/bootstrap.ts:60-124` принимает список строк — достаточно добавить пункт в начало массива `options` в `selectDevice()`.

### Тесты

- `test/config.test.ts:20-62` — 5 тестов проверяют defaults и merge. Переписать под «в defaults этих полей нет»: `expect(read.inputDevice).toBe(defaultConfig.inputDevice)` на `:32` и `expect(defaultConfig.inputDevice).toBe('plughw:2,0')` / `outputDevice` на `:53-54`.
- `test/daemon.test.ts:53-54, 94-95` — fixtures с `inputDevice: 'hw:0,0'` и `outputDevice: 'hw:0,0'` остаются валидными (теперь это кейс «explicit override»). Добавить тест: config без `inputDevice`/`outputDevice`/`inputDeviceIndex` → listener спавнится без `--device-index`, speaker/sounds спавнят `aplay` без `-D`.
- Ручная проверка на Raspberry Pi: `voca start` с чистым конфигом → wake word срабатывает через системный микрофон по умолчанию; TTS и beep звучат через системный динамик.

## Requirements

1. `VocaConfig.inputDevice` и `VocaConfig.outputDevice` — опциональные (`string | undefined`). Добавить опциональное поле `inputDeviceIndex?: number` для передачи в PyAudio.
2. `defaultConfig` в `src/config.ts` больше не содержит `inputDevice`, `outputDevice`, `inputDeviceIndex`. Существующие конфиги с `"inputDevice": "plughw:2,0"` читаются как явный override — merge через `{ ...defaultConfig, ...parsed }` работает без изменений.
3. Если `config.outputDevice` не задан — `src/speaker.ts` и `src/sounds.ts` спавнят `aplay` **без флага `-D`** (и без `-D default`). ALSA возьмёт `pcm.!default` из `~/.asoundrc` / PipeWire / PulseAudio.
4. Если `config.inputDeviceIndex` не задан — `src/daemon.ts:49-54` передаёт `deviceIndex: undefined` в `spawnListener`; `listener.py` получает `input_device_index=None`, и PyAudio сам берёт системный default.
5. `src/daemon.ts:53` не содержит хардкод `0`; индекс приходит только из `this.config.inputDeviceIndex`.
6. `voca bootstrap`, шаги выбора микрофона и динамика: первый пункт списка — `Use system default (recommended)`, при выборе соответствующее поле удаляется из config. Перед списком печатается предупреждение об изменчивости `plughw:X,Y` после перезагрузки и переподключения USB.
7. `voca start` на свежей установке (config без device-полей) поднимает daemon, реагирует на wake word и отвечает через TTS без ручного запуска `voca bootstrap`.
8. Существующие юнит-тесты обновлены под новые дефолты; добавлен хотя бы один тест на сценарий «нет device-полей → spawn без `-D` и без `--device-index`».

## Constraints

- Не трогать `src/recorder.ts` — dead code (recording живёт в `listener.py`); возиться с ним вне скоупа.
- Не добавлять CLI-флаги вида `voca start --input-device ...` — override идёт только через `~/.openclaw/assistant/config.json`.
- Не мигрировать и не переписывать существующие `config.json`: поля со значениями остаются как «explicit override». Никаких предупреждений и миграций при `voca start`.
- Не вводить sentinel-строку `"default"` — опциональности достаточно.
- Не менять формат `plughw:X,Y` и не пытаться резолвить строку ALSA в индекс PyAudio: это два независимых поля (`outputDevice` для aplay, `inputDeviceIndex` для PyAudio).
- Не менять протокол listener.py ↔ listener.ts (JSON events, сигналы). Только аргументы запуска.
- Не ломать stub-режим listener (`--stub` в `src/listener.ts:18-20`).

## Verification

- `npm run build` → без ошибок TypeScript.
- `npm test` → существующие тесты зелёные; новый тест «config без device-полей» зелёный.
- `rm ~/.openclaw/assistant/config.json && voca bootstrap` → в шагах 1 и 2 первым пунктом виден `Use system default (recommended)`, перед списком напечатано предупреждение о volatility `plughw:X,Y`. После выбора этого пункта в сохранённом `config.json` нет ключей `inputDevice`/`outputDevice`.
- После `voca start` с конфигом без device-полей: `pgrep -af 'aplay'` во время TTS не содержит `-D`; `pgrep -af 'listener.py'` не содержит `--device-index`.
- `voca start` с конфигом `{ "inputDevice": "plughw:2,0", "outputDevice": "plughw:2,0" }` (existing override): aplay спавнится с `-D plughw:2,0`; listener.py — без `--device-index` (`inputDeviceIndex` не задан).
- Reboot scenario: выбран «Use system default», реальный USB-микрофон переехал с `card 2` на `card 3` → `voca start` работает без изменений (PyAudio подхватил новый default).
- `voca start` при остановленном PulseAudio/PipeWire и пустом `~/.asoundrc` → `aplay` без `-D` берёт ALSA default PCM; daemon не падает (возможна тишина, но spawn без ошибок).

## Материалы

- [GitHub Issue #1](https://github.com/yokeloop/voca/issues/1)
- `~/.openclaw/assistant/config.json`
- `src/types.ts`, `src/config.ts`, `src/daemon.ts`, `src/listener.ts`, `src/speaker.ts`, `src/sounds.ts`, `src/bootstrap.ts`
- `listener.py`
- `test/config.test.ts`, `test/daemon.test.ts`
