---
name: 14-storage-relocation
description: Relocate VOCA runtime storage from ~/.openclaw/assistant to a user-chosen root with VOCA_HOME + pointer file discovery
type: task
---

# Move VOCA storage out of ~/.openclaw into its own directory

**Slug:** 14-storage-relocation
**Ticket:** https://github.com/yokeloop/voca/issues/14
**Сложность:** medium
**Тип:** general

## Task

Replace the hardcoded `~/.openclaw/assistant/` root with a per-user storage root chosen at bootstrap. Resolve the root at runtime via the `VOCA_HOME` env var or a pointer file, and centralise all path construction in `src/paths.ts`.

## Context

### Архитектура области

VOCA stores runtime data (`config.json`, `session.json`, `sounds/`, `models/`, `venv/`, `bin/`, `daemon.pid`, `daemon-state.json`) under `~/.openclaw/assistant/`. Six TypeScript modules and one test compute this path independently via `path.join(os.homedir(), '.openclaw/assistant', …)`. `daemon.ts` passes paths to `listener.py` as CLI flags, so `listener.py` needs no change. `speaker.ts` reads `piperBin` from `config.piperBin`, which bootstrap writes as an absolute string.

Today each module calls `os.homedir()` and appends a literal subpath. The target flow routes each module through a `paths.ts` helper that derives the absolute path from a single `storageRoot()` call.

### Файлы для изменения

- `src/config.ts:6` — `CONFIG_PATH` constant
- `src/config.ts:12-13` — `defaultConfig.piperModel`, `defaultConfig.piperBin` literals
- `src/config.ts:37` — `resolvePiperModel` fallback base
- `src/bootstrap.ts:10-14` — `ASSISTANT_DIR`, `VENV_DIR`, `PIPER_DIR`, `MODELS_DIR`, `SOUNDS_DIR`
- `src/bootstrap.ts:124` — user-facing error message mentioning the old path
- `src/bootstrap.ts:306-307` — venv site-packages path used to copy the wake model
- `src/bootstrap.ts:350-384` — `runBootstrap` entry point; the storage-root prompt must run before `readConfig()`
- `src/daemon.ts:21-23, 41, 53` — `ASSISTANT_DIR`, `PID_FILE`, `STATE_FILE`, venv Python path, model dir
- `src/session.ts:6` — `SESSION_PATH`
- `src/sounds.ts:5-10` — `SOUNDS_DIR`
- `src/voice.ts:9, 42, 46-58, 62-77, 101-126` — `PIPER_DIR` and every path derived from it
- `test/config.test.ts:58` — asserts `.openclaw/assistant/bin/piper`
- New: `src/paths.ts` — exports `storageRoot()`, `configPath()`, `sessionPath()`, `soundsDir()`, `modelsDir()`, `venvDir()`, `binDir()`, `pidFile()`, `stateFile()`, plus `setStorageRoot(path)` / `readPointerFile()` / `writePointerFile(path)`
- `CLAUDE.md` — nine references to the old path (directory tree, listener.py notes, bootstrap notes)
- `README.md` — add migration note

### Паттерны для повторения

- ES modules with `.js` import suffixes, Node 20+ stdlib only (`node:fs/promises`, `node:path`, `node:os`)
- Existing `CONFIG_PATH` / `SESSION_PATH` export pattern: a constant plus a function that accepts an explicit path argument (tests rely on this). Preserve it so `test/config.test.ts` and `test/session.test.ts` keep passing unchanged.
- `voice.ts` centralises one subtree (`PIPER_DIR`); extend that style to every subtree in `paths.ts`.
- `util.ts` exposes `run` / `runCapture` / `fileExists` helpers for subprocess and fs probes.

### Тесты

- `test/config.test.ts` — 10 tests; line 58 hardcodes the old path and must assert the new root (`/.voca/bin/piper` after relocation).
- `test/session.test.ts` — 8 tests, all parameterised via explicit path arguments; no path literal to change.
- No bootstrap tests exist. Add `test/paths.test.ts` covering: `VOCA_HOME` precedence over the pointer file, pointer file read/write round-trip, the missing-pointer error message, and pointer writes creating `~/.config/voca/` recursively.

## Requirements

