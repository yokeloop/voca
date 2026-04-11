# Review: voca-implementation

**Plan:** docs/ai/voca-implementation/voca-implementation-plan.md
**Status:** ✅ All issues fixed

## Summary

Full VOCA voice daemon implementation — 13 TypeScript modules, Python listener, 3 WAV files, 6 test files. State machine architecture with child process orchestration for wake word detection, recording, transcription, agent query, and TTS.

## Issues Found

| # | Severity | Score | Category | File | Description |
|---|---|---|---|---|---|
| 1 | Critical | 95 | bugs | `src/recorder.ts:55` | stop() fires cancel instead of done |
| 2 | Critical | 92 | bugs | `src/daemon.ts:110` | Listener paused during RECORDING blocks stop events |
| 3 | Critical | 90 | bugs | `src/daemon.ts:44` | stub: true hardcoded, real mode unreachable |
| 4 | Critical | 88 | bugs | `src/config.ts:14` | piperModel bare name, piper needs .onnx path |
| 5 | Critical | 85 | bugs | `src/bootstrap.ts:171` | Stop-word ONNX model never downloaded |
| 6 | Important | 75 | bugs | `src/agent.ts:7` | Hardcoded machine-specific openclaw path |
| 7 | Important | 72 | bugs | `src/listener.ts:17` | No --device-index in real mode |
| 8 | Important | 70 | bugs | `src/daemon.ts` | Listener crash not handled |
| 9 | Important | 65 | bugs | `src/daemon.ts:145` | Temp WAV files never cleaned up |
| 10 | Important | 60 | quality | `src/daemon.ts:175` | Bypasses state machine on error |
| 11 | Important | 58 | tests | `test/agent.test.ts:84` | Hardcoded path in assertion |
| 12 | Minor | 40 | tests | — | No daemon.ts unit tests |
| 13 | Minor | 35 | style | `src/daemon.ts:44` | stub: true without explanation |
| 14 | Minor | 30 | documentation | `src/recorder.ts` | stop() vs cancel() semantics undocumented |
| 15 | Minor | 25 | quality | `src/bootstrap.ts:147` | pip reinstalls every bootstrap run |

## Fixed Issues

All 15 issues fixed in commit `9691148`.

| # | Fix Applied |
|---|---|
| 1 | Recorder exit handler checks `stopped` flag; stop() now emits 'done' |
| 2 | Listener pause moved from handleWake() to before speak() only |
| 3 | Dynamic stub detection: checks venv existence at runtime |
| 4 | piperModel default changed to full .onnx path |
| 5 | stop.onnx added to model download in bootstrap |
| 6 | OPENCLAW_BIN uses PATH lookup via env var fallback |
| 7 | deviceIndex option added to ListenerOptions, passed to listener.py |
| 8 | ListenerHandle.on() extended with 'exit' event, daemon handles it |
| 9 | fs.unlink(filePath) in finally block after transcription |
| 10 | recoverFromError() uses transition() first, fallback only on failure |
| 11 | Test imports OPENCLAW_BIN constant instead of hardcoding |
| 12 | Created test/daemon.test.ts with 5 tests (happy path, cancel, stop, ignore, cleanup) |
| 13 | Addressed by fix #3 (dynamic stub detection) |
| 14 | JSDoc added to stop() and cancel() methods |
| 15 | pip install skipped when openwakeword already installed |

## Skipped Issues

All found issues fixed.

## Validation

```
npm run build ✅
npm test ✅ (67 passed, 0 failed)
```

## Commits

- `9691148` fix(voca-implementation): fix 15 review issues
