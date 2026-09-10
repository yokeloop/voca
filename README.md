# VOCA

**VOCA v3** — thin, customizable realtime voice agent with task delegation to stronger agents.

> From Latin *voca* — "call, summon, invoke."

Clean rewrite of voice-agent-v2. See [docs/architecture.md](docs/architecture.md) for the full architecture spec and [docs/index.md](docs/index.md) for the grill decision summary (2026-09-02).

## Stack

```
Browser ─ WebRTC (FastRTC) + WS control ─ Python backend
                                           ├── STT: Whisper server (OpenAI-compatible API)
                                           ├── LLM: LiteLLM proxy → LFM-2.5 (tracing via LangSmith)
                                           ├── TTS: audio.cpp server (TTSProvider abstraction)
                                           ├── Delegation: delegate(agent, task) → Hermes/Pi/OpenClaw
                                           └── Sessions: SQLite (+ JSONL export)
```

- **LLM loop**: custom (not a framework) — text = speak, tool call = act, interim replies possible
- **Transport**: FastRTC (WebRTC, VAD, browser AEC, barge-in detection)
- **Activation (Phase 1)**: push-to-talk; always-on semantic activation is Phase 5 R&D
- **Instructions**: SOUL.md / user.md / agent.md with hot-reload

## Phases

1. **MVP pipeline** — voice conversation works end-to-end
2. **Agent capabilities** — tool calling + delegation gateway
3. **Realtime polish** — barge-in TTS cancel, semantic turn detection
4. **UI** — new from scratch, after the backend
5. **R&D** — always-on activation, LFM-2.5 fine-tuning, model experiments

## Docs

- [CONTEXT.md](CONTEXT.md) — domain glossary
- [docs/architecture.md](docs/architecture.md) — architecture spec
- [docs/index.md](docs/index.md) — grill results & decision summary
- [docs/adr/0001-rewrite-v3.md](docs/adr/0001-rewrite-v3.md) — rewrite decision ADR