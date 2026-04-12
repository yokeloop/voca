# CLAUDE.md

## Project

VOCA (Voice Operated Claw Assistant) — a Node.js CLI daemon for voice control of [OpenClaw](https://github.com/yokeloop/openclaw). Listens for a wake word, records speech, transcribes via Whisper, sends to OpenClaw agent, speaks the response through Piper TTS. Written in TypeScript, published as `@yokeloop/voca`.

## Architecture

```
src/
  cli.ts          # Entry point (commander) — bootstrap/start/stop/status/session/profile commands
  daemon.ts       # State machine: IDLE → LISTENING → RECORDING → PROCESSING → SPEAKING → IDLE
  listener.ts     # Spawn and manage listener.py (openWakeWord child process)
  recorder.ts     # sox rec child process, records to temporary WAV with silence detection
  transcriber.ts  # Invokes whisper-stt
  agent.ts        # Invokes openclaw agent CLI
  speaker.ts      # Pipes piper | aplay (no intermediate files)
  session.ts      # Session and profile management
  config.ts       # Reads/writes ~/.openclaw/assistant/config.json
  sounds.ts       # Sound indicators (beep/double-beep/error tone)
  bootstrap.ts    # Interactive setup: mic, speaker, dependencies
listener.py       # Python openWakeWord script — emits JSON lines on stdout
sounds/           # Default sounds: wake.wav, stop.wav, error.wav
```

**Data flow:** Mic → `listener.py` (JSON lines) → `daemon.ts` state machine → `recorder.ts` (WAV) → `transcriber.ts` (text) → `agent.ts` (OpenClaw :18789) → `speaker.ts` (piper | aplay)

**Runtime data** stored in `~/.openclaw/assistant/` (not in the repository):
```
~/.openclaw/assistant/
  config.json     # inputDevice, outputDevice, profile, wakeWord, stopWord, piper model
  session.json    # sessionId (asst-<unix-ts>), messageCount, profile, createdAt
  sounds/         # Copied during bootstrap
  models/         # Wake word .onnx models
  venv/           # Python venv for openWakeWord
  bin/            # piper binary + models
```

## Commands

Project does not have `package.json` yet. After creation:

```bash
npm install                          # install dependencies
npm run build                        # tsc → dist/
npm test                             # unit tests (state machine, config, session)
npm run lint                         # eslint + prettier (to be configured after npm init)
```

CLI after global installation:

```bash
npm publish --access public          # publish @yokeloop/voca to npm registry
npm install -g @yokeloop/voca

voca bootstrap                       # interactive setup for mic/speaker/dependencies
voca start                           # start daemon (foreground)
voca start --daemon                  # start as background process
voca stop                            # stop daemon
voca status                          # current state: IDLE/LISTENING/RECORDING/PROCESSING/SPEAKING

voca session new                     # new sessionId, reset message counter
voca session info                    # sessionId, profile, message count

voca profile list                    # list profiles: personal, public
voca profile use personal            # switch profile (resets session)
```

Integration tools (already installed):

```bash
openclaw agent --agent personal --session-id asst-<ts> --message "<text>" --json
whisper-stt-wrapper <file.wav> --language ru
echo "<text>" | piper --model ru_RU-irina-medium --output_raw | aplay -r 22050 -f S16_LE -c 1
# Note: piper is not installed globally — installed to ~/.openclaw/assistant/bin/ via `voca bootstrap`
```

## Conventions

- TypeScript, Node.js 20+, ES modules
- Naming: camelCase for variables/functions, PascalCase for classes
- Files: camelCase (`cli.ts`, `daemon.ts`, `listener.ts`)
- Dependencies minimal: only `commander`, `tsx` (dev) — no express, socket.io, etc.
- Commits: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`)
- **All .md files must be written in English.** No Russian or other languages in generated markdown content.

## Non-obvious

- **listener.py is not restarted between iterations** — the process lives for the entire daemon lifetime. During SPEAKING state it is paused (SIGSTOP/SIGCONT) to prevent self-triggering on its own voice. Uses the openwakeword 0.4.0 API (`wakeword_model_paths=`, no `inference_framework`). The stop model is optional — loaded only if its `.onnx` file exists in `~/.openclaw/assistant/models/`.

- **OpenClaw integrates only via CLI**, not through the API directly. Binary path: `/home/priney/.npm-global/bin/openclaw`. The `--json` flag returns the response as JSON. Timeout is 900s (not the default 600s) — the agent may take long to think.

- **TTS without intermediate files** — `piper` pipes directly to `aplay` (`--output_raw | aplay -r 22050 -f S16_LE -c 1`). After playback ends — 500ms pause before resuming openWakeWord.

- **Recording cancels** without sending to the agent if silence >30s or duration >2min — returns to IDLE. Trailing "stop" is trimmed from the transcript before sending.

- **Changing profile resets session** — `voca profile use public` creates a new `sessionId`. Session format: `asst-<unix-ts>`.

- **Bootstrap installs dependencies step by step** — piper (if missing), Python venv in `~/.openclaw/assistant/venv/`, portaudio19-dev (checked via `dpkg -s` and installed via `apt-get` if missing), openWakeWord + pyaudio, wake word ONNX model. The wake model (`hey_jarvis_v0.1.onnx`) is copied from the installed openwakeword pip package in the venv; if not found there, it falls back to downloading from GitHub. No stop model is downloaded. Confirmation is requested before each installation.

- **Sound indicators**: single beep — wake word, double beep — stop phrase, low tone — error. Default files in `sounds/`, copied to `~/.openclaw/assistant/sounds/` during bootstrap.
