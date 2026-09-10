# VOCA

**VOCA v3** — thin, customizable local voice assistant with asynchronous task delegation.

> From Latin *voca* — “call, summon, invoke.”

**Docs-only:** this repository specifies the v3 rewrite; it does not yet contain an implemented voice runtime. Host performance, browser interruption and deployment prerequisites remain verification gates, not proven capabilities.

## Architecture

```text
Browser ─ WebRTC audio (FastRTC) + control WebSocket ─ one Python/FastAPI backend
                                                       ├── conversation runtime / custom LLM loop
                                                       ├── STT: local Whisper server
                                                       ├── LLM: local LiteLLM → llama.cpp / LFM2.5
                                                       ├── TTS: local audio.cpp via TTSProvider
                                                       ├── delegation: selected Pi instance first
                                                       ├── state: SQLite; JSONL export
                                                       └── tracing: local self-hosted LangSmith
```

- A button toggles the conversation on/off; no holding required. VAD separates utterances. Browser AEC is requested, not assumed to eliminate echo.
- Barge-in cancels the current response and clears backend **and browser** playback, but leaves delegated tasks running. FastRTC flush is an early gate, not an already proven callback.
- Only user-facing assistant content reaches TTS, never reasoning, tool payloads or RPC events. The serving layer normalizes tool calls; the loop validates them before dispatch.
- Pi is the first adapter: a user-selected instance and directory, a separate new session inheriting that instance’s configuration and restrictions. Results persist through disconnects and are delivered to the original session without duplicate dispatch; reconnect never enables the microphone automatically.
- Instructions are snapshotted in SOUL → user → agent order for each turn; reload affects the next turn.
- Inference endpoints stay local, with no automatic cloud fallback. MiniCPM and unified audio.cpp STT/TTS are experiments, not prerequisites.
- Self-hosted LangSmith on the same host is mandatory from the first voice stage. Enterprise entitlement/artifact access and resource fit remain blockers: published recommendations exceed the target host’s approximately 32 GB RAM. No SaaS substitution.

## Roadmap

1. **Voice conversation** — minimal UI, local voice chain, reliable interruption, persistence and full available local tracing.
2. **Context and instructions** — reconnect history, JSONL export, managed snapshot/reload.
3. **Pi delegation** — durable asynchronous tasks, task cards and late results.
4. **Further capabilities** — separate Hermes/OpenClaw adapters, richer UI and gated semantic/model experiments.

See the [roadmap](docs/roadmap.md) for boundaries and acceptance. Approximately 500 ms from speech end to first audible response is a measurable target, not a guarantee or a completed benchmark.

## Docs

- [Architecture](docs/architecture.md) — owners, providers, conversation/control/task contracts, sources and verification protocols
- [Roadmap](docs/roadmap.md) — vertical stages and gates
- [Glossary](CONTEXT.md) — shared terms
- [Index and historical grill](docs/index.md) — provenance and current navigation
- [ADR-0001](docs/adr/0001-rewrite-v3.md) — historical rewrite decision, 2026-09-02
- [ADR-0002](docs/adr/0002-conversation-lifecycle-and-roadmap.md) — VOCA-2 lifecycle and roadmap decisions
- [MiniCPM research](docs/research/minicpm5-2b.md) — source evidence and future comparison, not a model selection
