# Strip emoji and markdown before Piper TTS

**Slug:** 4-strip-emoji-tts
**Ticket:** https://github.com/yokeloop/voca/issues/4
**Complexity:** simple
**Type:** general

## Task

Add an engine-agnostic text sanitizer that removes emojis, markdown, and decorative symbols before the agent response reaches Piper TTS.

## Context

### Area architecture

Daemon calls `speak({ text: response.text, ... })` in `src/daemon.ts:225`. `speak()` in `src/speaker.ts:14` writes `opts.text + '\n'` directly into `piper` stdin at `src/speaker.ts:78`. No pre-processing exists anywhere between the agent response and Piper. The project uses Russian output (`ru_RU-irina-medium` Piper model) — the sanitizer must be language-neutral.

### Files to change

- `src/speaker.ts:78` — entry point for TTS input; must sanitize `opts.text` before writing to `piper.stdin`.
- `src/sanitizer.ts` — **new file** — `sanitizeForTts(text: string): string`.
- `test/sanitizer.test.ts` — **new file** — unit tests via vitest.

### Patterns to repeat

- Module shape: see `src/transcriber.ts`, `src/agent.ts` — single exported function, named error class where relevant, no default exports, ES modules with `.js` extension in imports.
- Test shape: see `test/transcriber.test.ts`, `test/daemon.test.ts` — vitest `describe`/`it`/`expect`, no global setup needed for a pure function.
- TypeScript config: strict mode, Node 20+, ES modules (`"type": "module"` in `package.json`).

### Tests

No existing test touches `speaker.ts` (it spawns child processes). Add a standalone `test/sanitizer.test.ts` since `sanitizeForTts` is a pure function — no mocking needed.

## Requirements

1. Export `sanitizeForTts(text: string): string` from `src/sanitizer.ts`. Pure function, no side effects, no I/O.
2. Strip Unicode emoji (pictographs, symbols, flags, skin-tone modifiers, ZWJ sequences). Use the `\p{Extended_Pictographic}` Unicode property with the `u` flag; also strip variation selectors (`\uFE0F`) and ZWJ (`\u200D`).
3. Remove markdown formatting: `*`, `_`, `` ` ``, `#` (headings), `>` (blockquote), leading list bullets (`-`, `*`, `+`, `1.` at line start), and inline links `[text](url)` → keep `text`.
4. Replace fenced code blocks (`` ``` ``…`` ``` ``, any language tag, possibly multi-line) with the literal word `Code block`.
5. Replace standalone URLs (`http://…`, `https://…`, `www.…`) with the literal word `Link`.
6. Replace decorative symbols `→`, `•`, `—`, `–`, `…`, `«`, `»`, `"`, `"`, `'`, `'` with a single space (drop, do not transliterate — language-neutral per user decision).
7. Collapse runs of whitespace (including newlines) into single spaces; trim leading/trailing whitespace.
8. Preserve Cyrillic, Latin letters, digits, and sentence punctuation (`.`, `,`, `!`, `?`, `:`, `;`, `(`, `)`).
9. `speak()` in `src/speaker.ts` calls `sanitizeForTts(opts.text)` once and pipes the result to Piper. The unsanitized text must never reach `piper.stdin`.
10. Sanitization is always on — no config flag.
11. Unit tests in `test/sanitizer.test.ts` cover: emoji input, markdown bold/italic/code-inline, fenced code block (→ "Code block"), URL (→ "Link"), decorative symbols, mixed Russian + emoji, whitespace collapsing, empty string, pure-whitespace input.
12. `npm test` passes; `npm run build` produces clean `dist/`.

## Constraints

- Do not change `speak()` signature or the piper/aplay pipeline.
- Do not add runtime dependencies. Use stdlib regex only — no `remark`, `strip-markdown`, `emoji-regex`, etc.
- Do not transliterate symbols into Russian or English words (except the two explicit replacements: `Code block`, `Link`).
- Do not add a config toggle; do not touch `src/config.ts`, `~/.openclaw/assistant/config.json` schema, or bootstrap flow.
- Do not modify `daemon.ts` — sanitization lives inside `speaker.ts`, callers stay unaware.
- Keep the module engine-agnostic — no reference to Piper, aplay, or voice model in `sanitizer.ts`.

## Verification

- `npm test` → all suites pass, including new `test/sanitizer.test.ts`.
- `npm run build` → exits 0, no TypeScript errors.
- Manual: `node -e "import('./dist/sanitizer.js').then(m => console.log(JSON.stringify(m.sanitizeForTts('Привет 👋! **Жми** → https://ya.ru\n\`\`\`js\nx=1\n\`\`\`'))))"` → something like `"Привет ! Жми Link Code block"` (exact spacing may vary, but no emoji/markdown/URL remains).
- Edge cases: empty string → `""`; string of only emojis → `""`; string with only markdown punctuation → `""` (after trim).

## Materials

- [Issue #4](https://github.com/yokeloop/voca/issues/4)
- `src/speaker.ts:14` — integration point
- `src/daemon.ts:225` — caller
- `test/transcriber.test.ts` — test style reference
