# SP Context: voca

## Stack

- Languages: TypeScript, JavaScript, Python
- Frameworks: Commander CLI, openWakeWord, Whisper STT, Piper TTS, OpenClaw
- Package manager: npm
- Runtime: Node.js 20+

## Commands

- Dev: NOT_FOUND
- Build: NOT_FOUND
- Test: NOT_FOUND
- Lint: NOT_FOUND
- Format: NOT_FOUND
- Typecheck: NOT_FOUND

## Architecture

- Pattern: flat (стадия спецификации, код не написан)
- Key dirs: docs/ (документация), src/ (планируется — исходный код)
- Entry points: src/cli.ts (CLI), src/daemon.ts (daemon), listener.py (openWakeWord)
- Layers: cli (src/cli.ts), core (src/daemon.ts), io-adapters (listener, recorder, transcriber, agent, speaker), infra (config, session, bootstrap, sounds)

## Conventions

- Naming: camelCase
- File naming: camelCase
- Import style: ES modules
