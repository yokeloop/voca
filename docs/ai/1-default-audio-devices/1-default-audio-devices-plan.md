# Default audio devices on startup — implementation plan

**Task:** docs/ai/1-default-audio-devices/1-default-audio-devices-task.md
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Absence of a field = system default

**Decision:** optional `inputDevice?`, `outputDevice?`, `inputDeviceIndex?` in `VocaConfig`; the absence of a key means "use the system default".
**Rationale:** matches the working pattern `opts.deviceIndex !== undefined` in `src/listener.ts:22-24` — the consumer decides whether to skip the flag.
**Alternative:** a sentinel string `"default"`. Rejected: `default` is a real ALSA PCM name, and every consumer would have to parse the string.

### DD-2: Conditional spread for `aplay` arguments

**Decision:** build argv as an array with a conditional spread: `['-r','22050','-f','S16_LE','-c','1', ...(device ? ['-D', device] : [])]` in `speaker.ts` and the same in `sounds.ts`.
**Rationale:** mirrors `src/listener.ts:18-24` — a conditional push of `--device-index` there.
**Alternative:** separate `if/else` branches with their own `spawn` calls. Rejected: duplicates code and reads worse.

### DD-3: Bootstrap deletes the field instead of writing an empty string

**Decision:** on `Use system default (recommended)` selection, do `delete config[opts.field]` (for the input field also `delete config.inputDeviceIndex`), with no `""` / `"default"` in JSON.
**Rationale:** JSON without the field naturally maps to the optional type; the merge `{ ...defaultConfig, ...parsed }` (`src/config.ts:27`) does not break.
**Alternative:** write an empty string. Rejected: consumers would have to distinguish `""` from `undefined`.

### DD-4: Warning about `plughw:X,Y` volatility

**Decision:** `bootstrap.ts` prints a single line before the device list: the system may renumber USB devices after reboot or hotplug — an explicit pick will then break.
**Rationale:** user requirement (from the answer to the synthesize question) — warn without blocking.
**Alternative:** block the `plughw` choice without confirmation. Rejected: an extra interactive prompt.

### DD-5: Existing `hw:0,0` fixtures stay as the override case

**Decision:** `test/daemon.test.ts` keeps `hw:0,0` in `mockConfig` — this is now the "explicit override" case; we add a variant without device fields alongside it.
**Rationale:** preserves happy-path coverage and gives both cases for free.
**Alternative:** rewrite all fixtures. Rejected: more diff without new information.

## Tasks

### Task 1: types-config

- **Files:** `src/types.ts:1-10` (edit), `src/config.ts:8-17` (edit), `test/config.test.ts:20-62` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Mark `inputDevice` and `outputDevice` in `VocaConfig` as optional; add `inputDeviceIndex?: number`. Remove `inputDevice` and `outputDevice` from `defaultConfig`.
- **How:** In `types.ts:2-3` put `?:` after the field names, add the line `inputDeviceIndex?: number;`. In `config.ts:9-10` remove the `inputDevice` / `outputDevice` lines. In `test/config.test.ts:32` drop the `read.inputDevice` vs defaults comparison (both undefined → not throwing is enough). At `:53-54` replace `expect(defaultConfig.inputDevice).toBe('plughw:2,0')` with `expect(defaultConfig.inputDevice).toBeUndefined()` and likewise for `outputDevice`. Add two new tests: (1) `readConfig` on an empty file returns `inputDevice: undefined`; (2) `readConfig` preserves an `inputDeviceIndex: 3` passed via JSON.
- **Context:** `src/types.ts`, `src/config.ts`, `test/config.test.ts`.
- **Verify:** `npm run build` passes; `npm test test/config.test.ts` is green.

### Task 2: speaker

- **Files:** `src/speaker.ts:14-35` (edit)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Make `device` in `speak()` parameters optional; do not pass `-D` when it is absent.
- **How:** In the signature `speak(opts: { ...; device?: string })`. Build `aplayArgs = ['-r','22050','-f','S16_LE','-c','1', ...(opts.device !== undefined ? ['-D', opts.device] : [])]` and pass it to `spawn('aplay', aplayArgs, ...)`. Other behavior — unchanged.
- **Context:** `src/speaker.ts`, `src/listener.ts:18-24` (pattern).
- **Verify:** `npm run build` passes; speaker call sites in `daemon.ts` still compile.

