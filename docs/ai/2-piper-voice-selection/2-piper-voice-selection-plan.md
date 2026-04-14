# Allow selecting alternative Piper TTS voices — implementation plan

**Task:** docs/ai/2-piper-voice-selection/2-piper-voice-selection-task.md
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** mixed

## Design decisions

### DD-1: Shared utility module location

**Decision:** Create `src/util.ts` exporting `run()` and `fileExists()`; move them out of `src/bootstrap.ts` and import from both `bootstrap.ts` and the new `src/voice.ts`.
**Rationale:** Task file mandates that `voice.ts` stay decoupled from the daemon runtime and reuse `run()` (`bootstrap.ts:22-31`) and `fileExists()` (`bootstrap.ts:51-58`). Extracting avoids a dependency from `voice.ts` back into `bootstrap.ts`.
**Alternative:** Re-export from `voice.ts` — inverts the natural dependency direction (bootstrap would import voice logic just for plumbing).

### DD-2: Catalog fetch and caching

**Decision:** Use Node 20 global `fetch()` once per CLI invocation; cache the parsed JSON in a module-level `let catalogCache: CatalogMap | null` inside `voice.ts`. No persistent cache on disk.
**Rationale:** Task file requires `fetch` (no new deps) and each CLI command is a single-shot process, so in-process caching is enough to avoid double fetches when `useVoice()` calls both `fetchCatalog()` and `installVoice()`.
**Alternative:** Cache to `~/.openclaw/assistant/voices.json` — adds staleness problems and extra IO for a rare command.

### DD-3: HF URL derivation

**Decision:** Derive URL path segments from voice name alone (`<lang_code>-<name>-<quality>` → `<lang>/<lang_code>/<name>/<quality>/<name>.onnx[.json]`). Treat the catalog's `voices.json` as membership check only, not path source.
**Rationale:** Task file prescribes this derivation verbatim. Catalog `files` entries add complexity (per-file paths) for no gain — HF repo layout is stable under the `v1.0.0` ref.
**Alternative:** Use catalog `files` key — correct even if repo layout changes, but larger parser and no current need.

### DD-4: Sample rate source in speaker

**Decision:** In `speak()`, read the sibling `<piperModel>.json` via `fs.readFile` at the start of each call, parse `audio.sample_rate`, pass to `aplay -r`. Throw `SpeakerError` if missing or malformed — no silent fallback.
**Rationale:** Task file requires per-call derivation with no `config.json` field for rate. Reading once per utterance is cheap (JSON < 1 KB) and keeps voice switches instant without daemon plumbing.
**Alternative:** Cache rate in `config.piperSampleRate` at `voice use` time — adds a new config field and drifts if the user edits the .onnx.json manually.

### DD-5: Catalog fetch error handling

**Decision:** On `fetch` failure (network, non-200, JSON parse error), throw a typed error from `fetchCatalog()`; CLI handlers in `cli.ts` catch it, print a short message, and `process.exitCode = 1`. Locally installed voices remain usable because `voca voice list` does not call `fetchCatalog()`.
**Rationale:** Matches the offline-safe requirement in Verification and keeps error surface local to commands that need network.
**Alternative:** Fall back to an empty catalog — silently hides network issues and breaks `install`/`use`.

### DD-6: `voice use` when voice already active

**Decision:** No-op detection — if `<resolved new path>` equals current `config.piperModel`, print `Already using voice: <name>` and exit 0 without rewriting the config.
**Rationale:** Cheap UX, avoids a pointless restart hint. Matches the "quiet on no-op" vibe of the rest of the CLI.
**Alternative:** Always write config and print restart hint — noisy false positive.

## Tasks

### Task 1: Extract shared utilities into `src/util.ts`

- **Files:** `src/util.ts` (create), `src/bootstrap.ts:22-58` (edit — remove `run`, `runCapture`, `fileExists`, add import)
- **Depends on:** none
- **Scope:** S
- **What:** Create `src/util.ts` exporting `run(cmd, args)`, `runCapture(cmd, args)`, and `fileExists(path)` with identical signatures and behavior as today's versions in `bootstrap.ts`. Update `bootstrap.ts` to import them.
- **How:** Copy the three functions verbatim out of `bootstrap.ts:22-58`. Add `export` to each in `util.ts`. Replace the definitions in `bootstrap.ts` with `import { run, runCapture, fileExists } from './util.js';`. Keep `bootstrap.ts`'s existing call sites unchanged.
- **Context:** `src/bootstrap.ts:22-58` (source of the functions).
- **Verify:** `npm run build` succeeds; `npm test` green (no existing tests touch these utilities directly, but bootstrap imports must still compile).

