# Review report — 4-strip-emoji-tts

**Slug:** 4-strip-emoji-tts
**Ticket:** https://github.com/yokeloop/voca/issues/4
**Scope:** Fix Critical + Important + both Minor (user opted for all)

## Summary

Implementation matches the task spec. All 82 original tests passed before review. Two minor correctness gaps surfaced and were fixed. Final state: 85/85 tests green, build clean.

## Issues found

| # | Severity | File:Line | Category | Description | Status |
|---|----------|-----------|----------|-------------|--------|
| 1 | Minor | `src/sanitizer.ts:2` | correctness | URL regex `\S+` absorbed trailing sentence punctuation — `"https://ya.ru, друг"` dropped the comma. | Fixed |
| 2 | Minor | `src/sanitizer.ts:3` | correctness | Image markdown `![alt](url)` left a stray `!` because the inline-link regex only matched `[alt](url)`. Piper would voice the exclamation. | Fixed |

## Fixes applied

- **Issue 1** — Kept greedy `\S+` URL match (preserves query strings like `?x=1&y=2`), then stripped trailing `[,.!?;:)\]}"']+` via a replacement callback that re-emits the punctuation outside the `Link` token. Commit `f43f8de`.
- **Issue 2** — Extended `INLINE_LINK` to `/!?\[([^\]]+)\]\(([^)]+)\)/g` so a leading `!` is consumed alongside the image markdown. Commit `f43f8de`.

## Tests added

- `preserves sentence punctuation after URLs` — `,`, `.`, `!` after URL.
- `preserves URL query strings while trimming trailing punctuation` — `http://a.b/c?x=1,` → `Link,`.
- `strips image markdown including the leading exclamation` — `![alt](url)` → `alt`.

## Verification

- `npm test` → **85 passed / 85**, 7 files.
- `npm run build` → exit 0.

## Skipped / deferred

None.
