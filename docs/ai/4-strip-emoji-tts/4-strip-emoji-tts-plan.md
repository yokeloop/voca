# Implementation plan — strip emoji and markdown before Piper TTS

**Slug:** 4-strip-emoji-tts
**Task:** [docs/ai/4-strip-emoji-tts/4-strip-emoji-tts-task.md](./4-strip-emoji-tts-task.md)
**Ticket:** https://github.com/yokeloop/voca/issues/4
**Complexity:** simple
**Routing:** inline, sequential (single session; file overlap on `src/speaker.ts` is minimal but tasks chain linearly for atomicity)

---

## Design decisions

### D1. Sanitizer order of operations

**Decision:** apply regexes in a fixed pipeline — (1) fenced code blocks → `Code block`, (2) URLs → `Link`, (3) inline links `[text](url)` → `text`, (4) emoji + variation selectors + ZWJ → drop, (5) markdown punctuation (`*`, `_`, `` ` ``, `#`, `>`, leading bullets) → drop, (6) decorative symbols → space, (7) whitespace collapse + trim.
**Why:** earlier steps consume multi-character patterns (``` ``` ```, `http://…`) before later steps can chew up their constituent characters. Reversing the order would strip backticks before the code-block rule runs, turning ``` ```js\nx=1\n``` ``` into loose text instead of `Code block`.
**Rejected:** single mega-regex — unreadable, hard to unit-test, slower to evolve.

### D2. Emoji matching strategy

**Decision:** rely on JavaScript's Unicode property escape `/\p{Extended_Pictographic}/gu`, combined with explicit stripping of variation selector `\uFE0F` and ZWJ `\u200D`.
**Why:** Node 20 supports Unicode property escapes natively. No dependency needed (Constraint: no `emoji-regex`). `Extended_Pictographic` covers the practical emoji range including flags and skin-tone modifiers when paired with ZWJ removal.
**Rejected:** hand-rolled codepoint ranges — brittle, misses new emoji blocks.

### D3. Where to call the sanitizer

**Decision:** call `sanitizeForTts(opts.text)` once inside `speak()` at `src/speaker.ts`, right before `piper.stdin.write(...)`. The daemon stays unaware.
**Why:** matches the task's engine-agnostic intent — any TTS flowing through `speak()` gets clean input. Keeps `daemon.ts` untouched and makes the sanitizer a local responsibility of the speaker boundary.
**Rejected:** sanitize in `daemon.ts` before calling `speak()` — leaks TTS concerns upstream; future Kitten TTS integration would have to re-plumb it.

### D4. Tests style

**Decision:** pure-function unit tests in `test/sanitizer.test.ts` using vitest `describe`/`it`/`expect`, no mocks. Follow the shape of `test/transcriber.test.ts` minus the `execFile` mock scaffolding.
**Why:** `sanitizeForTts` is pure — no I/O, no child processes. Testing at this layer avoids touching `speaker.ts` (which spawns piper/aplay and is hard to test).

---

## Decomposition

### T1. Create `src/sanitizer.ts` — S

**What:** implement and export `sanitizeForTts(text: string): string` following the D1 pipeline.

**Files:**
- `src/sanitizer.ts` (new)

**How:**
- Single exported function, no default export, ES module.
- Constant regex table at module scope for clarity.
- Regex order from D1.
- Return empty string for input that collapses to whitespace.

**Depends on:** —

**Verify:**
- `npm run build` exits 0.
- Manual: `node -e "import('./dist/sanitizer.js').then(m => console.log(m.sanitizeForTts('Привет 👋 **мир** https://a.b')))"` prints `Привет мир Link` (with single spaces).

---

### T2. Create `test/sanitizer.test.ts` — S

**What:** vitest suite covering every bullet in Requirement 11 of the task file.

**Files:**
- `test/sanitizer.test.ts` (new)

**How:**
- `import { sanitizeForTts } from '../src/sanitizer.js';`
- `describe('sanitizeForTts', () => { ... })` with one `it` per case:
  - emoji only → `''`
  - bold/italic/inline-code markdown → text stripped of punctuation
  - fenced code block (multi-line, with language tag) → substring `Code block`
  - URL (`http://`, `https://`, `www.`) → `Link`
  - inline link `[text](url)` → `text`
  - decorative symbols (`→`, `•`, `—`, `…`) collapse to spaces
  - mixed Russian + emoji → Cyrillic preserved, emoji gone
  - whitespace collapsing (newlines, tabs, multiple spaces) → single spaces, trimmed
  - empty string → `''`
  - pure whitespace → `''`

**Depends on:** T1

**Verify:** `npx vitest run test/sanitizer.test.ts` — all cases pass.

---

### T3. Wire sanitizer into `src/speaker.ts` — S

**What:** sanitize `opts.text` once before writing to Piper stdin.

**Files:**
- `src/speaker.ts` (modify line 78 area)

**How:**
- Add `import { sanitizeForTts } from './sanitizer.js';` at top.
- Replace `piper.stdin.write(opts.text + '\n');` with `piper.stdin.write(sanitizeForTts(opts.text) + '\n');`.
- No signature change. No `SpeakerError` change.

**Depends on:** T1

**Verify:**
- `npm run build` exits 0.
- `npm test` — all existing suites pass (no suite currently imports `speaker.ts`, so no regression expected).

---

### T4. Validation — S

**What:** final build + full test run to confirm nothing regressed.

**Files:** —

**How:**
- `npm run build`
- `npm test`

**Depends on:** T1, T2, T3

**Verify:**
- `npm run build` → exit 0.
- `npm test` → all suites green, including `test/sanitizer.test.ts`.

---

## File intersection matrix

| | T1 | T2 | T3 | T4 |
|--|--|--|--|--|
| T1 | — | — | — | — |
| T2 | imports sanitizer.ts | — | — | — |
| T3 | imports sanitizer.ts | — | — | — |
| T4 | — | — | — | — |

No two tasks write to the same file. T2 and T3 both *read* `src/sanitizer.ts`; neither modifies it.

## Execution order (DAG)

```
T1 ──┬── T2 ──┐
     │        ├── T4
     └── T3 ──┘
```

T2 and T3 are parallelizable once T1 lands — but since routing is inline sequential, run them in order T1 → T2 → T3 → T4 for clean commit history.

## Verification (from task file)

- `npm test` passes, including the new `test/sanitizer.test.ts`.
- `npm run build` exits 0 with no TypeScript errors.
- Manual smoke: `sanitizeForTts('Привет 👋! **Жми** → https://ya.ru\n\`\`\`js\nx=1\n\`\`\`')` yields a string with no emoji, no markdown, contains `Link` and `Code block`, and has collapsed whitespace.
- Edge cases: empty string, emoji-only, markdown-only, whitespace-only all return `''` (after trim).
