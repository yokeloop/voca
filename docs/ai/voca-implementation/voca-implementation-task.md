# VOCA — Voice Assistant Implementation

**Slug:** voca-implementation
**Ticket:** —
**Complexity:** complex
**Type:** general

## Task

Implement Node.js CLI daemon `voca` incrementally — from project setup to a full voice cycle. Each phase delivers a working result with verification. Seven phases: setup → sounds+listener → recorder+transcriber → agent+speaker → state machine → bootstrap → daemon mode.

## Context

### Architecture

Daemon listens for a wake word, records speech, transcribes it, sends to OpenClaw agent, speaks the response.

```
Mic (USB plughw:2,0)
  │
  ├── listener.py (openWakeWord + PyAudio) ── JSON lines stdout
  │     {"event":"wake"} / {"event":"stop"}
  │
  └── sox rec (WAV recording with silence detection)
        │
        ▼
  daemon.ts (state machine)
    IDLE → LISTENING → RECORDING → PROCESSING → SPEAKING → IDLE
        │
        ├── transcriber.ts → whisper-stt-wrapper <file> --language ru
        ├── agent.ts → openclaw agent --agent <profile> --session-id <id> --message "<text>" --json
        └── speaker.ts → echo "<text>" | piper --model <m> --output_raw | aplay -D plughw:2,0 ...
```

Daemon stores runtime data in `~/.openclaw/assistant/` (config.json, session.json, sounds/, models/, venv/, bin/), outside the repository.

### External Tools (verified)

| Tool | Path | Version | Status |
|---|---|---|---|
| Node.js | `/usr/bin/node` | v24.13.1 | OK |
| npm | — | 11.8.0 | OK |
| Python 3 | `/usr/bin/python3` | 3.13.5 | OK (externally-managed, needs venv) |
| openclaw | `/home/priney/.npm-global/bin/openclaw` | 2026.3.8 | OK |
| whisper-stt | `/usr/local/bin/whisper-stt-wrapper` | faster-whisper 1.2.1 | OK |
| sox | `/usr/bin/sox` | — | OK (recording + silence + sound generation) |
| aplay | `/usr/bin/aplay` | — | OK |
| piper | — | — | NOT installed (bootstrap) |
| openWakeWord | — | — | NOT installed (bootstrap) |

**Critical differences from original spec:**
- whisper-stt: file as FIRST argument — `whisper-stt-wrapper <file.wav> --language ru`
- Recording via `sox rec` instead of `arecord` — built-in silence detection
- Audio device: HyperX Cloud Flight Wireless = `plughw:2,0` (mic and speakers)

### Files to Create

```
~/voca/
├── package.json              # @yokeloop/voca, bin: { "voca": "./dist/cli.js" }
├── tsconfig.json             # target: ES2022, module: NodeNext, strict
├── vitest.config.ts
├── src/
│   ├── types.ts              # VocaConfig, VocaSession, DaemonState, all interfaces
│   ├── cli.ts                # Commander entry point
│   ├── daemon.ts             # State machine + I/O orchestration
│   ├── daemon-state.ts       # Pure state machine (no I/O, testable)
│   ├── listener.ts           # Spawn/manage openWakeWord process
│   ├── recorder.ts           # sox rec child process, WAV + silence detection
│   ├── transcriber.ts        # whisper-stt-wrapper invocation
│   ├── agent.ts              # openclaw agent CLI invocation
│   ├── speaker.ts            # piper | aplay pipe
│   ├── session.ts            # Session/profile management
│   ├── config.ts             # Config read/write ~/.openclaw/assistant/config.json
│   ├── sounds.ts             # Sound indicators via aplay
│   └── bootstrap.ts          # Interactive setup + dependency installation
├── listener.py               # openWakeWord Python script (PyAudio)
├── sounds/                   # Generated via sox during bootstrap
│   ├── wake.wav
│   ├── stop.wav
│   └── error.wav
└── test/
    ├── config.test.ts
    ├── session.test.ts
    ├── daemon-state.test.ts
    ├── transcriber.test.ts
    └── agent.test.ts
```

### Patterns to Follow

