# Voice Interface Daemon for OpenClaw on RPi5

**Slug:** claw-assistant-voice-daemon
**Тикет:** —
**Сложность:** complex
**Тип:** general

## Task

Build a Node.js CLI daemon `claw-assistant` in a separate repository (`~/claw-assistant`). The daemon listens continuously for a wake word, records speech, transcribes it with whisper-stt-wrapper, sends the transcript to OpenClaw agent, and speaks the response through Piper TTS.

## Context

### Архитектура области

Build a new project from scratch and integrate it with existing RPi5 components:

```
                    claw-assistant (Node.js daemon)
                    ┌──────────────────────────────────┐
                    │                                  │
Mic (USB) ──arecord─┤  State Machine:                  │
                    │  IDLE → LISTENING → RECORDING    │
                    │  → PROCESSING → SPEAKING → IDLE  │
                    │                                  │
                    │  listener.py (openWakeWord)       │
                    │       ↕ JSON lines на stdout     │
                    │  recorder (arecord child proc)    │
                    │  transcriber (whisper-stt-wrapper)│
                    │  agent (openclaw agent CLI)       │
                    │  speaker (piper | aplay pipe)     │
                    └──────────┬───────────────────────┘
                               │
         openclaw agent --agent personal \
           --session-id asst-<ts> \
           --message "<text>" --json
                               │
                    OpenClaw Gateway :18789
```

**openclaw agent CLI** (already installed at `/home/priney/.npm-global/bin/openclaw`):
- Flags: `--agent <id>`, `--session-id <id>`, `--message <text>`, `--json`
- Agents: `personal` (default), `public`
- Default timeout: 600s

**whisper-stt-wrapper** (already installed at `/usr/local/bin/whisper-stt-wrapper`):
- Call: `whisper-stt-wrapper --language ru <file.wav>`
- Returns transcribed text on stdout

**piper** (not installed — bootstrap installs it):
- Call: `echo "<text>" | piper --model ru_RU-irina-medium --output_raw | aplay -r 22050 -f S16_LE -c 1`
- Pipes directly without intermediate files, uses streaming

**openWakeWord** (Python, TFLite):
- Runs as long-lived child process `listener.py`
- Communicates via JSON lines: `{"event":"wake"}`, `{"event":"stop"}`
- Models: `hey_jarvis` (wake), `stop` (stop phrase)
- Pauses during SPEAKING state to prevent self-triggering

**arecord/aplay** (already installed at `/usr/bin/`):
- arecord records PCM to a temporary WAV file
- aplay plays the TTS output

### Files to Create

Create the following directory structure:

```
~/claw-assistant/
├── package.json              # bin: { "claw-assistant": "./dist/cli.js" }
├── tsconfig.json
├── src/
│   ├── cli.ts                # Entry point (commander), subcommands
│   ├── daemon.ts             # State machine, main loop
│   ├── listener.ts           # Spawn/manage openWakeWord process
│   ├── recorder.ts           # arecord child process, WAV output
│   ├── transcriber.ts        # whisper-stt-wrapper invocation
│   ├── agent.ts              # openclaw agent CLI invocation
│   ├── speaker.ts            # piper | aplay pipe
│   ├── session.ts            # Session/profile management
│   ├── config.ts             # Config read/write ~/.openclaw/assistant/config.json
│   ├── sounds.ts             # Sound indicators (beeps)
│   └── bootstrap.ts          # Interactive setup
├── listener.py               # openWakeWord Python script
└── sounds/                   # Default sound samples
    ├── wake.wav
    ├── stop.wav
    └── error.wav
```

Store runtime data in `~/.openclaw/assistant/`:
```
~/.openclaw/assistant/
├── config.json
├── session.json
├── sounds/                   # Copied during bootstrap
├── models/                   # Wake word .onnx models
├── venv/                     # Python venv for openWakeWord
└── bin/                      # piper binary + models (if bootstrap installs)
```

### Patterns to Follow