### Task 2: Create `src/voice.ts` with catalog + listing primitives

- **Files:** `src/voice.ts` (create)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Add `fetchCatalog()`, `deriveVoicePaths(name)`, `listInstalled()`, `listAvailable({ languageFilter?, all? })`. No CLI wiring yet.
- **How:**
  - `CATALOG_URL = 'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json'`.
  - `fetchCatalog(): Promise<Record<string, { language: { code: string }, quality: string }>>` — `fetch(CATALOG_URL)`, throw on non-OK, parse JSON, cache in `let catalogCache`.
  - `deriveVoicePaths(name)` → `{ onnxUrl, jsonUrl, onnxPath, jsonPath }`. Split `name` on `-`: `[lang_code, voiceName, quality]`. `lang = lang_code.split('_')[0].toLowerCase()`. Base URL = `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${lang}/${lang_code}/${voiceName}/${quality}`. Local paths in `~/.openclaw/assistant/bin/`.
  - `listInstalled()` — read `~/.openclaw/assistant/bin/`, return names of `.onnx` files that have a sibling `.onnx.json`.
  - `listAvailable({ languageFilter, all })` — call `fetchCatalog()`, filter keys: if `!all && languageFilter`, keep those starting with `${languageFilter}_`.
- **Context:** `src/types.ts:1-10` (VocaConfig), `src/config.ts:14-17` (bin path pattern), `src/util.ts` (fileExists).
- **Verify:** `npm run build` succeeds; module importable (a throwaway `tsx -e "import('./src/voice.ts')"` or rely on Task 7 tests).

### Task 3: Add `installVoice` and `useVoice` to `src/voice.ts`

- **Files:** `src/voice.ts` (edit — append functions)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement `installVoice(name)` and `useVoice(name)` on top of Task 2 primitives.
- **How:**
  - `installVoice(name)`:
    1. Call `fetchCatalog()`; throw `Error("Unknown voice: <name>. Run: voca voice available")` if key missing.
    2. `deriveVoicePaths(name)`.
    3. If both `.onnx` and `.onnx.json` already exist → log `already installed` and return.
    4. `fs.mkdir(PIPER_DIR, { recursive: true })`.
    5. `await run('curl', ['--fail', '-L', '-o', onnxPath, onnxUrl])` then same for `jsonPath`. On failure, attempt `fs.unlink` of partial files, rethrow.
  - `useVoice(name)`:
    1. Call `fetchCatalog()`; reject unknown names same as `installVoice`.
    2. `deriveVoicePaths(name)`. If `.onnx` missing → call `installVoice(name)`.
    3. `readConfig()`; if `config.piperModel === onnxPath` → print `Already using voice: <name>` and return.
    4. Set `config.piperModel = onnxPath`; `writeConfig(config)`.
    5. Print `Switched to voice: <name>. Restart the daemon (voca stop && voca start) to apply.`.
- **Context:** `src/voice.ts` (from Task 2), `src/config.ts:23-60` (readConfig/writeConfig).
- **Verify:** `npm run build` succeeds; `installVoice` / `useVoice` callable signatures match Task 4 needs.

### Task 4: Wire `voca voice` CLI subcommands

- **Files:** `src/cli.ts:8, 72` (edit — add import, register subcommand group after `profile`)
- **Depends on:** Task 3
- **Scope:** S
- **What:** Add `voice list`, `voice available [--all]`, `voice install <name>`, `voice use <name>` subcommands mirroring the `profile` pattern.
- **How:** Mirror `cli.ts:44-72`. Parent: `const voice = program.command('voice').description('Piper voice management');`. Four children:
  - `voice.command('list')` → call `listInstalled()`, read `config.piperModel`, print names with `*` prefix when path matches.
  - `voice.command('available').option('--all', 'Show all languages')` → `readConfig()` for `language`, call `listAvailable({ languageFilter: opts.all ? undefined : config.language, all: opts.all })`, print rows `<name>  <lang_code>  <quality>`.
  - `voice.command('install <name>')` → `await installVoice(name)`; catch error, print, `process.exitCode = 1`.
  - `voice.command('use <name>')` → `await useVoice(name)`; catch error same way.
