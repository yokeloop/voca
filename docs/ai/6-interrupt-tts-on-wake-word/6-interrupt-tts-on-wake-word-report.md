# Execution Report — Interrupt TTS on wake word

**Slug:** 6-interrupt-tts-on-wake-word
**Тикет:** https://github.com/yokeloop/voca/issues/6
**Branch:** issue-6-interrupt-tts
**Status:** ✅ done (7/7 tasks)

## Commits

| SHA | Task | Scope |
|-----|------|-------|
| 3a635cf | T1 | `src/types.ts` — `WAKE_INTERRUPT` event, `speakingStart/speakingEnd` on `ListenerHandle` |
| aaf364c | T2 | `src/daemon-state.ts` + state test — `SPEAKING+WAKE_INTERRUPT → RECORDING` |
| 7dcc68f | T3 | `src/speaker.ts` — `speak()` returns `SpeechHandle { done, interrupt }` |
| a00e5e1 | T4 | `src/listener.ts` — `speakingStart/speakingEnd` send `SIGRTMIN` / `SIGRTMIN+1` |
| cd63252 | T5 | `listener.py` — `speaking` flag, `SPEAKING_THRESHOLD=0.85`, `SIGRTMIN` handlers |
| 7be0a44 | T6 | `src/daemon.ts` — `activeSpeech`, `speakingStart/End` around `speak`, interrupt branch in `handleWake` |
| c44cd88 | T7 | `test/daemon.test.ts` — interrupt scenario + `speakingEnd` on reject |

## Verification

- `npx tsc --noEmit` → ✅ no errors
- `npx vitest run` → ✅ 77/77 tests pass (6 files, including 2 new interrupt cases and expanded state transitions)
- `python3 -m py_compile listener.py` → ✅ OK

## Requirements coverage

1. ✅ `WAKE_INTERRUPT` event + `SPEAKING+WAKE_INTERRUPT → RECORDING` transition
2. ✅ `speak()` returns `SpeechHandle` with synchronous `interrupt()` that kills piper+aplay and resolves `done`
3. ✅ `VocaDaemon.activeSpeech` stores handle during SPEAKING, cleared after
4. ✅ `handleWake` SPEAKING branch: interrupt → transition → `pause()` (SIGUSR1); no `playSound('wake')`
5. ✅ `listener.ts` exports `speakingStart/speakingEnd` sending `SIGRTMIN` / `SIGRTMIN+1`
6. ✅ `listener.py` raises wake threshold to `0.85` while `speaking=True`; stop-word detection disabled during SPEAKING
7. ✅ daemon calls `speakingStart()` before `speak()` and `speakingEnd()` in success / error / interrupt paths
8. ✅ Interrupt path is synchronous (kill + resolve immediate); no wake-sound latency
9. ✅ Tests cover state transition, interrupt, absence of wake-sound, `speakingEnd` on reject

## Notes

- Interrupt path re-uses `listener.pause()` (SIGUSR1 → `start_recording()` in listener.py). Daemon lands in `RECORDING`; recording pipeline proceeds as normal via `mockListenerHandle.emit('recorded', ...)` in production `listener.py` writes a WAV.
- `onRecorderDone` after `await speak` checks if state changed away from SPEAKING — if interrupt fired, `handleWake` already transitioned to RECORDING, so `SPEAKING_DONE` is skipped.
- `speakingEnd()` runs on every exit from the speak block (success, error, interrupt) to keep listener.py state consistent.
- `SIGRTMIN` resolved via `os.constants.signals.SIGRTMIN` with numeric fallback (34) for robustness on unusual platforms.

## Remaining concerns

- Manual smoke test against real microphone + openWakeWord not performed (no hardware access in this session). Acceptance criterion "~200ms latency" and "self-trigger rate acceptable" remain to be validated on device.
