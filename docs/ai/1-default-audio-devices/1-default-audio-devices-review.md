# Code review — 1-default-audio-devices

**Branch:** issue-1-default-audio-devices
**Scope:** commits `3b0038f..HEAD`
**Status:** ✅ 7/7 issues fixed, 78 tests pass, build clean.

## Summary

Реализация покрывает R1–R8 и ограничения (no CLI flags, no sentinel, recorder.ts нетронут, без миграции конфигов, listener protocol неизменён). Review выявил 3 Important (bootstrap UX + 1 gap в тесте daemon) и 4 Minor; всё починено в 4 коммитах.

## Issues

| # | Severity | Category | Location | Description | Status |
|---|---|---|---|---|---|
| 1 | Important | design | `src/bootstrap.ts:160-179` | Early return на пустом списке ALSA устройств блокировал выбор "Use system default". | ✅ fixed (`468575e`) |
| 2 | Important | design | `src/bootstrap.ts` input branch | `config.inputDevice = 'plughw:X,Y'` писалось, но runtime читает только `inputDeviceIndex`. UX misleading. | ✅ fixed (`468575e`) — input branch теперь предлагает только DEFAULT + Keep current, печатает подсказку про ручной override `inputDeviceIndex`. |
| 3 | Important | test | `test/daemon.test.ts` | Ассёрт `device: undefined` в prop-bag не ловил регрессию `-D undefined` в argv. | ✅ fixed (`c2fd98e`) — теперь проверяется наличие ключа `device` и строгое значение `undefined`. |
| 4 | Minor | docs | `src/bootstrap.ts:153` | Warning про `plughw:X,Y` печатался перед early-skip. | ✅ fixed (`468575e`) — перенесён в output branch. |
| 5 | Minor | style | `src/bootstrap.ts` | Безусловный `delete config.inputDeviceIndex` в default handler. | ✅ subsumed by fix #2. |
| 6 | Minor | style | `src/speaker.ts:28-33` | Порядок `-D` отличался от `sounds.ts`. | ✅ fixed (`fc338f4`). |
| 7 | Minor | test | `test/config.test.ts:20-23` | `toEqual(defaultConfig)` не фиксировал отсутствие device-ключей в JSON. | ✅ fixed (`b37627e`) — новый тест проверяет `'inputDevice' in serialized === false`. |

## Commits

```
b37627e test(1-default-audio-devices): assert device keys absent from defaultConfig
c2fd98e test(1-default-audio-devices): tighten default-device assertions
fc338f4 refactor(1-default-audio-devices): match sounds argv order in speaker
468575e fix(1-default-audio-devices): separate input and output device bootstrap flows
```

## Validation

- `npm run build` → clean.
- `npm test` → 78/78 passed (было 77, +1 новый `defaultConfig JSON` тест).
