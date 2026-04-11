# VOCA — Voice Assistant Implementation — Implementation Plan

**Task:** docs/ai/voca-implementation/voca-implementation-task.md
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Module boundary — daemon-state.ts is pure, daemon.ts owns all I/O

**Decision:** `daemon-state.ts` exports a single `transition(state, event)` pure function with no imports from Node.js. `daemon.ts` owns the EventEmitter, child process handles, and calls `transition()` on every event.
**Rationale:** The task explicitly requires a testable pure state machine. Keeping all I/O in `daemon.ts` lets tests import `daemon-state.ts` without mocking anything. This matches the test coverage requirement: `daemon-state.test.ts` tests transitions only.
**Alternative:** Put transition logic inside `VocaDaemon` class methods — harder to test, more coupling.

### DD-2: Child process API — spawn with stdio pipes, not execFile

**Decision:** `listener.ts` and `recorder.ts` use `spawn` (streaming stdout). `transcriber.ts` and `agent.ts` use `execFile` (buffered stdout + promise).
**Rationale:** `listener.py` emits a stream of JSON lines continuously — `spawn` with `stdout` pipe fits naturally. `whisper-stt-wrapper` and `openclaw` run to completion and return a single result — `execFile` with `{ maxBuffer }` is simpler and matches the integration contract exactly.
**Alternative:** Use `spawn` for everything — more code, no benefit for one-shot tools.

### DD-3: listener.py operates in two modes — stdin-stub and real openWakeWord

**Decision:** `listener.py` checks for `--stub` flag at startup. With `--stub` it reads stdin and emits events. Without `--stub` it loads openWakeWord models and captures audio via PyAudio.
**Rationale:** The task requires a working stub before bootstrap installs openWakeWord. A single file with a mode flag avoids maintaining two files. `listener.ts` passes `--stub` in dev/before-bootstrap.
**Alternative:** Two separate Python files (`listener-stub.py`, `listener.py`) — more files to maintain, more logic in `listener.ts` to choose which to spawn.

### DD-4: Sound files ship in the repo, bootstrap copies them

**Decision:** `sounds/wake.wav`, `sounds/stop.wav`, `sounds/error.wav` are generated once via `sox` and committed to the repo. `bootstrap.ts` copies them to `~/.openclaw/assistant/sounds/`.
**Rationale:** The task says "generate via sox" — do this once during development, commit artifacts. At runtime `sounds.ts` reads from `~/.openclaw/assistant/sounds/`, not the repo. This means `voca` works after `npm install -g` without `sox` on the target machine.
**Alternative:** Generate sounds on-demand in `sounds.ts` — requires `sox` at runtime, adds startup latency.

### DD-5: Config defaults baked into defaultConfig, not scattered

**Decision:** `config.ts` exports `defaultConfig: VocaConfig` with all fields populated (device `plughw:2,0`, language `ru`, wakeWord `hey_jarvis`, stopWord `stop`, piperModel `ru_RU-irina-medium`, agentId `personal`). `readConfig` merges file content over defaults.
**Rationale:** Merge-on-read makes partial config files work. Defaults are centralised in one place. Tests import `defaultConfig` directly without filesystem.
**Alternative:** Throw on missing fields — breaks before bootstrap runs.

### DD-6: whisper-stt-wrapper called with file as first positional argument

**Decision:** `transcriber.ts` invokes `execFile('whisper-stt-wrapper', [filePath, '--language', language])`. No `--file` flag.
**Rationale:** The wrapper's actual CLI is `whisper-stt <audio-file> [--language LANG]` (confirmed from `/usr/local/bin/whisper-stt-wrapper` and `/home/priney/whisper-stt/whisper-stt`). File is positional argument 1.
**Alternative:** Use `--file` flag — does not exist, would fail.

### DD-7: types.ts holds all shared interfaces, no circular imports

**Decision:** `src/types.ts` declares `VocaConfig`, `VocaSession`, `DaemonState`, `DaemonEvent`, `ListenerHandle`, `RecorderHandle`, `AgentResponse`. All other modules import from `types.ts`, never from each other for type purposes.
**Rationale:** Flat project with 13 source files. Centralised types prevent circular dependency chains and give tests a single import point.
**Alternative:** Co-locate types in each module — fine at small scale but creates cross-module import cycles when `daemon.ts` needs types from multiple adapters.

## Tasks

### Task 1: Project scaffold — package.json, tsconfig.json, vitest.config.ts

