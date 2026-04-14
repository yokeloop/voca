---
name: 14-storage-relocation-plan
description: Implementation plan for relocating VOCA storage to a user-chosen root via paths.ts abstraction
type: plan
---

# Plan: Relocate VOCA storage to a dedicated root

**Task:** [14-storage-relocation-task.md](./14-storage-relocation-task.md)
**Ticket:** https://github.com/yokeloop/voca/issues/14
**Complexity:** medium
**Type:** general

## Design Decisions

### D1 — Storage root discovery order: env var → pointer file → throw
- **Decision:** `storageRoot()` reads `process.env.VOCA_HOME` first; on empty, reads `~/.config/voca/root`; on both missing, throws `Error("VOCA storage root not configured — run 'voca bootstrap'")`.
- **Why:** Matches the issue's fallback wording and the answers from task synthesis. Env var wins so CI and ad-hoc overrides skip file edits.
- **Rejected:** Pointer-only (loses CI override ergonomics). Env-only (forces every user to edit shell rc).

### D2 — Every `paths.ts` export is a function, not a module-level constant
- **Decision:** `configPath()`, `sessionPath()`, `soundsDir()`, `modelsDir()`, `venvDir()`, `binDir()`, `pidFile()`, `stateFile()`, `piperBin()`, `piperModelPath(name)` all resolve on call. No cached values.
- **Why:** The task file requires it — tests that set `VOCA_HOME` after import must see the new value. Call-time resolution also lets `voca bootstrap` run without a configured root; import-time resolution would throw.
- **Rejected:** Top-level constants (break `voca bootstrap`). Memoising the first call (defeats test isolation).

### D3 — `readConfig()` resolves relative piperBin/piperModel to absolute on load; throws if root unset
- **Decision:** After reading `config.json`, `readConfig` rewrites relative `piperBin` and `piperModel` strings as absolute paths under the active storage root. `resolvePiperModel` passes absolute paths through unchanged (back-compat). A missing root triggers the `storageRoot()` error.
- **Why:** The user picked this answer during plan questions — it matches the "run bootstrap first" contract. Callers (speaker.ts, daemon.ts) consume absolute strings and skip re-resolution.
- **Rejected:** Resolving lazily at each call site (scatters the concern, risks divergence).

### D4 — Existing test defaults assertion switches to field-by-field checks
- **Decision:** `test/config.test.ts:20-23` (`readConfig returns defaults when file does not exist`) drops `.toEqual(defaultConfig)` and asserts `profile`, `wakeWord`, `stopWord`, `language` individually. A `beforeEach` sets `process.env.VOCA_HOME = tmpDir` so `readConfig()` resolves.
- **Why:** D3 forces a root at `readConfig` time; `toEqual(defaultConfig)` fails because resolved `piperBin` differs from the relative `'bin/piper'`.
- **Rejected:** Keeping `toEqual` by returning unresolved defaults (violates D3).

### D5 — Bootstrap Step 0 creates the root directory immediately
- **Decision:** After writing the pointer file, `runBootstrap()` calls `fs.mkdir(storageRoot(), { recursive: true })` before any other step runs.
- **Why:** Later steps assume the root exists (they `mkdir` subdirs and `writeConfig` under it). Centralising the `mkdir` here removes the question "did any step forget to mkdir the parent?".
- **Rejected:** Deferring to subdir writes (spreads the `mkdir` responsibility, risks missed cases).

### D6 — `cli.ts` replaces constant imports with function calls at action time
- **Decision:** `cli.ts:10` currently imports `PID_FILE`, `STATE_FILE`, `ASSISTANT_DIR` as constants from `daemon.ts`. After the refactor, `cli.ts` imports `pidFile`, `stateFile`, `storageRoot` as functions and calls them inside each command's `.action()` handler.
- **Why:** Module-level constants force `storageRoot()` to resolve at `voca bootstrap` start — but bootstrap is the command that creates the pointer. Functions inside handlers resolve only when the command runs, and `bootstrap` creates the pointer before touching any path helper.
- **Rejected:** Keeping constants with lazy getters on the daemon module (adds indirection without clarity).

