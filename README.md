# VOCA

**VOCA v3** — thin, customizable local voice assistant with asynchronous task delegation.

> From Latin *voca* — “call, summon, invoke.”

**Session foundation implemented:** VOCA-13 provides a local diagnostic session panel, durable SQLite session IDs and snapshot-only control recovery. Voice, models, microphone/media, provider integrations and their host/browser gates remain future work, not proven capabilities.

## Run the diagnostic session panel

See [code architecture](docs/code-architecture.md), [testing](docs/testing.md), and [diagnostics](docs/diagnostics.md) for the implemented boundary and checks.

Bootstrap from the repository root with uv 0.12.4, Python 3.13.15, Node 22.23.2 and pnpm 10.34.5:

```sh
uv sync --locked --group dev
pnpm --dir web install --frozen-lockfile
```

Install missing `uv` and Node/pnpm with their user-local upstream installers; do not use system `pip`. Bootstrap may need registry/cache access. The following checks and application commands work without a hidden dependency install:

```sh
uv run --offline --no-sync uvicorn voca.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --ws websockets --ws-max-size 8192
pnpm --dir web dev --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173`. For a restart walkthrough, stop only that backend with SIGINT and relaunch the same command from the same working directory and `VOCA_DB_PATH`.

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

- [Code architecture](docs/code-architecture.md) — implemented session foundation, owners and dependency direction
- [Testing](docs/testing.md) — risk matrix and reproducible checks
- [Diagnostics](docs/diagnostics.md) — safe local JSONL policy and future map
- [Architecture](docs/architecture.md) — normative future voice/provider/control/task contracts
- [Roadmap](docs/roadmap.md) — vertical stages and gates
- [Glossary](CONTEXT.md) — shared terms
- [Index and historical grill](docs/index.md) — provenance and current navigation
- [ADR-0001](docs/adr/0001-rewrite-v3.md) — historical rewrite decision, 2026-09-02
- [ADR-0002](docs/adr/0002-conversation-lifecycle-and-roadmap.md) — accepted documentary lifecycle and roadmap decision
- [ADR-0003](docs/adr/0003-code-and-test-foundation.md) — agreed session/code/test foundation, pending runtime review
- [MiniCPM research](docs/research/minicpm5-2b.md) — source evidence and future comparison, not a model selection
