# Code review — 1-default-audio-devices

**Branch:** issue-1-default-audio-devices
**Scope:** commits `3b0038f..HEAD`
**Status:** 7/7 issues fixed, 78 tests pass, build clean.

## Summary

The implementation covers R1–R8 and the constraints (no CLI flags, no sentinel, `recorder.ts` untouched, no config migration, listener protocol unchanged). The review surfaced 3 Important issues (bootstrap UX + 1 gap in a daemon test) and 4 Minor ones; all were fixed across 4 commits.

## Issues

| # | Severity | Category | Location | Description | Status |
|---|---|---|---|---|---|
| 1 | Important | design | `src/bootstrap.ts:160-179` | Early return on an empty ALSA device list blocked the "Use system default" selection. | fixed (`468575e`) |
| 2 | Important | design | `src/bootstrap.ts` input branch | `config.inputDevice = 'plughw:X,Y'` was written, but the runtime reads only `inputDeviceIndex`. Misleading UX. | fixed (`468575e`) — the input branch now offers only DEFAULT + Keep current and prints a hint about manually overriding `inputDeviceIndex`. |
| 3 | Important | test | `test/daemon.test.ts` | The `device: undefined` assertion in the prop-bag did not catch a `-D undefined` regression in argv. | fixed (`c2fd98e`) — now both the presence of the `device` key and its strict `undefined` value are checked. |
| 4 | Minor | docs | `src/bootstrap.ts:153` | The `plughw:X,Y` warning was printed before the early skip. | fixed (`468575e`) — moved into the output branch. |
| 5 | Minor | style | `src/bootstrap.ts` | Unconditional `delete config.inputDeviceIndex` in the default handler. | subsumed by fix #2. |
| 6 | Minor | style | `src/speaker.ts:28-33` | `-D` ordering differed from `sounds.ts`. | fixed (`fc338f4`). |
| 7 | Minor | test | `test/config.test.ts:20-23` | `toEqual(defaultConfig)` did not pin the absence of device keys in JSON. | fixed (`b37627e`) — a new test checks `'inputDevice' in serialized === false`. |

## Commits

```
b37627e test(1-default-audio-devices): assert device keys absent from defaultConfig
c2fd98e test(1-default-audio-devices): tighten default-device assertions
fc338f4 refactor(1-default-audio-devices): match sounds argv order in speaker
468575e fix(1-default-audio-devices): separate input and output device bootstrap flows
```

## Validation

- `npm run build` → clean.
- `npm test` → 78/78 passed (was 77, +1 new `defaultConfig JSON` test).