### D7 — `readPointerFile()` throws on non-absolute content, returns null on missing file
- **Decision:** Missing `~/.config/voca/root` → `null`. Present but content is relative or empty → throws `Error("corrupt pointer file at <path>: expected absolute path")`.
- **Why:** Missing is a normal state before bootstrap. Corrupt content is a data-integrity problem the user must see.

## Decomposition

### T1 — Create `src/paths.ts`
- **What:** Implement `storageRoot()`, `readPointerFile()`, `writePointerFile(path)`, plus the 10 subpath helpers from D2.
- **Files:** `src/paths.ts` (new).
- **Depends on:** —
- **Scope:** M.
- **Verify:** File compiles. No top-level code calls `storageRoot()`. Exports match the D2 list.

### T2 — Add `test/paths.test.ts`
- **What:** Five test cases — `VOCA_HOME` beats pointer, pointer fallback, both-missing throws, `writePointerFile` round-trip creates `~/.config/voca/` recursively, `writePointerFile` rejects relative paths. `beforeEach` stashes and clears `VOCA_HOME`; `afterEach` restores it and removes the temp pointer dir.
- **Files:** `test/paths.test.ts` (new).
- **Depends on:** T1.
- **Scope:** M.
- **Verify:** `npm test` runs the new file; all five cases pass.

### T3 — Refactor `src/session.ts` to paths.ts
- **What:** Replace the `SESSION_PATH` constant (line 6) with `import { sessionPath } from './paths.js'`. Change the default arg in `readSession`, `writeSession`, `incrementMessageCount`, `resetSessionForProfile` from `SESSION_PATH` to `sessionPath()`, computed at call time. Export a `sessionPath` alias if other modules need it; otherwise drop the legacy constant export.
- **Files:** `src/session.ts`.
- **Depends on:** T1.
- **Scope:** S.
- **Verify:** `test/session.test.ts` still passes unchanged — it always passes explicit paths.

### T4 — Refactor `src/sounds.ts` to paths.ts
- **What:** Delete the `SOUNDS_DIR` constant at lines 5-10. Change `soundFile(type)` to call `soundsDir()` from paths.ts at call time.
- **Files:** `src/sounds.ts`.
- **Depends on:** T1.
- **Scope:** S.
- **Verify:** `tsc` passes. The `playSound('wake', ...)` call site in `daemon.ts` still resolves.

### T5 — Refactor `src/voice.ts` to paths.ts
- **What:** Delete the `PIPER_DIR` constant at line 9. Replace every in-file use (lines 42, 57-58, 65, 73, 113) with `binDir()` from paths.ts. Keep the public API unchanged: `voicePath`, `deriveVoicePaths`, `listInstalled`, `installVoice`, `useVoice`.
- **Files:** `src/voice.ts`.
- **Depends on:** T1.
- **Scope:** S.
- **Verify:** `tsc` passes. `cli.ts` voice commands still work — confirm via manual smoke test after later integration tasks land.

### T6 — Refactor `src/config.ts` to paths.ts + relative defaults
- **What:**
  1. Delete the `CONFIG_PATH` constant at line 6; re-export `configPath` as a function from paths.ts. Update imports site-by-site in T8/T10 to call the function instead of the old constant.
  2. Change `defaultConfig.piperModel` to `'bin/ru_RU-irina-medium.onnx'`.
  3. Change `defaultConfig.piperBin` to `'bin/piper'`.
  4. In `readConfig()`, after the merge, resolve `piperBin`: if relative, prefix with `binDir()`; if absolute, keep as-is.
  5. Rewrite `resolvePiperModel` to use `binDir()` as the base and pass absolute paths through.
  6. Default args of `readConfig`, `writeConfig`, `ensureConfigDir` call `configPath()` at call time, not import time.
- **Files:** `src/config.ts`.
- **Depends on:** T1.
- **Scope:** M.
- **Verify:** Unit tests round-trip fields correctly with `VOCA_HOME` set; resolved `piperBin` is absolute.

### T7 — Refactor `src/daemon.ts` to paths.ts
- **What:**
  1. Delete the `ASSISTANT_DIR`, `PID_FILE`, `STATE_FILE` constants at lines 21-23.
  2. Re-export `pidFile` and `stateFile` as functions from `./paths.js`. T10 then updates `cli.ts` to import from paths instead of daemon.
  3. Line 41: `const venvPython = path.join(venvDir(), 'bin', 'python3');`.
  4. Line 53: `modelDir: modelsDir()`.
  5. In `cleanup()` and `writeStateFile()`, replace `PID_FILE` / `STATE_FILE` with `pidFile()` / `stateFile()` calls.