- **Context:** `src/cli.ts:44-72` (profile subcommand analog), `src/voice.ts` exports (from Task 3), `src/config.ts:23-35` (readConfig).
- **Verify:** `npm run build` succeeds; `node dist/cli.js voice --help` lists all four subcommands.

### Task 5: Refactor `bootstrap.ts` to delegate voice install

- **Files:** `src/bootstrap.ts:17-18, 189-234` (edit)
- **Depends on:** Task 3
- **Scope:** S
- **What:** Remove `PIPER_VOICE_BASE` constant and the inline `.onnx`/`.onnx.json` download block; replace with a call to `installVoice('ru_RU-irina-medium')`.
- **How:** Delete lines 17-18. In `installPiper()`, after the piper binary install block, replace the voice-download block (`bootstrap.ts:214-233`) with:
  ```ts
  if (await fileExists(path.join(PIPER_DIR, 'ru_RU-irina-medium.onnx'))) {
    console.log('Piper voice model already downloaded. Skipping.');
  } else if (await confirm(rl, 'Download ru_RU-irina-medium voice model?')) {
    await installVoice('ru_RU-irina-medium');
  } else {
    console.log('Skipped.');
  }
  ```
  Add `import { installVoice } from './voice.js';`.
- **Context:** `src/bootstrap.ts:189-234` (current installPiper), `src/voice.ts` (installVoice from Task 3).
- **Verify:** `npm run build` succeeds; running `voca bootstrap` on a fresh fixture downloads the default voice.

### Task 6: Dynamic sample rate in `src/speaker.ts`

- **Files:** `src/speaker.ts:14-35` (edit)
- **Depends on:** none (does not consume Tasks 1-5; can run any time)
- **Scope:** S
- **What:** Read `<piperModel>.json` inside `speak()` and pass its `audio.sample_rate` to `aplay -r`; throw `SpeakerError` on missing/malformed JSON.
- **How:**
  - Make `speak()` `async`. At the top:
    ```ts
    const jsonPath = `${opts.piperModel}.json`;
    let sampleRate: number;
    try {
      const raw = await fs.readFile(jsonPath, 'utf-8');
      sampleRate = JSON.parse(raw)?.audio?.sample_rate;
      if (typeof sampleRate !== 'number') throw new Error('missing audio.sample_rate');
    } catch (err) {
      throw new SpeakerError(`voice metadata unreadable for ${opts.piperModel}: ${(err as Error).message}`);
    }
    ```
  - Wrap the existing `new Promise<void>(...)` body and return it after resolving `sampleRate`.
  - Replace the hardcoded `'-r', '22050'` at `speaker.ts:30` with `'-r', String(sampleRate)`.
  - Import `fs` from `node:fs/promises`.
- **Context:** `src/speaker.ts:1-82` (full file), `src/daemon.ts` call site (unchanged — `speak()` is already awaited).
- **Verify:** `npm run build` succeeds; manual smoke: switch to `en_US-lessac-low` (16000 Hz), restart daemon, utterance plays at correct pitch.

### Task 7: Unit tests for voice module

- **Files:** `test/voice.test.ts` (create)
- **Depends on:** Task 3
- **Scope:** M
- **What:** Cover `deriveVoicePaths`, `listAvailable` language filter, and catalog error handling (mock `fetch`).
- **How:**
  - Use the existing test framework from `test/config.test.ts` (read it to confirm runner — likely `node --test` or similar).
  - `deriveVoicePaths('ru_RU-irina-medium')` → asserts `onnxUrl` ends with `ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx`.
  - `deriveVoicePaths('en_US-lessac-high')` → asserts `lang='en'`, `lang_code='en_US'`.
  - Mock `global.fetch` to return a stub catalog `{ 'ru_RU-irina-medium': {...}, 'en_US-lessac-high': {...}, 'de_DE-thorsten-medium': {...} }`. Assert `listAvailable({ languageFilter: 'ru' })` returns only `ru_*` keys; `{ all: true }` returns all.
  - `fetchCatalog` rejecting on non-OK response.
  - Reset `catalogCache` between tests (export a `__resetCatalogCacheForTests()` helper from `voice.ts`).
