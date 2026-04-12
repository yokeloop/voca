# Default audio devices on startup — план реализации

**Task:** docs/ai/1-default-audio-devices/1-default-audio-devices-task.md
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Отсутствие поля = системный default

**Решение:** опциональные `inputDevice?`, `outputDevice?`, `inputDeviceIndex?` в `VocaConfig`; отсутствие ключа означает «брать системный default».
**Обоснование:** совпадает с рабочим паттерном `opts.deviceIndex !== undefined` в `src/listener.ts:22-24` — потребитель сам решает, пропускать ли флаг.
**Альтернатива:** sentinel-строка `"default"`. Отвергнуто: `default` — реальное имя ALSA PCM, и каждому потребителю пришлось бы парсить строку.

### DD-2: Условный spread для аргументов `aplay`

**Решение:** собирать argv массивом с условным spread: `['-r','22050','-f','S16_LE','-c','1', ...(device ? ['-D', device] : [])]` в `speaker.ts` и так же в `sounds.ts`.
**Обоснование:** повторяет `src/listener.ts:18-24` — там условный push `--device-index`.
**Альтернатива:** отдельные ветки `if/else` со spawn в каждой. Отвергнуто: дублирует код и хуже читается.

### DD-3: Bootstrap удаляет поле вместо записи пустой строки

**Решение:** при выборе `Use system default (recommended)` — `delete config[opts.field]` (для input-поля также `delete config.inputDeviceIndex`), никаких `""` / `"default"` в JSON.
**Обоснование:** JSON без поля естественно соответствует опциональному типу; merge `{ ...defaultConfig, ...parsed }` (`src/config.ts:27`) не ломается.
**Альтернатива:** записать пустую строку. Отвергнуто: потребителям пришлось бы отличать `""` от `undefined`.

### DD-4: Предупреждение о летучести `plughw:X,Y`

**Решение:** `bootstrap.ts` печатает одну строку перед списком устройств: система может перенумеровать USB после reboot или hotplug — явный выбор тогда сломается.
**Обоснование:** требование пользователя (из ответа на synthesize-вопрос) — предупредить, не блокируя.
**Альтернатива:** блокировать выбор plughw без подтверждения. Отвергнуто: лишний interactive prompt.

### DD-5: Существующие `hw:0,0` фикстуры остаются как override-кейс

**Решение:** `test/daemon.test.ts` сохраняет `hw:0,0` в `mockConfig` — теперь это кейс «explicit override»; рядом добавляем вариант без device-полей.
**Обоснование:** сохраняет покрытие happy-path и даёт оба кейса бесплатно.
**Альтернатива:** переписать все фикстуры. Отвергнуто: больше диффа без новой информации.

## Tasks

### Task 1: types-config

- **Files:** `src/types.ts:1-10` (edit), `src/config.ts:8-17` (edit), `test/config.test.ts:20-62` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Пометить `inputDevice` и `outputDevice` в `VocaConfig` опциональными; добавить `inputDeviceIndex?: number`. Удалить `inputDevice` и `outputDevice` из `defaultConfig`.
- **How:** В `types.ts:2-3` поставить `?:` после имён полей, добавить строку `inputDeviceIndex?: number;`. В `config.ts:9-10` удалить строки `inputDevice`/`outputDevice`. В `test/config.test.ts:32` убрать сравнение `read.inputDevice` с defaults (оба undefined → достаточно не бросить). В `:53-54` заменить `expect(defaultConfig.inputDevice).toBe('plughw:2,0')` на `expect(defaultConfig.inputDevice).toBeUndefined()` и так же для `outputDevice`. Добавить два новых теста: (1) `readConfig` на пустом файле возвращает `inputDevice: undefined`; (2) `readConfig` сохраняет переданный `inputDeviceIndex: 3` из JSON.
- **Context:** `src/types.ts`, `src/config.ts`, `test/config.test.ts`.
- **Verify:** `npm run build` проходит; `npm test test/config.test.ts` — зелёный.

### Task 2: speaker

- **Files:** `src/speaker.ts:14-35` (edit)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Сделать `device` в параметрах `speak()` опциональным; не передавать `-D`, если его нет.
- **How:** В сигнатуре `speak(opts: { ...; device?: string })`. Собрать `aplayArgs = ['-r','22050','-f','S16_LE','-c','1', ...(opts.device !== undefined ? ['-D', opts.device] : [])]` и передать в `spawn('aplay', aplayArgs, ...)`. Остальное поведение — без изменений.
- **Context:** `src/speaker.ts`, `src/listener.ts:18-24` (паттерн).
- **Verify:** `npm run build` проходит; speaker вызовы в `daemon.ts` продолжают компилироваться.