### Task 3: sounds

- **Files:** `src/sounds.ts:18-27` (edit)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Make `device` in `playSound()` optional; do not pass `-D` when it is absent.
- **How:** In the signature `opts: { device?: string }`. Build `args = ['-D', opts.device, soundFile(type)]` when `opts.device !== undefined`, otherwise `args = [soundFile(type)]`. Pass it to `execFile('aplay', args, ...)`.
- **Context:** `src/sounds.ts`, `src/listener.ts:18-24` (pattern).
- **Verify:** `npm run build` passes; `playSound` call sites in `daemon.ts` still compile.

### Task 4: daemon

- **Files:** `src/daemon.ts:49-54` (edit)
- **Depends on:** Task 1, Task 2, Task 3
- **Scope:** S
- **What:** Remove the hardcoded `deviceIndex: useStub ? undefined : 0`; forward `this.config.inputDeviceIndex`.
- **How:** Replace `deviceIndex: useStub ? undefined : 0,` with `deviceIndex: useStub ? undefined : this.config.inputDeviceIndex,`. `playSound` calls (`daemon.ts:131, 163, 259`) and `speak` (`daemon.ts:225-230`) already pass `this.config.outputDevice` — no change needed; after Task 1 they will correctly pass `undefined`.
- **Context:** `src/daemon.ts`, `src/listener.ts:22-24`.
- **Verify:** `npm run build` passes; `npm test test/daemon.test.ts` is green on existing tests.

### Task 5: bootstrap

- **Files:** `src/bootstrap.ts:146-179` (edit), `src/bootstrap.ts:364-371` (edit)
- **Depends on:** Task 1
- **Scope:** M
- **What:** In `selectDevice()` add `Use system default (recommended)` as the first item; on selection remove the field from the config. Before the list print a warning about `plughw:X,Y` volatility.
- **How:** In `selectDevice` (`bootstrap.ts:146-179`):
  1. Right after `console.log('\n=== ... ===')` (`:150`) print `console.log('Note: ALSA plughw:X,Y indices may shift after reboot or USB re-plug — "Use system default" survives that.')`.
  2. In `options` (`:165`) insert `'Use system default (recommended)'` as the first element; then `devices.map(...)`; add the `Keep current: <value>` item only when `config[opts.field] !== undefined`.
  3. In the selection handling, before the existing branches: `if (selected === 'Use system default (recommended)') { delete config[opts.field]; if (opts.field === 'inputDevice') delete config.inputDeviceIndex; console.log(\`${opts.label} device: system default\`); return; }`.
- **Context:** `src/bootstrap.ts:60-124` (select helper), `src/bootstrap.ts:146-179`, `src/bootstrap.ts:364-371` (call sites — check integrity).
- **Verify:** `npm run build` passes. Manual check: `voca bootstrap` on a clean config → in steps 1/2 the first item is `Use system default (recommended)`, the warning is printed; after selecting this item, `~/.openclaw/assistant/config.json` has no `inputDevice` / `outputDevice` / `inputDeviceIndex` keys.

### Task 6: speaker+sounds unit tests

- **Files:** `test/speaker.test.ts` (create), `test/sounds.test.ts` (create)
- **Depends on:** Task 2, Task 3
- **Scope:** M
- **What:** Add unit tests for the conditional `-D` in `speaker.ts` and `sounds.ts`.
- **How:** Mirror the style of `test/daemon.test.ts:1-76` (`vi.mock` + `beforeEach` + `vi.clearAllMocks`).
  - `test/speaker.test.ts`: `vi.mock('node:child_process', ...)` intercepting `spawn`. Two tests: (1) `speak({ device: 'hw:0,0', ... })` → the last argument of `spawn('aplay', args)` contains `-D` and `'hw:0,0'`; (2) `speak({ device: undefined, ... })` → args do not contain `-D`. Mock `piper.stdout.pipe`, `aplay.on('close')` with `code=0` so that `speak()` resolves.
  - `test/sounds.test.ts`: `vi.mock('node:child_process', ...)` intercepting `execFile`. Two tests: (1) `playSound('wake', { device: 'hw:0,0' })` → `execFile` called with args containing `-D`; (2) `playSound('wake', { device: undefined })` → args do not contain `-D`.