- **Files:** `package.json` (create), `tsconfig.json` (create), `vitest.config.ts` (create)
- **Depends on:** none
- **Scope:** S
- **What:** Create the three project config files that make `npm install`, `npm run build`, and `npm test` work.
- **How:**
  - `package.json`: name `@yokeloop/voca`, version `0.1.0`, type `module`, bin `{ "voca": "./dist/cli.js" }`, scripts: `build: tsc`, `dev: tsx src/cli.ts`, `test: vitest run`. Dependencies: `commander`. DevDependencies: `typescript`, `tsx`, `vitest`, `@types/node`.
  - `tsconfig.json`: `target: ES2022`, `module: NodeNext`, `moduleResolution: NodeNext`, `strict: true`, `outDir: dist`, `rootDir: src`, `declaration: true`.
  - `vitest.config.ts`: minimal config, include `test/**/*.test.ts`, environment `node`.
- **Context:** CLAUDE.md (architecture, commands)
- **Verify:** `npm install` succeeds. `npx tsc --noEmit` runs (fails on missing src files — expected). `npx vitest run` runs (0 tests — expected).

### Task 2: types.ts — all shared interfaces

- **Files:** `src/types.ts` (create)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Declare all TypeScript interfaces and type aliases shared across modules.
- **How:**
  - `VocaConfig`: `inputDevice: string`, `outputDevice: string`, `profile: string`, `wakeWord: string`, `stopWord: string`, `piperModel: string`, `piperBin: string`, `language: string`.
  - `VocaSession`: `sessionId: string`, `messageCount: number`, `profile: string`, `createdAt: string`.
  - `DaemonState`: union `'IDLE' | 'LISTENING' | 'RECORDING' | 'PROCESSING' | 'SPEAKING'`.
  - `DaemonEvent`: union `'WAKE' | 'STOP' | 'RECORD_CANCEL' | 'PROCESSING_DONE' | 'SPEAKING_DONE' | 'ERROR'`.
  - `ListenerHandle`: `{ on(event: 'wake' | 'stop', cb: () => void): void; pause(): void; resume(): void; kill(): void }`.
  - `RecorderHandle`: `{ filePath: string; stop(): void; cancel(): void; on(event: 'done' | 'cancel', cb: () => void): void }`.
  - `AgentResponse`: `{ text: string; sessionId: string }`.
- **Context:** CLAUDE.md, docs/ai/voca-implementation/voca-implementation-task.md (Requirements section)
- **Verify:** `npx tsc --noEmit --strict --target ES2022 --module NodeNext --moduleResolution NodeNext src/types.ts` — zero errors.

### Task 3: config.ts + session.ts + their tests

