# Code Review — Interrupt TTS on wake word

**Slug:** 6-interrupt-tts-on-wake-word
**Scope:** `main..issue-6-interrupt-tts` (7 feature + 1 review-fix commits)
**Outcome:** ✅ 4/4 issues fixed

## Summary

Feature implementation соответствует плану и ТЗ:
- State-машина расширена `SPEAKING+WAKE_INTERRUPT → RECORDING`.
- Speaker экспортирует `SpeechHandle` с синхронным `interrupt()`.
- Daemon хранит `activeSpeech`, шлёт `speakingStart/End` в listener.py на всех путях выхода из SPEAKING (success, error, interrupt).
- listener.py повышает wake-threshold до 0.85 и отключает stop-детекцию во время SPEAKING.

Критических проблем не найдено. 4 minor/important замечания устранены в `8b0fccc`.

## Issues

| # | Severity | Status | File:line | Description | Fix |
|---|----------|--------|-----------|-------------|-----|
| 1 | Important | ✅ Fixed | `CLAUDE.md:89` | Документация описывала SIGSTOP/SIGCONT как механизм подавления самотриггера — устарело. | Заменено на описание SIGRTMIN/+1, SPEAKING_THRESHOLD и interrupt-flow. |
| 2 | Minor | ✅ Fixed | `src/daemon.ts:86-95` | `daemon.stop()` не прерывал активную речь — piper/aplay оставались зомби при shutdown во время SPEAKING. | Добавлен `this.activeSpeech?.interrupt()` перед `listener.kill()`. |
| 3 | Minor | ✅ Fixed | `src/daemon.ts:146-158` | `handleStop` не имел явного гарда для SPEAKING/IDLE/PROCESSING. | Добавлен `else` с логом; поведение идентичное (listener.py уже отключает stop в SPEAKING), но явно. |
| 4 | Minor | ✅ Fixed | `src/listener.ts:8-9` | `SIGRTMIN` fallback `?? 34` недокументирован. | Добавлен комментарий про Linux-таргет и glibc default. |

## Validation

- `npx vitest run` → 77/77 passed (6 files)
- `npx tsc --noEmit` → clean
- `python3 -m py_compile listener.py` → OK

## Commits (feature + review)

- `3a635cf` T1 types
- `aaf364c` T2 state
- `7dcc68f` T3 speaker
- `a00e5e1` T4 listener.ts
- `cd63252` T5 listener.py
- `7be0a44` T6 daemon
- `c44cd88` T7 tests
- `8b0fccc` review fixes (this pass)

## Non-actionable notes

- Manual smoke test на реальном микрофоне (~200ms acceptance) не выполнен в этой сессии — требует физического окружения.
- Выбор `SPEAKING_THRESHOLD = 0.85` эмпирический; возможно потребует калибровки после полевых тестов.