- **Context:** `test/config.test.ts` (runner/style), `src/voice.ts` (exports under test).
- **Verify:** `npm test` green, including the new file.

### Task 8: Validation

- **Files:** —
- **Depends on:** Tasks 1-7
- **Scope:** S
- **What:** Run the full validation sweep matching the task's Verification section.
- **How:** Execute `npm run build && npm test`. Smoke-test manually: `node dist/cli.js voice list`, `... voice available`, `... voice available --all`, `... voice install does-not-exist` (expect non-zero exit), `... voice use ru_RU-irina-medium` (no-op path).
- **Context:** —
- **Verify:** `npm run build && npm test` green; all smoke commands behave per task Verification.

## File intersection matrix

| File | T1 | T2 | T3 | T4 | T5 | T6 | T7 |
|------|----|----|----|----|----|----|----|
| `src/util.ts` | create | import (read) | import (read) | | import (indirect) | | |
| `src/voice.ts` | | create | edit | import | import | | test |
| `src/bootstrap.ts` | edit | | | | edit | | |
| `src/cli.ts` | | | | edit | | | |
| `src/speaker.ts` | | | | | | edit | |
| `test/voice.test.ts` | | | | | | | create |

Intersections: `src/voice.ts` (T2→T3→{T4,T5,T7}), `src/bootstrap.ts` (T1→T5). All other pairs disjoint.

## Execution

- **Mode:** sub-agents
- **Parallel:** mixed
- **Reasoning:** 8 tasks, medium complexity, no cross-layer; core sequence goes through shared `voice.ts`, but Tasks 4/5/6/7 fan out in parallel once Task 3 lands (routing table row 5).
- **Order:**
  Group 1 (sequential): Task 1 → Task 2 → Task 3
  ─── barrier ───
  Group 2 (parallel): Task 4, Task 5, Task 6, Task 7
  ─── barrier ───
  Group 3 (sequential): Task 8

## Verification

- `npm run build` — compiles with no TypeScript errors.
- `npm test` — existing suite stays green; new `test/voice.test.ts` passes.
- `voca voice list` on a fresh install (only `ru_RU-irina-medium` present) → prints `* ru_RU-irina-medium`.
- `voca voice available` with `config.language = 'ru'` → prints only `ru_*` voices; `voca voice available --all` prints the full catalog (>50 entries).
- `voca voice install en_US-lessac-medium` → creates `~/.openclaw/assistant/bin/en_US-lessac-medium.onnx` and `.onnx.json`; rerunning prints "already installed" and exits 0.
- `voca voice install does-not-exist` → non-zero exit; the error names the voice and suggests `voca voice available`.
- `voca voice use en_US-lessac-medium` when not installed → downloads, then updates `config.json`; `config.piperModel` ends with `en_US-lessac-medium.onnx`.
- Daemon restart after switching to `en_US-lessac-low` (16000 Hz voice) → TTS plays at correct pitch (not chipmunked); `aplay` runs with `-r 16000`.
- Deleting `<voice>.onnx.json` and attempting to speak → `SpeakerError` names the voice; the daemon does not fall back to 22050.
- Offline run of `voca voice available` → fails with a clear network error and non-zero exit; installed voices remain usable.

## Materials

- [GitHub issue #2](https://github.com/yokeloop/voca/issues/2)
- `src/cli.ts:44-72` — profile subcommand analog
- `src/config.ts:8-17, 36-40` — config defaults + resolvePiperModel
- `src/speaker.ts:28-35` — hardcoded `-r 22050` to make dynamic
- `src/bootstrap.ts:17-18, 189-234` — PIPER_VOICE_BASE + installPiper to refactor
- `src/types.ts:1-10` — VocaConfig
- `test/config.test.ts` — config test pattern
- Piper catalog: `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json`
