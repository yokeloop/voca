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

```
voca bootstrap          # Interactive setup: mic, speaker, profile, wake/stop words
voca start              # Start daemon in foreground
voca start --daemon     # Start as background process
voca stop               # Stop daemon
voca status             # Current state: IDLE/LISTENING/RECORDING/PROCESSING/SPEAKING

voca session new        # New session
voca session info       # Current session-id, profile, message count

voca profile list       # List profiles (personal, public)
voca profile use <id>   # Switch active profile
```

## License

MIT