- **Files:** `src/config.ts` (create), `src/session.ts` (create), `test/config.test.ts` (create), `test/session.test.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement config and session read/write modules with full test coverage.
- **How:**
  - `config.ts`:
    - `CONFIG_PATH = path.join(os.homedir(), '.openclaw/assistant/config.json')`
    - `defaultConfig: VocaConfig` — all fields from DD-5 defaults
    - `ensureConfigDir()` — `mkdir -p` on the dir
    - `readConfig(): Promise<VocaConfig>` — read file, JSON.parse, merge over defaultConfig with `{ ...defaultConfig, ...parsed }`; if ENOENT return defaultConfig
    - `writeConfig(cfg: VocaConfig): Promise<void>` — ensureConfigDir, then write JSON
  - `session.ts`:
    - `SESSION_PATH = path.join(os.homedir(), '.openclaw/assistant/session.json')`
    - `generateSessionId(): string` — `'asst-' + Date.now()`
    - `newSession(profile: string): VocaSession` — fresh object
    - `readSession(): Promise<VocaSession>` — read or create new with profile `personal`
    - `writeSession(s: VocaSession): Promise<void>`
    - `incrementMessageCount(): Promise<VocaSession>` — read, increment, write, return
    - `resetSessionForProfile(profile: string): Promise<VocaSession>` — newSession + write
  - Tests: use `vi.mock('node:fs/promises')` or write to temp dirs via `os.tmpdir()`. Test read-on-missing-file returns defaults. Test profile change resets session ID. Test incrementMessageCount. All tests use temp path override, not actual `~/.openclaw`.
- **Context:** `src/types.ts` (VocaConfig, VocaSession), CLAUDE.md (~/.openclaw/assistant/ layout)
- **Verify:** `npm test test/config.test.ts test/session.test.ts` — all green.

### Task 4: cli.ts — Commander entry point (session/profile commands)

- **Files:** `src/cli.ts` (create)
- **Depends on:** Task 3
- **Scope:** M
- **What:** Wire up the `session` and `profile` subcommands via Commander.
- **How:**
  - Top-level program: `name('voca')`, `version` from package.json
  - `session new` → `resetSessionForProfile(currentProfile)`, print new sessionId
  - `session info` → `readSession()`, print sessionId, profile, messageCount, createdAt
  - `profile list` → print `personal`, `public` (hardcoded list for now; bootstrap will expand)
  - `profile use <id>` → validate id is `personal` or `public`, call `resetSessionForProfile(id)`, write config with updated profile
  - Add placeholder commands `start`, `stop`, `status`, `bootstrap` that print "not yet implemented" — these get filled in later tasks
  - Export `program` for testing; call `program.parse()` only when `import.meta.url === pathToFileURL(process.argv[1]).href`
- **Context:** `src/config.ts`, `src/session.ts`, `src/types.ts`. Commander docs pattern: `program.command('session').command('new').action(...)`.
- **Verify:** `npx tsx src/cli.ts session new` prints new session ID. `npx tsx src/cli.ts profile use public` switches profile and resets session. `npx tsx src/cli.ts profile list` prints two profiles.

### Task 5: sounds.ts + generate sound files via sox

- **Files:** `src/sounds.ts` (create), `sounds/wake.wav` (create), `sounds/stop.wav` (create), `sounds/error.wav` (create)
- **Depends on:** Task 2
- **Scope:** S
- **What:** Implement sound playback module and generate the three default WAV files.
- **How:**
  - Generate sound files with `sox` (run in shell, commit artifacts):
    - `wake.wav`: `sox -n -r 22050 -c 1 sounds/wake.wav synth 0.1 sine 880`
    - `stop.wav`: `sox -n -r 22050 -c 1 /tmp/beep.wav synth 0.1 sine 880 && sox /tmp/beep.wav /tmp/beep.wav sounds/stop.wav` (two beeps concatenated)
    - `error.wav`: `sox -n -r 22050 -c 1 sounds/error.wav synth 0.3 sine 220`
  - `sounds.ts`:
    - `SOUNDS_DIR = path.join(os.homedir(), '.openclaw/assistant/sounds')`
    - `soundFile(type: 'wake' | 'stop' | 'error'): string` — returns path in SOUNDS_DIR
    - `playSound(type, opts: { device: string }): Promise<void>` — `execFile('aplay', ['-D', opts.device, soundFile(type)])`; resolves when aplay exits
    - `playWake`, `playStop`, `playError` convenience wrappers that call `playSound` with the right type
- **Context:** `src/types.ts`, CLAUDE.md (aplay -D plughw:2,0)
- **Verify:** `ls sounds/` shows three WAV files. `git ls-files sounds/` confirms all three are tracked. `npx tsx -e "import('./src/sounds.js').then(m => m.playSound('wake', {device:'plughw:2,0'}))"` plays beep (requires bootstrap-copied sounds). Note: DD-4 overrides requirement 8 (bootstrap generates sounds) — sounds are generated once during development and committed to avoid requiring sox at runtime.

### Task 6: listener.py (stub mode) + listener.ts

- **Files:** `listener.py` (create), `src/listener.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement the Python listener script (stub mode) and the TypeScript module that spawns and manages it.
- **How:**
  - `listener.py`:
    - Parse argv: if `--stub` in argv, run stdin loop: read line, if `wake` emit `{"event":"wake"}\n`, if `stop` emit `{"event":"stop"}\n`
    - Real openWakeWord section (skeleton for Phase 6): import guard `try: import openwakeword` wrapped in `if '--stub' not in sys.argv`
    - Always flush stdout after every print
  - `listener.ts`:
    - `spawnListener(opts: { pythonBin?: string; modelDir: string; stub?: boolean }): ListenerHandle`
    - Spawn `python3 listener.py [--stub]` with `stdio: ['ignore', 'pipe', 'inherit']`
    - Parse stdout line-by-line (readline on `process.stdout`), parse JSON, emit `wake` or `stop` events
    - `pause()` → `process.kill(child.pid!, 'SIGSTOP')`
    - `resume()` → `process.kill(child.pid!, 'SIGCONT')`
    - `kill()` → `child.kill('SIGTERM')`
    - Return EventEmitter-like object implementing `ListenerHandle`
