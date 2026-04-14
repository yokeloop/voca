# VOCA

**Voice Operated Claw Assistant**

> From Latin *voca* — "call, summon, invoke." VOCA calls OpenClaw with your voice.

A Node.js CLI daemon that provides a hands-free voice interface to [OpenClaw](https://github.com/yokeloop/openclaw). Listens for a wake word, records speech, transcribes it, sends the transcript to an OpenClaw agent, and speaks the response aloud.

## How It Works

```
You speak → wake word detected → recording → transcription → OpenClaw agent → TTS → you hear the answer
```

**State machine:** `IDLE → LISTENING → RECORDING → PROCESSING → SPEAKING → IDLE`

## Requirements

- Linux (ARM64 or x64) with a microphone
- Node.js 20+
- Python 3.11+
- OpenClaw running on the same machine (gateway on port 18789)
- `arecord` / `aplay` (ALSA utils)
- `whisper-stt-wrapper` (Russian STT)

## Quick Start

```bash
npm install -g @yokeloop/voca

voca bootstrap    # interactive setup: mic, speaker, dependencies
voca start        # launch the daemon
```

## CLI

```bash
voca bootstrap              # Interactive setup: mic, speaker, dependencies
voca start [--daemon]       # Start the voice assistant (foreground or daemon)
voca stop                   # Stop the daemon
voca status                 # Show daemon status

voca session new            # Create new session
voca session info           # Show session info (id, profile, message count)

voca profile list           # List available profiles
voca profile use <id>       # Switch active profile (resets session)
```

## Storage layout and migration

VOCA keeps its runtime data (config, session, Piper binary and voices, wake-word models, Python venv, sounds) under a single root directory. The default is `~/.voca`.

**Discovery order** (first match wins):

1. `VOCA_HOME` environment variable
2. Pointer file at `~/.config/voca/root` (written by `voca bootstrap`)
3. If neither is set, VOCA exits with an error asking you to run `voca bootstrap`

**Override the root:**

```bash
export VOCA_HOME=/mnt/data/voca
voca start
```

**Migrating from an older install** (data previously under `~/.openclaw/assistant/`):

- Option A — re-bootstrap from scratch: `rm -rf ~/.openclaw/assistant` and run `voca bootstrap` again.
- Option B — move files manually: `mv ~/.openclaw/assistant ~/.voca` and point VOCA at the new location by either running `voca bootstrap` (accept the default) or writing the path yourself to `~/.config/voca/root`.

## Development

```bash
npm run build               # Compile TypeScript to dist/
npm test                    # Run unit tests
npm run dev                 # Run in development mode
```

## License

MIT