- **Files:** `src/daemon.ts`.
- **Depends on:** T1.
- **Scope:** S.
- **Verify:** `tsc` passes. T10 updates `cli.ts` to import from paths.ts.

### T8 — Update `test/config.test.ts`
- **What:**
  1. Add `beforeEach` that sets `process.env.VOCA_HOME = tmpDir` and `afterEach` that deletes it. Snapshot and restore the prior value.
  2. Rewrite the `readConfig returns defaults when file does not exist` assertion (lines 20-23) to check individual fields — `profile`, `wakeWord`, `stopWord`, `language` — instead of `toEqual(defaultConfig)`.
  3. Change line 58 to `expect(defaultConfig.piperBin).toBe('bin/piper')`.
  4. Add one new test: with `VOCA_HOME=tmpDir`, write a partial config and assert `readConfig()` returns an absolute `piperBin` that starts with `tmpDir` and ends with `bin/piper`.
- **Files:** `test/config.test.ts`.
- **Depends on:** T6, T1.
- **Scope:** S.
- **Verify:** `npm test test/config.test.ts` passes all 10+1 cases.

### T9 — Add bootstrap Step 0 and refactor `src/bootstrap.ts`
- **What:**
  1. Delete the five constants at lines 10-14: `ASSISTANT_DIR`, `VENV_DIR`, `PIPER_DIR`, `MODELS_DIR`, `SOUNDS_DIR`.
  2. Add a helper `promptStorageRoot(rl)` that asks `Enter VOCA storage path [~/.voca] `, trims the input, expands a leading `~/` via `os.homedir()`, reprompts on non-absolute input, and returns an absolute path.
  3. In `runBootstrap()` (line 350), insert Step 0 before `ensureConfigDir()`:
     - Call `promptStorageRoot(rl)`.
     - Call `writePointerFile(root)`.
     - Call `fs.mkdir(root, { recursive: true })`.
  4. Replace every remaining `ASSISTANT_DIR` / `VENV_DIR` / `PIPER_DIR` / `MODELS_DIR` / `SOUNDS_DIR` reference (lines 195, 205-206, 212, 219, 234, 245, 270, 290, 302, 305-308, 324, 338, 347, 380) with the corresponding `binDir()`, `venvDir()`, `modelsDir()`, `soundsDir()`, `storageRoot()` call.
  5. Update the error message at line 124 to drop `~/.openclaw/assistant/config.json` and reference `configPath()` instead.
  6. At lines 306-307, rebuild the venv-model probe path from `venvDir()`.
- **Files:** `src/bootstrap.ts`.
- **Depends on:** T1, T6 (for relative defaults).
- **Scope:** M.
- **Verify:** Manual: `rm -rf ~/.voca ~/.config/voca && node dist/cli.js bootstrap` prompts for root, accepts `~/.voca`, writes `~/.config/voca/root`, creates `~/.voca/config.json`.

### T10 — Update `src/cli.ts` to function-based imports
- **What:** Split the import at line 10 (`VocaDaemon, PID_FILE, STATE_FILE, ASSISTANT_DIR` from `./daemon.js`) into two: `VocaDaemon` from `./daemon.js` and `pidFile, stateFile, storageRoot` from `./paths.js`. Replace every in-file use of `PID_FILE` / `STATE_FILE` / `ASSISTANT_DIR` (lines 143, 150, 178, 201) with the corresponding `pidFile()` / `stateFile()` / `storageRoot()` call.
- **Files:** `src/cli.ts`.
- **Depends on:** T1, T7.
- **Scope:** S.
- **Verify:** `voca status`, `voca start --daemon`, `voca stop` resolve paths via the active root.

### T11 — Update `CLAUDE.md` and `README.md`
- **What:**
  1. CLAUDE.md: replace the directory tree at lines 28-37 with the `<VOCA_HOME>/` variant; update the listener.py bullet at line 75 to reference `modelsDir()`; rewrite the bootstrap bullet (lines 99-101) to describe Step 0, `VOCA_HOME`, and the pointer file; add a new "Migration" subsection under "Non-obvious" listing the two manual options from the issue.
  2. README.md: add one new section, "Storage layout and migration", covering the `~/.voca` default, the `VOCA_HOME` override, and the manual move-files instruction.