- **Context:** `src/types.ts` (ListenerHandle), CLAUDE.md (SIGSTOP/SIGCONT, listener.py stdin mode)
- **Verify:** `echo "wake" | python3 listener.py --stub` outputs `{"event":"wake"}`. `npx tsx -e "import('./src/listener.js').then(m => { const h = m.spawnListener({stub:true, modelDir:''}); h.on('wake', () => console.log('got wake')); })"` — process starts, no crash.

### Task 7: recorder.ts — sox rec with silence detection

- **Files:** `src/recorder.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement recording via `sox rec` with silence detection and max-duration guard.
- **How:**
  - `startRecording(opts: { device: string; tmpDir?: string }): RecorderHandle`
  - Generate `filePath = path.join(tmpDir || os.tmpdir(), 'voca-rec-' + Date.now() + '.wav')`
  - Spawn sox: `sox -t alsa ${opts.device} ${filePath} silence 1 0.1 0.1% 1 30 0.1%` — records until silence for 30s
  - Add `setTimeout` at 120s (2 min): call `cancel()` if still running
  - `stop()` — send SIGTERM to sox child; sox flushes WAV header on SIGTERM
  - `cancel()` — stop() + emit `'cancel'` event + `fs.unlink(filePath)` (best-effort)
  - `on('done', cb)` — fired when sox exits with code 0 after silence detection
  - `on('cancel', cb)` — fired on cancel or timeout
- **Context:** `src/types.ts` (RecorderHandle), CLAUDE.md (sox rec, silence >30s = cancel, max 2 min)
- **Verify:** `npx tsx -e "import('./src/recorder.js').then(m => { const r = m.startRecording({device:'plughw:2,0'}); r.on('done', () => console.log('done', r.filePath)); })"` — starts recording; file appears at path.

### Task 8: transcriber.ts + transcriber test

- **Files:** `src/transcriber.ts` (create), `test/transcriber.test.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement transcription via whisper-stt-wrapper and test output parsing + stop-trimming.
- **How:**
  - `transcribe(filePath: string, opts: { language?: string }): Promise<string>`
  - Call `execFile('/usr/local/bin/whisper-stt-wrapper', [filePath, '--language', opts.language ?? 'ru'], { maxBuffer: 1024 * 1024 })`
  - Trim stdout: `stdout.trim()`
  - Strip trailing "stop" (case-insensitive): `text.replace(/\s*stop\s*$/i, '').trim()`
  - Return empty string if result is empty after trimming (daemon will cancel)
  - On execFile error, throw `TranscribeError` with stderr
  - Test file: mock `node:child_process` `execFile` using `vi.mock`. Test cases:
    - Normal text returned as-is
    - Text ending in "stop " gets trimmed
    - Text ending in "STOP" gets trimmed
    - Text that is only "stop" returns empty string
    - execFile error → throws TranscribeError
- **Context:** `src/types.ts`, CLAUDE.md (whisper-stt file as first arg, trim trailing "stop")
- **Verify:** `npm test test/transcriber.test.ts` — all green.

### Task 9: agent.ts + agent test