Project starts from scratch — no code to reuse. Reference points:
- whisper-stt venv (`/home/priney/whisper-stt/.venv/`) — template for creating openWakeWord venv
- openclaw config (`~/.openclaw/openclaw.json`) — stores timeout 900s, agents personal/public
- Each module: separate file, child process via `spawn`/`execFile`, EventEmitter for events

### Tests

Create tests from scratch. Framework: vitest (devDependency).

Coverage:
- `daemon-state.ts` — all state machine transitions
- `config.ts` — read/write/default
- `session.ts` — new/increment/reset on profile change
- `transcriber.ts` — stdout parsing, trimming "stop"
- `agent.ts` — JSON response parsing, error handling (mock child process)

## Requirements

### Phase 1 — Project setup + Config + Session CLI

1. Create `package.json` with `"name": "@yokeloop/voca"`, `"type": "module"`, `"bin": { "voca": "./dist/cli.js" }`, scripts: build (tsc), dev (tsx src/cli.ts), test (vitest).
2. Create `tsconfig.json`: target ES2022, module NodeNext, moduleResolution NodeNext, strict true, outDir dist.
3. Implement `src/config.ts` — readConfig, writeConfig, ensureConfigDir, defaultConfig. Path: `~/.openclaw/assistant/config.json`.
4. Implement `src/session.ts` — readSession, writeSession, newSession, generateSessionId (`asst-<unix-ts>`), incrementMessageCount. Changing profile resets session.
5. Implement `src/cli.ts` via commander: `session new`, `session info`, `profile list`, `profile use <id>`.
6. Write tests for config and session.

**Verification:** `npm run build` compiles. `npx tsx src/cli.ts session new` creates a session. `npx tsx src/cli.ts profile use public` switches profile and resets session. `npm test` passes.

### Phase 2 — Sounds + Listener skeleton

7. Implement `src/sounds.ts` — playSound(type, opts) via `aplay -D <device> <wav-file>`.
8. Generate sound files via sox: wake.wav (880Hz 0.1s), stop.wav (880Hz 0.1s x2), error.wav (220Hz 0.3s).
9. Implement `src/listener.ts` — spawnListener returns ListenerHandle with on('wake'/'stop'), pause() (SIGSTOP), resume() (SIGCONT), kill().
10. Create `listener.py` skeleton: reads stdin, on input "wake" → `{"event":"wake"}`, on "stop" → `{"event":"stop"}`. For testing without real models.

**Verification:** `npx tsx -e "import('./src/sounds.js').then(m => m.playSound('wake', {...}))"` — beep is audible. listener.py in stdin mode emits JSON lines.

### Phase 3 — Recorder + Transcriber

11. Implement `src/recorder.ts` — startRecording via `sox rec`. Sox automatically stops recording on silence >30s (silence detection). Maximum duration 2 min. Returns RecorderHandle: filePath, stop(), cancel().
12. Implement `src/transcriber.ts` — transcribe(filePath, opts) via `execFile(whisperBin, [filePath, '--language', language])`. Trim trailing "stop" from text.
13. Write tests for transcriber (mock execFile, verify trimming).

**Verification:** 3s recording → WAV file created. `transcribe('/tmp/test.wav', {language: 'ru'})` returns text.

### Phase 4 — Agent + Speaker

14. Implement `src/agent.ts` — queryAgent(opts) via `execFile(openclaw, ['agent', '--agent', agentId, '--session-id', sessionId, '--message', message, '--json', '--timeout', String(timeoutS)])`. Parse JSON response. Timeout: 900s. On error — throw AgentError.
15. Implement `src/speaker.ts` — speak(opts) via pipe: `echo text → piper --model M --output_raw → aplay -D device -r 22050 -f S16_LE -c 1`. No intermediate files. await aplay completion. 500ms pause after.
16. Write test for agent (mock child process, verify parsing).

**Verification:** `queryAgent({...message: 'hello'...})` returns response from OpenClaw. `speak({text: 'Hello world', ...})` speaks text (requires installed piper).

### Phase 5 — State Machine

