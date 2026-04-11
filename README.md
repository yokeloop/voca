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

## Development

```bash
npm run build               # Compile TypeScript to dist/
npm test                    # Run unit tests
npm run dev                 # Run in development mode
```

## License

MIT