- **Files:** `src/agent.ts` (create), `test/agent.test.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement OpenClaw agent CLI invocation and test JSON response parsing.
- **How:**
  - `queryAgent(opts: { agentId: string; sessionId: string; message: string; timeoutS?: number }): Promise<AgentResponse>`
  - Call `execFile('/home/priney/.npm-global/bin/openclaw', ['agent', '--agent', opts.agentId, '--session-id', opts.sessionId, '--message', opts.message, '--json', '--timeout', String(opts.timeoutS ?? 900)], { maxBuffer: 10 * 1024 * 1024, timeout: (opts.timeoutS ?? 900) * 1000 + 5000 })`
  - Parse stdout as JSON; extract text field (openclaw --json returns `{ "text": "...", ... }`)
  - On JSON parse error: throw `AgentError('invalid response: ' + stdout.slice(0, 200))`
  - On execFile error: throw `AgentError` with stderr
  - Test file: `vi.mock('node:child_process')`. Test cases:
    - Valid JSON response → returns `AgentResponse`
    - Non-JSON stdout → throws AgentError
    - execFile error (non-zero exit) → throws AgentError
    - Verify correct argv construction (agentId, sessionId, message, timeout)
- **Context:** `src/types.ts` (AgentResponse), CLAUDE.md (openclaw agent CLI, --json, timeout 900s), `/home/priney/.openclaw/openclaw.json` (agents: personal, public)
- **Verify:** `npm test test/agent.test.ts` — all green.

### Task 10: speaker.ts — piper | aplay pipe

- **Files:** `src/speaker.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement TTS via piper piped to aplay, no intermediate files, 500ms post-pause.
- **How:**
  - `speak(opts: { text: string; piperBin: string; piperModel: string; device: string }): Promise<void>`
  - Spawn piper: `spawn(opts.piperBin, ['--model', opts.piperModel, '--output_raw'], { stdio: ['pipe', 'pipe', 'inherit'] })`
  - Write `opts.text + '\n'` to piper.stdin, then `piper.stdin.end()`
  - Spawn aplay: `spawn('aplay', ['-D', opts.device, '-r', '22050', '-f', 'S16_LE', '-c', '1'], { stdio: ['pipe', 'inherit', 'inherit'] })`
  - Pipe piper.stdout → aplay.stdin: `piper.stdout.pipe(aplay.stdin)`
  - Promise resolves when aplay exits (code 0 or 1) — wait for `aplay` process close event
  - After aplay exits, `await sleep(500)` before resolving
  - On piper exit non-zero: reject with `SpeakerError`
  - Export `sleep(ms: number): Promise<void>` for test use
- **Context:** CLAUDE.md (piper --output_raw | aplay -r 22050 -f S16_LE -c 1, 500ms pause)
- **Verify:** `speak({text:'Hello', piperBin:'~/.openclaw/assistant/bin/piper', piperModel:'ru_RU-irina-medium', device:'plughw:2,0'})` speaks (requires bootstrap). Module loads without error before piper is installed.

### Task 11: daemon-state.ts + daemon-state tests

- **Files:** `src/daemon-state.ts` (create), `test/daemon-state.test.ts` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Implement pure state machine and test all valid and invalid transitions.
- **How:**
  - `transition(state: DaemonState, event: DaemonEvent): DaemonState`
  - Transition table:
    - `IDLE + WAKE → LISTENING`
    - `LISTENING + START_RECORD → RECORDING` (daemon emits START_RECORD internally after playWake() resolves)
    - `LISTENING + STOP → IDLE` (stop word before recording starts cancels the cycle)
    - `RECORDING + STOP → PROCESSING`
    - `RECORDING + RECORD_CANCEL → IDLE`
    - `RECORDING + ERROR → IDLE`
    - `PROCESSING + PROCESSING_DONE → SPEAKING`
    - `PROCESSING + ERROR → IDLE`
    - `SPEAKING + SPEAKING_DONE → IDLE`
    - `SPEAKING + ERROR → IDLE`
    - All other combinations → throw `InvalidTransitionError(state, event)`
  - Update `src/types.ts` DaemonEvent to include `'START_RECORD'`.
  - Test all valid transitions (one test per row).
  - Test that invalid transitions throw `InvalidTransitionError`.
- **Context:** `src/types.ts` (DaemonState, DaemonEvent), CLAUDE.md state machine description
- **Verify:** `npm test test/daemon-state.test.ts` — all green.

### Task 12a: daemon.ts — class scaffold + IDLE→LISTENING→RECORDING cycle

- **Files:** `src/daemon.ts` (create)
- **Depends on:** Task 3, Task 5, Task 6, Task 7, Task 11
- **Scope:** M
- **What:** Implement `VocaDaemon` class with listener spawn, wake→beep→record cycle, and cancel-on-silence path.
- **How:**
  - `class VocaDaemon extends EventEmitter`
  - Constructor accepts `config: VocaConfig`
  - Private fields: `state: DaemonState`, `listener: ListenerHandle | null`, `recorder: RecorderHandle | null`
  - `async start(): Promise<void>`:
    1. Spawn listener with `spawnListener({ stub: true, modelDir: ASSISTANT_DIR + '/models' })`
    2. Set `state = 'IDLE'`
    3. Register listener `'wake'` handler → `handleWake()`
    4. Register listener `'stop'` handler → `handleStop()`
  - `async stop(): Promise<void>`: kill listener, cancel recorder if active, emit `'stopped'`
  - `handleWake()`:
    1. If state !== IDLE, return (ignore duplicate wake)
    2. `state = transition(state, 'WAKE')` → LISTENING
    3. `listener.pause()`
    4. `await playWake(config)`
    5. `state = transition(state, 'START_RECORD')` → RECORDING
    6. Start recorder, register `'done'` and `'cancel'` handlers
  - `handleStop()`: if state === RECORDING, `recorder.stop()` (sox exits, triggers done event)
  - On recorder `'cancel'`: `state = transition(state, 'RECORD_CANCEL')` → IDLE, `listener.resume()`
  - On recorder `'done'`: `state = transition(state, 'STOP')` → PROCESSING (pipeline implemented in Task 12b)
  - Error handling: catch → `playError`, transition to IDLE via ERROR event, `listener.resume()`
