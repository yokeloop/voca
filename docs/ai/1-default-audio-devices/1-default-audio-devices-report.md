# Default audio devices on startup — execution report

**Task:** docs/ai/1-default-audio-devices/1-default-audio-devices-task.md
**Plan:** docs/ai/1-default-audio-devices/1-default-audio-devices-plan.md
**Branch:** issue-1-default-audio-devices
**Status:** done (8/8 tasks)

## Summary

`inputDevice` and `outputDevice` in `VocaConfig` became optional; the daemon falls back to the system default when they are not set. Added an optional `inputDeviceIndex?: number` field for the PyAudio index. Fixed the hardcoded `deviceIndex: 0` in `src/daemon.ts:53`. In `voca bootstrap` the first item is now `Use system default (recommended)` with a warning about `plughw:X,Y` volatility.

## Tasks

| #   | Task                          | Commit    | Status |
| --- | ----------------------------- | --------- | ------ |
| 1   | types-config                  | `3b0038f` | done   |
| 2   | speaker conditional -D        | `94f7ac9` | done   |
| 3   | sounds conditional -D         | `44fb47a` | done   |
| 4   | daemon inputDeviceIndex       | `fd0ec94` | done   |
| 5   | bootstrap system-default      | `af925e7` | done   |
| 6   | speaker+sounds unit tests     | `80d3b3f` | done   |
| 7   | daemon test variant           | `ff4f00e` | done   |
| 8   | Validation                    | —         | done   |

## Validation

- `npm run build` → 0 TypeScript errors.
- `npm test` → **77/77 passed** across 8 files (`config`, `daemon`, `daemon-state`, `sounds`, `speaker`, `transcriber`, and others).
- New tests: 2 in `test/sounds.test.ts`, 2 in `test/speaker.test.ts`, 1 in `test/daemon.test.ts` (`VocaDaemon with default devices`), 2 in `test/config.test.ts` (empty file + `inputDeviceIndex` preservation).

## Changes

- `src/types.ts`: `inputDevice?`, `outputDevice?`, new `inputDeviceIndex?: number`.
- `src/config.ts`: device fields removed from `defaultConfig`.
- `src/speaker.ts`: `device?: string`; `-D` is added via a conditional spread.
- `src/sounds.ts`: `device?: string`; `-D` is added only when `device` is set.
- `src/daemon.ts:53`: `deviceIndex: this.config.inputDeviceIndex` instead of `0`.
- `src/bootstrap.ts`: `DEFAULT_OPTION = 'Use system default (recommended)'` as the first item; warning about `plughw:X,Y`; `delete config[field]` on default selection (+ `delete config.inputDeviceIndex` for input).
- `test/config.test.ts`, `test/daemon.test.ts`: assertions updated; new tests added.
- `test/speaker.test.ts`, `test/sounds.test.ts`: new files.

## Skipped manual scenarios

The following scenarios from the Verification section require a live Raspberry Pi with microphone/speaker and were not executed automatically:

- `rm ~/.openclaw/assistant/config.json && voca bootstrap` — visual check of the new item.
- `voca start` + `pgrep -af 'aplay'` / `pgrep -af 'listener.py'` — runtime check that `-D` and `--device-index` are absent.
- Reboot simulation: USB microphone moves `card 2 → card 3`.
- `voca start` with PulseAudio/PipeWire stopped and an empty `~/.asoundrc`.

Automated tests and manual logical checks cover code correctness; for full acceptance these scenarios must be run on hardware.

## Notes

- Post-phase Polish/Document/Format were skipped — changes are small and covered by the project formatter/linter (prettier via tsc-build).
- Existing user configs are NOT migrated: a persisted `"inputDevice": "plughw:2,0"` keeps working as an explicit override.