No existing codebase exists. Patterns come from the specification:
- Each component lives as a separate module with child process management
- Modules communicate via EventEmitter or callbacks
- openWakeWord uses a JSON lines protocol on stdout
- Spawn all external commands as child processes using `spawn` or `execFile`

### Tests

Create tests from scratch. Cover:
- State machine transitions
- Config read/write
- Session management
- Parsing JSON lines from listener.py
- Correct invocation of openclaw agent CLI (mock child process)

## Requirements

1. Implement state machine: IDLE → LISTENING → RECORDING → PROCESSING → SPEAKING → IDLE
2. Create `listener.py` — a Python script for openWakeWord that emits JSON lines `{"event":"wake"}` / `{"event":"stop"}` on stdout. Listen to both models simultaneously. Parse stdout line by line in Node.js.
3. Recording: arecord writes to a temporary WAV file. Cancel if silence exceeds 30s or total time exceeds 2min (do not send).
4. Transcription: `whisper-stt-wrapper --language ru <file.wav>`. Trim "stop" from the end if present.
5. Agent: `openclaw agent --agent <profile> --session-id <id> --message "<text>" --json`. Use 900s timeout. Speak an error message if the agent fails.
6. TTS: `echo "<response>" | piper --model <model> --output_raw | aplay -r 22050 -f S16_LE -c 1`. Pipe without intermediate files. Pause 500ms after playback completes, then resume openWakeWord.
7. Sound indicators: beep for wake word, double beep for stop phrase, low tone for error.
8. CLI via commander: `bootstrap`, `start [--daemon]`, `stop`, `status`, `session new/info`, `profile list/use <id>`.
9. Config file `~/.openclaw/assistant/config.json` stores: inputDevice, outputDevice, profile, sessionId, wakeWord, stopWord, piper model, timeouts.
10. Session file `~/.openclaw/assistant/session.json` stores: sessionId with format `asst-<unix-ts>`, messageCount, profile, createdAt. Changing the profile resets the session.
11. Bootstrap: interactive setup for mic, speaker, profile, wake/stop words. Install dependencies (piper, openwakeword venv, Python deps, models) if missing, requesting confirmation before each installation.
12. Use npm link to make the `claw-assistant` command globally available.
13. Create GitHub repository using `gh repo create`.

## Constraints

- Do not modify openclaw — integrate only via the `openclaw agent` CLI
- Store runtime data (`config.json`, `session.json`, `sounds/`, `models/`, `venv/`) in `~/.openclaw/assistant/`, not in the repository
- Keep source code in `~/claw-assistant/` separate from runtime data
- Keep Node.js dependencies minimal: `commander`, `tsx` (dev). No express, no socket.io, no unnecessary packages.
- Use Python venv in `~/.openclaw/assistant/venv/` instead of system pip
- Do not use intermediate files for TTS — pipe piper directly to aplay
- Do not terminate listener.py between iterations — keep it as a long-lived process, pause and resume it

## Verification

- `claw-assistant bootstrap` — interactively configures mic and speaker, installs piper if missing, creates venv with openWakeWord
- `claw-assistant start` — daemon starts, spawns openWakeWord process, enters IDLE state
- Say "hey jarvis" — produces beep, transitions to RECORDING state
- Say a phrase + "stop" — produces double beep, transitions to PROCESSING state, transcribes the recording, sends it to openclaw agent, speaks the response
- `claw-assistant status` — displays current state machine status
- `claw-assistant session new` — creates new sessionId, resets message counter
- `claw-assistant profile use public` — switches profile, resets session
- Silence exceeding 30s during recording — cancels recording, returns to IDLE without sending
- Connection error to OpenClaw gateway — produces low tone, speaks "Server nedostupen"
- `npm test` — unit tests pass for state machine, config, and session
- Ctrl+C during `claw-assistant start` — daemon cleanly terminates all child processes

## Материалы

- `docs/superpowers/specs/2026-04-11-claw-assistant-design.md` — полная спека проекта
- `openclaw agent --help` — интерфейс CLI для интеграции
- `whisper-stt-wrapper --help` — интерфейс STT