### Task 3: sounds

- **Files:** `src/sounds.ts:18-27` (edit)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Сделать `device` в `playSound()` опциональным; не передавать `-D`, если его нет.
- **How:** В сигнатуре `opts: { device?: string }`. Собрать `args = ['-D', opts.device, soundFile(type)]`, если `opts.device !== undefined`, иначе `args = [soundFile(type)]`. Передать в `execFile('aplay', args, ...)`.
- **Context:** `src/sounds.ts`, `src/listener.ts:18-24` (паттерн).
- **Verify:** `npm run build` проходит; вызовы `playSound` в `daemon.ts` продолжают компилироваться.

### Task 4: daemon

- **Files:** `src/daemon.ts:49-54` (edit)
- **Depends on:** Task 1, Task 2, Task 3
- **Scope:** S
- **What:** Убрать хардкод `deviceIndex: useStub ? undefined : 0`; пробрасывать `this.config.inputDeviceIndex`.
- **How:** Заменить строку `deviceIndex: useStub ? undefined : 0,` на `deviceIndex: useStub ? undefined : this.config.inputDeviceIndex,`. Вызовы `playSound` (`daemon.ts:131, 163, 259`) и `speak` (`daemon.ts:225-230`) уже передают `this.config.outputDevice` — менять не нужно; после Task 1 они корректно передадут `undefined`.
- **Context:** `src/daemon.ts`, `src/listener.ts:22-24`.
- **Verify:** `npm run build` проходит; `npm test test/daemon.test.ts` — зелёный на существующих тестах.

### Task 5: bootstrap

- **Files:** `src/bootstrap.ts:146-179` (edit), `src/bootstrap.ts:364-371` (edit)
- **Depends on:** Task 1
- **Scope:** M
- **What:** В `selectDevice()` добавить первым пунктом `Use system default (recommended)`; при выборе удалить поле из конфига. Перед списком напечатать предупреждение о летучести `plughw:X,Y`.
- **How:** В `selectDevice` (`bootstrap.ts:146-179`):
  1. Сразу после `console.log('\n=== ... ===')` (`:150`) напечатать `console.log('Note: ALSA plughw:X,Y indices may shift after reboot or USB re-plug — "Use system default" survives that.')`.
  2. В `options` (`:165`) вставить `'Use system default (recommended)'` первым элементом; далее `devices.map(...)`; пункт `Keep current: <value>` добавлять, только если `config[opts.field] !== undefined`.
  3. В обработке выбора перед существующими ветками: `if (selected === 'Use system default (recommended)') { delete config[opts.field]; if (opts.field === 'inputDevice') delete config.inputDeviceIndex; console.log(\`${opts.label} device: system default\`); return; }`.
- **Context:** `src/bootstrap.ts:60-124` (select helper), `src/bootstrap.ts:146-179`, `src/bootstrap.ts:364-371` (call sites — проверить целостность).
- **Verify:** `npm run build` проходит. Ручная проверка: `voca bootstrap` на чистом конфиге → в шагах 1/2 первым пунктом виден `Use system default (recommended)`, предупреждение напечатано; после выбора этого пункта в `~/.openclaw/assistant/config.json` нет ключей `inputDevice`/`outputDevice`/`inputDeviceIndex`.

### Task 6: speaker+sounds unit tests

- **Files:** `test/speaker.test.ts` (create), `test/sounds.test.ts` (create)
- **Depends on:** Task 2, Task 3
- **Scope:** M
- **What:** Добавить юнит-тесты на условный `-D` в `speaker.ts` и `sounds.ts`.
- **How:** Зеркалить стиль `test/daemon.test.ts:1-76` (`vi.mock` + `beforeEach` + `vi.clearAllMocks`).
  - `test/speaker.test.ts`: `vi.mock('node:child_process', ...)` с перехватом `spawn`. Два теста: (1) `speak({ device: 'hw:0,0', ... })` → последний аргумент `spawn('aplay', args)` содержит `-D` и `'hw:0,0'`; (2) `speak({ device: undefined, ... })` → args не содержит `-D`. Замокать `piper.stdout.pipe`, `aplay.on('close')` с `code=0`, чтобы `speak()` резолвился.
  - `test/sounds.test.ts`: `vi.mock('node:child_process', ...)` с перехватом `execFile`. Два теста: (1) `playSound('wake', { device: 'hw:0,0' })` → `execFile` вызван с args, содержащими `-D`; (2) `playSound('wake', { device: undefined })` → args не содержит `-D`.