- **Context:** `test/daemon.test.ts:1-76` (mock style), `src/speaker.ts`, `src/sounds.ts`.
- **Verify:** `npm test test/speaker.test.ts test/sounds.test.ts` is green.

### Task 7: daemon test variant

- **Files:** `test/daemon.test.ts:51-62, 93-102, 113-232` (edit)
- **Depends on:** Task 4
- **Scope:** S
- **What:** Add a test: config without `inputDevice` / `outputDevice` / `inputDeviceIndex` → `spawnListener` called without `deviceIndex`, `playSound` / `speak` receive `device: undefined`.
- **How:** Next to the existing `describe('VocaDaemon')` add `describe('VocaDaemon with default devices')` with its own `mockConfigDefault` (without the three device fields). In `beforeEach` create `new VocaDaemon(mockConfigDefault)`. A single test: call `daemon.start()`, emit `'wake'`, `'recorded'`, `flush()`; check that `vi.mocked(spawnListener).mock.calls[0][0].deviceIndex === undefined`, `playSound` is called with `{ device: undefined }`, `speak` — with `device: undefined`. Existing `hw:0,0` fixtures remain as the override case; their assertions are unchanged.
- **Context:** `test/daemon.test.ts` (whole file), `src/daemon.ts`.
- **Verify:** `npm test test/daemon.test.ts` is green on all tests (old and new).

### Task 8: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full check run + manual scenarios from the Verification section of the task file.
- **How:** Run `npm run build`, `npm test`. Walk through manual scenarios:
  1. `rm ~/.openclaw/assistant/config.json && voca bootstrap` → first item is `Use system default (recommended)`, warning printed. After picking default, `config.json` has no device fields.
  2. `voca start` with a config without device fields: `pgrep -af 'aplay'` during TTS does not contain `-D`; `pgrep -af 'listener.py'` does not contain `--device-index`.
  3. `voca start` with config `{ "inputDevice": "plughw:2,0", "outputDevice": "plughw:2,0" }`: `aplay` spawned with `-D plughw:2,0`; `listener.py` — without `--device-index`.
  4. Reboot simulation: change the default PyAudio device, `voca start` works without editing the config.
- **Context:** task Verification section.
- **Verify:** `npm run build && npm test` green; manual scenarios pass.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 8 tasks, medium complexity, files do not overlap between tasks — parallel groups of 2-3 tasks in agents with `isolation: worktree`.
- **Order:**
  Group 1 (sequential): Task 1
  ─── barrier ───
  Group 2 (parallel): Task 2, Task 3, Task 5
  ─── barrier ───
  Group 3 (sequential): Task 4
  ─── barrier ───
  Group 4 (parallel): Task 6, Task 7
  ─── barrier ───
  Group 5 (sequential): Task 8

## Verification

- `npm run build` → no TypeScript errors.
- `npm test` → existing tests green; new "config without device fields" test green.
- `rm ~/.openclaw/assistant/config.json && voca bootstrap` → in steps 1 and 2 the first item is `Use system default (recommended)`, the warning about `plughw:X,Y` volatility is printed before the list. After selecting this item, the saved `config.json` has no `inputDevice` / `outputDevice` keys.
- After `voca start` with a config without device fields: `pgrep -af 'aplay'` during TTS does not contain `-D`; `pgrep -af 'listener.py'` does not contain `--device-index`.
- `voca start` with config `{ "inputDevice": "plughw:2,0", "outputDevice": "plughw:2,0" }` (existing override): aplay spawned with `-D plughw:2,0`; listener.py — without `--device-index` (`inputDeviceIndex` not set).
- Reboot scenario: "Use system default" selected, USB microphone moved from `card 2` to `card 3` → `voca start` works unchanged (PyAudio picked up the new default).
- `voca start` with PulseAudio/PipeWire stopped and an empty `~/.asoundrc` → `aplay` without `-D` uses the ALSA default PCM; the daemon does not crash.

## Materials

- [GitHub Issue #1](https://github.com/yokeloop/voca/issues/1)
- `~/.openclaw/assistant/config.json`
- `src/types.ts`, `src/config.ts`, `src/daemon.ts`, `src/listener.ts`, `src/speaker.ts`, `src/sounds.ts`, `src/bootstrap.ts`
- `listener.py`
- `test/config.test.ts`, `test/daemon.test.ts`