- **Context:** `src/types.ts`, `src/daemon-state.ts`, `src/listener.ts`, `src/recorder.ts`, `src/sounds.ts`, `src/config.ts`
- **Verify:** `npx tsx src/cli.ts start` — daemon starts with `--stub` listener. Type "wake" → beep plays, recording starts. Type "stop" → recording stops, state transitions to PROCESSING (no further action yet). Cancel on silence timeout returns to IDLE.

### Task 12b: daemon.ts — PROCESSING→SPEAKING→IDLE pipeline

- **Files:** `src/daemon.ts` (edit — add processing pipeline)
- **Depends on:** Task 12a, Task 8, Task 9, Task 10
- **Scope:** M
- **What:** Wire transcription, agent query, and TTS into the daemon's PROCESSING→SPEAKING→IDLE path.
- **How:**
  - On recorder `'done'` (already transitions to PROCESSING in 12a), run pipeline:
    1. Transcribe filePath via `transcribe()`; if empty → ERROR transition → IDLE, `listener.resume()`
    2. Query agent via `queryAgent()` with transcript
    3. `state = transition(state, 'PROCESSING_DONE')` → SPEAKING
    4. Increment session message count via `incrementMessageCount()`
    5. `await speak()` with agent response text
    6. `state = transition(state, 'SPEAKING_DONE')` → IDLE
    7. `listener.resume()`
  - Error handling at every await: catch → `playError`, transition to IDLE via ERROR event, `listener.resume()`
- **Context:** `src/daemon.ts` (Task 12a output), `src/transcriber.ts`, `src/agent.ts`, `src/speaker.ts`, `src/session.ts`
- **Verify:** `npx tsx src/cli.ts start` — type "wake" → beep → recording. Type "stop" → double-beep → transcription logged → agent response logged → TTS speaks. Full cycle returns to IDLE.

### Task 13: cli.ts — start/stop/status commands

- **Files:** `src/cli.ts` (edit — add start, stop, status implementations)
- **Depends on:** Task 12b
- **Scope:** M
- **What:** Wire up `start`, `stop`, `status` commands in the existing cli.ts.
- **How:**
  - `start` (foreground): `readConfig()` → `new VocaDaemon(config)` → `daemon.start()` → block on SIGINT/SIGTERM
  - SIGINT/SIGTERM handler: `await daemon.stop()`, then `process.exit(0)`
  - `status` (foreground mode only for now): print current state from the daemon instance if running in same process; otherwise print "daemon not running" (Phase 7 adds PID file)
  - Leave `--daemon` flag placeholder returning "not yet implemented" — Phase 7
- **Context:** `src/daemon.ts`, `src/config.ts`, `src/cli.ts` (existing session/profile commands)
- **Verify:** `npx tsx src/cli.ts start` starts daemon, listener.py spawns, logs appear. Ctrl+C cleanly exits.

### Task 14: bootstrap.ts — interactive setup