17. Implement `src/daemon-state.ts` — pure function transition(state, event) → newState. Types: State = IDLE|LISTENING|RECORDING|PROCESSING|SPEAKING. Events: WAKE, STOP, RECORD_CANCEL, PROCESSING_DONE, SPEAKING_DONE, ERROR.
18. Implement `src/daemon.ts` — class VocaDaemon extends EventEmitter. Method start() spawns listener, enters IDLE. Method stop() — graceful shutdown. Orchestrates all modules via state machine.
19. Write daemon-state tests: all valid transitions, rejection of invalid ones.

**Verification:** `npm test` — state machine tests green. `npx tsx src/cli.ts start` — daemon runs with listener.py in stdin mode. Input "wake" → beep → recording. Input "stop" → double-beep → transcription → agent → TTS.

### Phase 6 — Bootstrap + full listener.py

20. Implement `src/bootstrap.ts` — interactive setup: mic selection (from `sox --help` or `arecord -l`), speaker, profile, wake/stop words. Install piper (download aarch64 binary + ru_RU-irina-medium model). Create venv + `pip install openwakeword pyaudio`. Download ONNX models (hey_jarvis, stop). Copy sounds/. Confirm each installation.
21. Implement `listener.py` — real openWakeWord: load models, capture audio via PyAudio, inference, emit JSON lines. Both models (wake + stop) run simultaneously.

**Verification:** `npx tsx src/cli.ts bootstrap` — installs piper, venv, openWakeWord, models. `npx tsx src/cli.ts start` — say "hey jarvis" → wake word detection triggers.

### Phase 7 — Daemon mode + full CLI + publishing

22. Add `start --daemon` — fork process, write PID to `~/.openclaw/assistant/daemon.pid`, status to `~/.openclaw/assistant/daemon-state.json`.
23. Implement `stop` — reads PID file, sends SIGTERM. `status` — reads daemon-state.json.
24. Graceful shutdown on SIGINT/SIGTERM: kill all child processes (listener.py, sox, piper, aplay, openclaw).
25. Prepare for publishing: `.npmignore`, update README.md.

**Verification:** full voice cycle — `voca start --daemon` → "hey jarvis" → beep → phrase → "stop" → double-beep → response spoken. `voca status` shows state. `voca stop` terminates daemon. `npm test` — all tests green. Ctrl+C terminates process cleanly.

## Constraints

- Do not modify openclaw — integrate only via `openclaw agent` CLI
- Runtime data (`config.json`, `session.json`, `sounds/`, `models/`, `venv/`) — in `~/.openclaw/assistant/`, not in the repository
- Dependencies minimal: `commander` (runtime), `tsx` + `vitest` + `typescript` (dev). No express, socket.io
- Python venv for openWakeWord — in `~/.openclaw/assistant/venv/`, not system pip
- TTS without intermediate files — piper pipes to aplay
- listener.py — long-lived process, SIGSTOP/SIGCONT for pausing (not restart)
- Recording via sox rec (not arecord) — built-in silence detection
- Sound files generated via sox (not downloaded)
- whisper-stt: file as first argument — `whisper-stt-wrapper <file.wav> --language ru`

## Verification

- `npm run build` — compiles without errors
- `npm test` — all unit tests green (state machine, config, session, transcriber, agent)
- `voca bootstrap` — interactively configures mic/speaker, installs piper, creates venv with openWakeWord
- `voca start` — daemon starts, listener.py spawns, state is IDLE
- Say "hey jarvis" → beep, transition to RECORDING
- Say phrase + "stop" → double-beep, transcription, send to openclaw agent, speak response
- `voca status` — current state, sessionId, profile
- `voca session new` — new sessionId, counter reset
- `voca profile use public` — profile switched, session reset
- Silence >30s during recording — cancel recording, return to IDLE
- openclaw gateway error — low tone, speak "Server unavailable"
- Ctrl+C during `voca start` — clean termination of all child processes

## Materials

- `docs/ai/claw-assistant-voice-daemon/claw-assistant-voice-daemon-task.md` — full project specification
- `openclaw agent --help` — CLI interface
- `whisper-stt-wrapper --help` — STT interface (file as first argument)
- `/home/priney/.openclaw/openclaw.json` — openclaw configuration (timeout 900s, agents)
- `/home/priney/whisper-stt/.venv/` — Python venv reference
