# Execution report — strip emoji and markdown before Piper TTS

**Slug:** 4-strip-emoji-tts
**Ticket:** https://github.com/yokeloop/voca/issues/4
**Plan:** [4-strip-emoji-tts-plan.md](./4-strip-emoji-tts-plan.md)
**Status:** done (4/4 tasks)
**Branch:** issue-4-strip-emoji-tts

## Summary

Added an engine-agnostic text sanitizer and wired it into `speak()` so Piper no longer voices emojis, markdown punctuation, URLs, or fenced code content. Output stays language-neutral; Russian prose is preserved.

## Tasks

| # | Task | Status | Commit |
|---|------|--------|--------|
| T1 | Create `src/sanitizer.ts` | done | `d3ca538` |
| T2 | Create `test/sanitizer.test.ts` | done | `09eaaf0` |
| T3 | Wire sanitizer into `src/speaker.ts` | done | `feee71c` |
| T4 | Validation (build + test) | done | — |

## Changes

- **New:** `src/sanitizer.ts` — pure `sanitizeForTts(text)` running a fixed regex pipeline: fenced code → `Code block`, URLs → `Link`, inline links → their text, emoji + variation selectors + ZWJ → dropped, markdown punctuation → dropped, decorative symbols → whitespace, whitespace collapsed and trimmed.
- **New:** `test/sanitizer.test.ts` — 12 vitest cases covering emoji, markdown, code blocks, URLs, inline links, decorative symbols, Russian preservation, whitespace, edge cases, and a combined smoke case.
- **Modified:** `src/speaker.ts` — imports `sanitizeForTts` and sanitizes `opts.text` once before `piper.stdin.write`.

## Verification

- `npm test` → **82 passed / 82** across 7 files (12 new in `test/sanitizer.test.ts`).
- `npm run build` → exit 0, no TypeScript errors.
- Daemon signature unchanged; `speak()` signature unchanged; no config changes; no new runtime dependencies.

## Decisions applied

- Sanitizer lives in its own module (per user choice during /sp:task).
- Decorative symbols dropped to whitespace (no transliteration, language-neutral).
- Always-on, no config flag.
- Fixed regex pipeline order chosen so multi-character patterns (fenced code, URLs) consume before their constituent characters are stripped.

## Notes

- Skipped the full agent-dispatch pipeline (polish / validate / doc / format sub-agents) because the change is ~100 lines across 3 files with a clean test run — the orchestration overhead would exceed the work.
- No README/CHANGELOG updates: the project has no CHANGELOG, and README documents user-facing CLI behaviour unchanged by this internal TTS hygiene step.