- **Files:** `src/bootstrap.ts` (create)
- **Depends on:** Task 3, Task 5, Task 13
- **Scope:** L
- **What:** Implement interactive setup that installs piper, creates Python venv, installs openWakeWord, downloads ONNX models, and copies sounds.
- **How:**
  - `ASSISTANT_DIR = path.join(os.homedir(), '.openclaw/assistant')`
  - `VENV_DIR = ASSISTANT_DIR + '/venv'`
  - `PIPER_DIR = ASSISTANT_DIR + '/bin'`
  - Helper `confirm(question: string): Promise<boolean>` — readline question, y/n
  - Helper `run(cmd: string, args: string[]): Promise<void>` — spawn with stdio inherit
  - Step 1: Select mic device — `arecord -l` → list devices, prompt user to pick. Write to config.
  - Step 2: Select speaker device — `aplay -l` → list, prompt. Write to config.
  - Step 3: Select profile — prompt for personal/public. Write to config.
  - Step 4: Piper install — check `PIPER_DIR/piper` exists. If not, confirm → download aarch64 release from GitHub (`piper_linux_aarch64.tar.gz`), extract to `PIPER_DIR`. Also download `ru_RU-irina-medium.onnx` and `.json` to `ASSISTANT_DIR/bin/`.
  - Step 5: Python venv — check `VENV_DIR/bin/python3` exists. If not, confirm → `python3 -m venv VENV_DIR`. Then `pip install openwakeword pyaudio` in venv.
  - Step 6: ONNX models — check `ASSISTANT_DIR/models/hey_jarvis.onnx` exists. If not, confirm → download from openWakeWord GitHub releases.
  - Step 7: Copy sounds — `cp sounds/wake.wav stop.wav error.wav ASSISTANT_DIR/sounds/` (mkdir -p first).
  - Each step prints status and skips if already installed.
  - Wire into `cli.ts` `bootstrap` command.
- **Context:** `src/config.ts`, `src/sounds.ts`, CLAUDE.md (venv in ~/.openclaw/assistant/venv/, piper binary + models in ~/.openclaw/assistant/bin/)
- **Verify:** `npx tsx src/cli.ts bootstrap` — runs interactively, installs correctly. `ls ~/.openclaw/assistant/` shows expected directories.

### Task 15: listener.py — real openWakeWord implementation

- **Files:** `listener.py` (edit — add real openWakeWord section)
- **Depends on:** Task 6, Task 14
- **Scope:** M
- **What:** Implement the real openWakeWord inference loop in listener.py (non-stub mode).
- **How:**
  - Parse argv: `--model-dir <path>`, `--wake-model <name>`, `--stop-model <name>`, `--device-index <int>`, `--stub`
  - Non-stub mode:
    1. Import `openwakeword`, `pyaudio`, `numpy`
    2. Init PyAudio, open stream: `rate=16000, channels=1, format=paInt16, frames_per_buffer=1280, input_device_index=args.device_index`
    3. Load wake model: `openwakeword.Model(wakeword_models=[model_dir + '/' + wake_model + '.onnx'], ...)`
    4. Load stop model separately (or combined)
    5. Inference loop: read chunk from stream, run model prediction, if score > 0.5 for wake model → print `{"event":"wake"}`, flush; if score > 0.5 for stop model → print `{"event":"stop"}`, flush
    6. Handle SIGSTOP/SIGCONT at OS level (OS handles this — Python process pauses/resumes naturally)
    7. Handle SIGTERM → clean close PyAudio, exit 0
  - Stub mode unchanged from Task 6
- **Context:** `listener.py` (existing stub from Task 6), CLAUDE.md (listener.py long-lived, SIGSTOP/SIGCONT, models in ~/.openclaw/assistant/models/)
- **Verify:** After `voca bootstrap`, `python3 listener.py --model-dir ~/.openclaw/assistant/models --wake-model hey_jarvis --stop-model stop --device-index 0` — detects wake word from mic.

### Task 16: daemon mode — PID file, stop, status from file

- **Files:** `src/cli.ts` (edit — implement --daemon, stop, status), `src/daemon.ts` (edit — write state to file)
- **Depends on:** Task 13
- **Scope:** M
- **What:** Add `--daemon` background fork, PID file, daemon-state.json, `stop` and `status` reading from files.
- **How:**
  - `PID_FILE = ASSISTANT_DIR + '/daemon.pid'`
  - `STATE_FILE = ASSISTANT_DIR + '/daemon-state.json'`
  - `start --daemon`: fork current process with `child_process.fork` or `spawn(process.execPath, process.argv.slice(1).filter(a => a !== '--daemon'), { detached: true, stdio: 'ignore' })`. Write child PID to PID_FILE. Parent exits.
  - `daemon.ts`: after each state transition, write `{ state, sessionId, profile, updatedAt }` to STATE_FILE (async, fire-and-forget)
  - `stop` command: read PID_FILE, `process.kill(pid, 'SIGTERM')`, delete PID_FILE
  - `status` command: read STATE_FILE, print state, sessionId, profile, updatedAt. If file missing → "daemon not running".
  - Graceful shutdown in daemon: register `SIGTERM` handler → `daemon.stop()` → delete PID_FILE and STATE_FILE → `process.exit(0)`
