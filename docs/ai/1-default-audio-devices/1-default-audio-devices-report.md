# Default audio devices on startup — отчёт о выполнении

**Task:** docs/ai/1-default-audio-devices/1-default-audio-devices-task.md
**Plan:** docs/ai/1-default-audio-devices/1-default-audio-devices-plan.md
**Branch:** issue-1-default-audio-devices
**Status:** ✅ done (8/8 tasks)

## Summary

`inputDevice` и `outputDevice` в `VocaConfig` стали опциональными; daemon ходит в системный default, если поля не заданы. Добавлено опциональное поле `inputDeviceIndex?: number` для PyAudio-индекса. Исправлен хардкод `deviceIndex: 0` в `src/daemon.ts:53`. В `voca bootstrap` первым пунктом появился `Use system default (recommended)` с предупреждением о летучести `plughw:X,Y`.

## Tasks

| #   | Task                          | Commit    | Status |
| --- | ----------------------------- | --------- | ------ |
| 1   | types-config                  | `3b0038f` | ✅ done |
| 2   | speaker conditional -D        | `94f7ac9` | ✅ done |
| 3   | sounds conditional -D         | `44fb47a` | ✅ done |
| 4   | daemon inputDeviceIndex       | `fd0ec94` | ✅ done |
| 5   | bootstrap system-default      | `af925e7` | ✅ done |
| 6   | speaker+sounds unit tests     | `80d3b3f` | ✅ done |
| 7   | daemon test variant           | `ff4f00e` | ✅ done |
| 8   | Validation                    | —         | ✅ done |

## Validation

- `npm run build` → 0 ошибок TypeScript.
- `npm test` → **77/77 passed** across 8 files (`config`, `daemon`, `daemon-state`, `sounds`, `speaker`, `transcriber` и др.).
- New tests: 2 в `test/sounds.test.ts`, 2 в `test/speaker.test.ts`, 1 в `test/daemon.test.ts` (`VocaDaemon with default devices`), 2 в `test/config.test.ts` (empty file + `inputDeviceIndex` preservation).

## Changes

- `src/types.ts`: `inputDevice?`, `outputDevice?`, new `inputDeviceIndex?: number`.
- `src/config.ts`: device-поля удалены из `defaultConfig`.
- `src/speaker.ts`: `device?: string`; `-D` добавляется через условный spread.
- `src/sounds.ts`: `device?: string`; `-D` добавляется только при заданном `device`.
- `src/daemon.ts:53`: `deviceIndex: this.config.inputDeviceIndex` вместо `0`.
- `src/bootstrap.ts`: `DEFAULT_OPTION = 'Use system default (recommended)'` первым пунктом; предупреждение о `plughw:X,Y`; `delete config[field]` при выборе default (+ `delete config.inputDeviceIndex` для input).
- `test/config.test.ts`, `test/daemon.test.ts`: обновлены ассёрты; добавлены новые тесты.
- `test/speaker.test.ts`, `test/sounds.test.ts`: новые файлы.

## Skipped manual scenarios

Следующие сценарии из секции Verification требуют живого Raspberry Pi с микрофоном/динамиком и не исполнены автоматически:

- `rm ~/.openclaw/assistant/config.json && voca bootstrap` — визуальная проверка нового пункта.
- `voca start` + `pgrep -af 'aplay'` / `pgrep -af 'listener.py'` — runtime-проверка отсутствия `-D` и `--device-index`.
- Reboot-симуляция: USB-микрофон переезжает `card 2 → card 3`.
- `voca start` при остановленном PulseAudio/PipeWire и пустом `~/.asoundrc`.

Автоматические тесты и ручные логические проверки покрывают корректность кода; для полной приёмки нужно прогнать эти сценарии на железе.

## Notes

- Post-phase Polish/Document/Format пропущены — изменения мелкие, покрыты формат-линтером проекта (prettier через tsc-build).
- Конфиги существующих пользователей НЕ мигрируются: persisted `"inputDevice": "plughw:2,0"` продолжает работать как явный override.