1. Add `src/paths.ts` that resolves the storage root in this order: `process.env.VOCA_HOME` → pointer file at `~/.config/voca/root` (plain text, one absolute path, whitespace trimmed) → throw `Error("VOCA storage root not configured — run 'voca bootstrap'")`.
2. Export typed helpers from `paths.ts` for every subpath in use today: `configPath()`, `sessionPath()`, `soundsDir()`, `modelsDir()`, `venvDir()`, `binDir()`, `pidFile()`, `stateFile()`, `piperBin()`, `piperModelPath(name)`. Each resolves lazily so env or pointer changes take effect without a process restart.
3. Expose `writePointerFile(absolutePath)` that creates `~/.config/voca/` recursively and writes the path followed by a newline. Reject relative paths with a clear error.
4. Store `piperBin` and `piperModel` in `config.json` as paths relative to the storage root (`bin/piper`, `bin/ru_RU-irina-medium.onnx`). Resolve them to absolute paths inside `readConfig()`. Keep `defaultConfig` values relative. `resolvePiperModel` must pass absolute paths through unchanged (back-compat for customised configs) and resolve relative names as `bin/<name>.onnx` under the current root.
5. Add a new first step to `runBootstrap()` — "Step 0: Storage root" — that prompts the user to accept `~/.voca` or type a custom absolute path, then calls `writePointerFile()`. Bootstrap must call `readConfig()` and every subdir constant only after that step.
6. Replace every reference to `~/.openclaw/assistant` in `src/**/*.ts` with a call to the matching `paths.ts` helper. After the change, `grep -r "openclaw/assistant" src/` returns zero hits outside `paths.ts` (which may mention the old path only inside a back-compat doc comment).
7. `daemon.ts` passes `--model-dir` to `listener.py`; update that call site to pass `modelsDir()`. Leave `listener.py` unchanged.
8. Update `test/config.test.ts:58` to assert the new relative value (`bin/piper`) and add an assertion that `readConfig()` resolves it to an absolute path under the active storage root when the env var is set.
9. Add `test/paths.test.ts` with at least four cases: (a) `VOCA_HOME` wins over the pointer, (b) `paths.ts` reads the pointer file when the env var is unset, (c) both unset throws the documented error, (d) `writePointerFile` round-trips and creates missing parent dirs.
10. Update `CLAUDE.md` — replace the directory tree, the listener.py bullet's model-dir note, and the bootstrap bullet to describe the new layout, the `VOCA_HOME` / pointer mechanism, and the new Step 0. Add a "Migration" section listing the two manual options from the issue.
11. Update `README.md` — add one short "Storage layout and migration" section referencing `VOCA_HOME`, the `~/.voca` default, and the move-files-manually instruction.

## Constraints

- Write no automatic migration. Users keep their old path only by typing it at the bootstrap prompt.
- Add no runtime dependency — no `xdg-basedir`, no `env-paths`. Use the Node stdlib.
- Leave OpenClaw's own config alone — `getAvailableProfiles()` still reads `~/.openclaw/openclaw.json`.
- Leave `listener.py` unchanged. Path changes flow only through the daemon's CLI-arg invocation.
- Export no subdir constants evaluated at import time. Every constant `paths.ts` exports must be lazy (getter or function) so tests that set `VOCA_HOME` after import see the right value.
- Preserve the existing test signatures that accept explicit paths. No `readConfig()` or `readSession()` call site in tests changes beyond the one assertion on line 58.
- The default storage root is `~/.voca` — never `~/.voca/assistant` or any nested variant.
- Use conventional-commit messages under `#14` (e.g., `#14 feat(14-storage-relocation): introduce paths module`).

## Verification

- `grep -rn "openclaw/assistant" src/ test/ listener.py` → zero matches (aside from a deliberate migration note in documentation or a back-compat comment inside `paths.ts`).
- `npm run build` → passes without TypeScript errors.
- `npm test` → every existing test passes, plus the new `paths.test.ts` cases.
- Manual: after `rm -rf ~/.voca ~/.config/voca`, `voca status` fails with the "run 'voca bootstrap'" message. Run `voca bootstrap`, accept the `~/.voca` default → the pointer file appears and `config/session/sounds/models/venv/bin` populate under `~/.voca/`. Re-running `voca status` succeeds.
- Manual: `VOCA_HOME=/tmp/voca-alt voca status` uses `/tmp/voca-alt` even when the pointer file points elsewhere.
- Manual: after bootstrap, moving `~/.voca` to `~/.voca-new` and updating the pointer file (`echo ~/.voca-new > ~/.config/voca/root`) lets the daemon start without re-bootstrapping — proof that piper paths stay relative.
- Edge: the bootstrap prompt rejects a relative path with a clear error and re-prompts instead of writing a broken pointer file.

## Материалы

- [GitHub issue #14](https://github.com/yokeloop/voca/issues/14)
- `src/config.ts`, `src/bootstrap.ts`, `src/daemon.ts`, `src/session.ts`, `src/sounds.ts`, `src/voice.ts`
- `listener.py` — already parameterised, reference only
- `test/config.test.ts`, `test/session.test.ts`
- `CLAUDE.md`, `README.md`