- **Files:** `CLAUDE.md`, `README.md`.
- **Depends on:** T9 (so docs describe the implemented flow).
- **Scope:** S.
- **Verify:** `grep "openclaw/assistant" CLAUDE.md` returns only the migration note — the old path appears exactly once, labelled as legacy. README mentions `VOCA_HOME`.

### T12 — Validation
- **What:**
  1. `grep -rn "openclaw/assistant" src/ test/ listener.py` → zero matches.
  2. `npm run build` → passes.
  3. `npm test` → every existing test passes, plus the new `test/paths.test.ts`.
  4. Manual smoke: from a clean state (`rm -rf ~/.voca ~/.config/voca`), `voca status` errors with "run 'voca bootstrap'"; `voca bootstrap` populates the root; `voca status` succeeds; `VOCA_HOME=/tmp/voca-alt voca status` uses the override.
- **Files:** none (validation only).
- **Depends on:** T1-T11.
- **Scope:** S.

## File Intersection Matrix

| Task | paths.ts | config.ts | session.ts | sounds.ts | voice.ts | daemon.ts | bootstrap.ts | cli.ts | test/config | test/paths | CLAUDE.md | README.md |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| T1   | W | — | — | — | — | — | — | — | — | — | — | — |
| T2   | R | — | — | — | — | — | — | — | — | W | — | — |
| T3   | R | — | W | — | — | — | — | — | — | — | — | — |
| T4   | R | — | — | W | — | — | — | — | — | — | — | — |
| T5   | R | — | — | — | W | — | — | — | — | — | — | — |
| T6   | R | W | — | — | — | — | — | — | — | — | — | — |
| T7   | R | — | — | — | — | W | — | — | — | — | — | — |
| T8   | — | R | — | — | — | — | — | — | W | — | — | — |
| T9   | R | R | — | — | — | — | W | — | — | — | — | — |
| T10  | R | — | — | — | — | R | — | W | — | — | — | — |
| T11  | — | — | — | — | — | — | — | — | — | — | W | W |

`W` = writes file, `R` = reads/imports from. No two tasks write the same file — no shared-state conflicts across the parallel group.

## Execution Order (DAG)

```
T1 ─┬─ T2
    ├─ T3   ┐
    ├─ T4   │
    ├─ T5   │   (parallel group A)
    ├─ T6   ┘
    ├─ T7   ────┐
    │          │
    └── T9 ←── T6 ──── T8
         │          (T6 → T8 must come after config refactor)
         └─── T10 ←── T7
                       │
                       └─── T11 ─── T12
```

- **Serial first:** T1 alone (everyone imports from it).
- **Parallel group A (after T1):** T2, T3, T4, T5, T6, T7 — no file overlap.
- **Serial after group A:** T8 (needs T6), T9 (needs T6 for relative defaults), T10 (needs T7).
- **Serial tail:** T11 (describes the finished flow), T12 (validates the whole thing).

## Routing

- **MODE:** sub-agents (per-task delegation).
- **PARALLEL:** true for group A (T2–T7 after T1).
- **PARALLEL_GROUPS:** `{T2, T3, T4, T5, T6, T7}` after T1 lands.
- **REASONING:** Twelve well-scoped tasks with a strict dependency spine fan out once after the foundation — a classic shape for sub-agent dispatch with one parallel wave.

## Verification (from task file)

- `grep -rn "openclaw/assistant" src/ test/ listener.py` → zero matches aside from a labelled migration note.
- `npm run build` passes.
- `npm test` — every existing test passes plus `test/paths.test.ts`.
- Manual: clean state → `voca status` errors → `voca bootstrap` accepts `~/.voca` → `~/.voca/` populated → `voca status` succeeds.
- Manual: `VOCA_HOME=/tmp/voca-alt voca status` uses the override.
- Manual: move `~/.voca` to `~/.voca-new`, update pointer file, daemon starts without re-bootstrap (proves relative piper paths).
- Edge: bootstrap rejects a relative path at Step 0 and reprompts.