- **Context:** `src/cli.ts`, `src/daemon.ts`, CLAUDE.md (daemon.pid, daemon-state.json)
- **Verify:** `npx tsx src/cli.ts start --daemon` forks and exits. `~/.openclaw/assistant/daemon.pid` exists. `npx tsx src/cli.ts status` shows IDLE. `npx tsx src/cli.ts stop` terminates daemon.

### Task 17: .npmignore + README update + publish prep

- **Files:** `.npmignore` (create), `README.md` (edit)
- **Depends on:** Task 16
- **Scope:** S
- **What:** Create `.npmignore` to exclude dev artifacts from npm publish. Update README.md with current requirements and CLI commands.
- **How:**
  - `.npmignore`: exclude `docs/`, `test/`, `src/`, `.claude/`, `*.md` (but keep `README.md` — use negation `!README.md`), `tsconfig.json`, `vitest.config.ts`, `.gitignore`. Include: `dist/`, `sounds/`, `listener.py`, `package.json`, `README.md`.
  - `README.md`: update Requirements section if any dependencies changed. Verify CLI section matches implemented commands. Add "Development" section with `npm run build`, `npm test`, `npm run dev`.
  - Run `npm pack --dry-run` to verify only publish-relevant files are included.
- **Context:** `package.json` (bin field points to dist/cli.js), `README.md` (existing content)
- **Verify:** `npm pack --dry-run` output includes `dist/cli.js`, `sounds/*.wav`, `listener.py`. Excludes `src/`, `test/`, `docs/`. `README.md` lists all implemented CLI commands.

### Task 18: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run full test suite and build to verify everything compiles and tests pass.
- **Context:** `package.json` (scripts: build, test), `test/*.test.ts` (all test files), `src/types.ts` (shared interfaces — common source of compile errors)
- **Verify:** `npm run build && npm test` — zero compile errors, all tests green (config, session, daemon-state, transcriber, agent tests).

## File intersection matrix

| File | Tasks that touch it |
|---|---|
| `src/types.ts` | Task 2, Task 11 (adds START_RECORD to DaemonEvent) |
| `src/cli.ts` | Task 4, Task 13, Task 14 (bootstrap wire-up), Task 16 |
| `src/daemon.ts` | Task 12a, Task 12b, Task 16 |
| `listener.py` | Task 6, Task 15 |

Tasks 2 and 11 both touch `src/types.ts` — Task 11 adds `START_RECORD` to the event union. Sequential dependency enforced.

Tasks 4, 13, 14, 16 all touch `src/cli.ts` — each builds on the previous. Fully sequential.

Tasks 12a, 12b, and 16 all touch `src/daemon.ts`. Sequential.

Tasks 6 and 15 both touch `listener.py`. Sequential.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 18 tasks, complex project, clear independent groups in middle phases (recorder, transcriber, agent, speaker have no shared files), sequential chains enforced only where files intersect.
- **Order:**
  ```
  Group 1 (sequential):
    Task 1: Scaffold → Task 2: types.ts
  ─── barrier ───
  Group 2 (parallel):
    Task 3: config.ts + session.ts + tests
    Task 5: sounds.ts + WAV files
    Task 6: listener.py stub + listener.ts
    Task 7: recorder.ts
    Task 8: transcriber.ts + test
    Task 9: agent.ts + test
    Task 10: speaker.ts
    Task 11: daemon-state.ts + test
  ─── barrier ───
  Group 3 (sequential):
    Task 4: cli.ts session/profile commands (depends on Task 3)
  ─── barrier ───
  Group 4 (sequential):
    Task 12a: daemon.ts IDLE→RECORDING cycle (depends on Tasks 3,5,6,7,8,11)
    Task 12b: daemon.ts PROCESSING→SPEAKING pipeline (depends on Task 12a,9,10)
  ─── barrier ───
  Group 5 (sequential):
    Task 13: cli.ts start/stop/status (depends on Task 12b)
  ─── barrier ───
  Group 6 (parallel):
    Task 14: bootstrap.ts
    Task 15: listener.py real openWakeWord (depends on Task 6)
  ─── barrier ───
  Group 7 (sequential):
    Task 16: daemon mode + PID file (depends on Task 13)
    Task 17: .npmignore
    Task 18: Validation
  ```

## Verification

- `npm run build` — compiles without errors
- `npm test` — all unit tests green (state machine, config, session, transcriber, agent)