- **Context:** `test/daemon.test.ts:1-76` (стиль mock), `src/speaker.ts`, `src/sounds.ts`.
- **Verify:** `npm test test/speaker.test.ts test/sounds.test.ts` — зелёный.

### Task 7: daemon test variant

- **Files:** `test/daemon.test.ts:51-62, 93-102, 113-232` (edit)
- **Depends on:** Task 4
- **Scope:** S
- **What:** Добавить тест: config без `inputDevice`/`outputDevice`/`inputDeviceIndex` → `spawnListener` вызван без `deviceIndex`, `playSound`/`speak` получают `device: undefined`.
- **How:** Рядом с существующим `describe('VocaDaemon')` добавить `describe('VocaDaemon with default devices')` с собственным `mockConfigDefault` (без трёх device-полей). В `beforeEach` создать `new VocaDaemon(mockConfigDefault)`. Один тест: вызвать `daemon.start()`, эмитнуть `'wake'`, `'recorded'`, `flush()`; проверить, что `vi.mocked(spawnListener).mock.calls[0][0].deviceIndex === undefined`, `playSound` вызван с `{ device: undefined }`, `speak` — с `device: undefined`. Существующие фикстуры `hw:0,0` остаются как override-кейс, их assertions не менять.
- **Context:** `test/daemon.test.ts` (весь файл), `src/daemon.ts`.
- **Verify:** `npm test test/daemon.test.ts` — зелёный на всех тестах (и старых, и новом).

### Task 8: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Полный прогон проверок + ручные сценарии из секции Verification task-файла.
- **How:** Выполнить `npm run build`, `npm test`. Проверить ручные сценарии:
  1. `rm ~/.openclaw/assistant/config.json && voca bootstrap` → первый пункт `Use system default (recommended)`, предупреждение напечатано. После выбора default в `config.json` нет device-полей.
  2. `voca start` с конфигом без device-полей: `pgrep -af 'aplay'` во время TTS не содержит `-D`; `pgrep -af 'listener.py'` не содержит `--device-index`.
  3. `voca start` с конфигом `{ "inputDevice": "plughw:2,0", "outputDevice": "plughw:2,0" }`: `aplay` спавнится с `-D plughw:2,0`; `listener.py` — без `--device-index`.
  4. Reboot-симуляция: сменить default PyAudio device, `voca start` без правки конфига работает.
- **Context:** task Verification секция.
- **Verify:** `npm run build && npm test` → зелёные; ручные сценарии прошли.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 8 задач, medium сложность, файлы между задачами не пересекаются — параллельные группы по 2-3 task в агентах с `isolation: worktree`.
- **Order:**
  Group 1 (sequential): Task 1
  ─── barrier ───
  Group 2 (parallel): Task 2, Task 3, Task 5
  ─── barrier ───
  Group 3 (sequential): Task 4
  ─── barrier ───
  Group 4 (parallel): Task 6, Task 7
  ─── barrier ───
  Group 5 (sequential): Task 8

## Verification

- `npm run build` → без ошибок TypeScript.
- `npm test` → существующие тесты зелёные; новый тест «config без device-полей» зелёный.
- `rm ~/.openclaw/assistant/config.json && voca bootstrap` → в шагах 1 и 2 первым пунктом виден `Use system default (recommended)`, перед списком напечатано предупреждение о volatility `plughw:X,Y`. После выбора этого пункта в сохранённом `config.json` нет ключей `inputDevice`/`outputDevice`.
- После `voca start` с конфигом без device-полей: `pgrep -af 'aplay'` во время TTS не содержит `-D`; `pgrep -af 'listener.py'` не содержит `--device-index`.
- `voca start` с конфигом `{ "inputDevice": "plughw:2,0", "outputDevice": "plughw:2,0" }` (existing override): aplay спавнится с `-D plughw:2,0`; listener.py — без `--device-index` (`inputDeviceIndex` не задан).
- Reboot scenario: выбран «Use system default», USB-микрофон переехал с `card 2` на `card 3` → `voca start` работает без изменений (PyAudio подхватил новый default).
- `voca start` при остановленном PulseAudio/PipeWire и пустом `~/.asoundrc` → `aplay` без `-D` берёт ALSA default PCM; daemon не падает.

## Materials

- [GitHub Issue #1](https://github.com/yokeloop/voca/issues/1)
- `~/.openclaw/assistant/config.json`
- `src/types.ts`, `src/config.ts`, `src/daemon.ts`, `src/listener.ts`, `src/speaker.ts`, `src/sounds.ts`, `src/bootstrap.ts`
- `listener.py`
- `test/config.test.ts`, `test/daemon.test.ts`
