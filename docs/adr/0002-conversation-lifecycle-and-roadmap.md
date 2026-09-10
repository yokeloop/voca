---
tags:
  - voice-agent
  - v3
  - adr
status: proposed
---

# ADR-0002: Conversation lifecycle and gated roadmap

## Status

Proposed for acceptance in [VOCA-2](https://github.com/yokeloop/voca/issues/2), 2026-09-10. This ADR records the planned v3 contract, not implemented or experimentally verified behavior. [ADR-0001](0001-rewrite-v3.md) remains the historical rewrite decision of 2026-09-02; this document refines its lifecycle, deployment and stage order.

## Context

The initial documents listed technologies but did not define who owns a turn, when a response is cancelled, what happens to delegated work after a disconnect, or what evidence permits the next stage. They also treated a FastRTC interrupt callback, model replacement by URL and local resource fit as if these were already established.

The target remains a thin, single-user local voice assistant. VOCA-2 supplies a verifiable architecture and roadmap without adding application code, installing products or reporting unperformed measurements.

## Decision

1. **One business backend.** A Python/FastAPI conversation runtime owns the active turn, context and event queue. Audio transport, provider and delegation adapters and SQLite stay inside that boundary. Inference servers, LiteLLM and self-hosted LangSmith are supporting processes, not additional business backends.
2. **Toggle, not hold.** A button enables/disables a conversation; VAD segments speech while enabled. Minimal browser UI, WebRTC/FastRTC audio and separate control WebSocket belong to the first voice stage. Browser AEC is requested, not presumed effective.
3. **Explicit lifecycle and playback.** Session/connection/conversation/turn/generation are distinct. Speech onset invalidates the old generation, cancels STT/LLM/TTS, clears backend and browser playback and immediately captures the new utterance. Late callbacks/audio are discarded. FastRTC client flush must be demonstrated early; closing a generator is insufficient.
4. **Tasks outlive voice responses.** Barge-in, stop and disconnect never abort Pi work. Reconnect restores data with the microphone off and never replays old audio. Generated text, started playback and confirmed completion are stored separately; interrupted text is not declared fully heard.
5. **Pi first, with the selected instance.** `delegate(agent, task)` initially allows only `pi`. The user chooses the instance and cwd before dispatch; a new separate Pi session inherits its configuration/tools/MCP/restrictions, not an existing conversation or a blank environment. Authority remains exactly: **«Чтение, исследование и использование тулов/mcp подключенных к конкретно этому Pi инстансу»**. No general tools/MCP prohibition, arbitrary write permission or new approval layer is introduced. RPC completion follows `agent_settled`, not initial prompt acceptance or the first `agent_end`.
6. **Durable results.** SQLite is the source of truth; JSONL is export. Persist task ID before dispatch, deduplicate originating tool calls, commit terminal result and queue entry atomically. Insert result once into the original session context and schedule a separate result turn in a speaking pause, yielding to the user. Results persist while disconnected/off. Interrupted result speech is not automatically repeated. Unknown outcome after restart never authorizes automatic re-dispatch; storage deduplication does not promise physical exactly-once listening.
7. **Local provider chain.** Start with Whisper server transcription → LiteLLM → local llama.cpp/LFM2.5 → audio.cpp/TTSProvider. No automatic cloud fallback. Serving-layer Pythonic/XML normalization, followed by loop validation, replaces model-specific parsing in the loop. Only user-facing content reaches TTS; reasoning/tool/RPC payloads do not. Provider language/streaming/cancel compatibility must be verified, not inferred from a URL.
8. **Mandatory local observability.** Self-hosted LangSmith on the same host starts with the first voice stage. Application tracing and LiteLLM use explicit local endpoints; no SaaS default or opt-in substitution. Full available voice/task/result traces retain useful content after credential redaction. Export failure is visible and uses a bounded local queue without blocking the microphone. Official entitlement, artifact access, single-host profile, license egress/offline license, RAM/CPU/storage prerequisites remain launch gates; direct application launch does not imply container-free LangSmith.
9. **Vertical stage order.** Voice conversation → context/instructions → Pi delegation → separate additional adapters, richer UI and gated semantic/model experiments. SOUL → user → agent snapshot is fixed at turn start; managed reload applies to the next turn, preserving last valid instructions on failure. Reliable interruption is not deferred to polish. MiniCPM/unified audio.cpp/always-on/fine-tuning are not prerequisites.

## Consequences and gates

- [Architecture](../architecture.md) is the normative contract for provider messages, state transitions, control correlation and task delivery; [glossary](../../CONTEXT.md) supplies consistent names.
- [Roadmap](../roadmap.md) ties each user outcome to dependencies, acceptance and an evidence artifact. There are no dates or speculative future tickets.
- H1 browser interruption, H2 Russian voice/shared memory and H7 local tracing are early functional gates. H5 normalized tool scenarios gate Pi. H4/H6 and [MiniCPM research](../research/minicpm5-2b.md) gate only their optional candidates.
- Approximately 500 ms speech-end → first-audible is a target to measure, not a guarantee or an existing measurement. Missing that target records a bottleneck without automatically rejecting a functionally accepted voice stage.
- Official LangSmith guidance recommends at least 16 vCPU/64 GB RAM; the target host has roughly 32 GB shared with OS/inference and RTX 4070 12 GB VRAM. Entitlement/image access and a viable supported local profile are unproven. Documentation can be accepted with these explicit blockers; the dependent voice stage cannot be declared ready by substituting another tracing product.
- Provider/RPC documentation establishes source behavior only. No runtime, inference, browser or Pi experiment was executed by VOCA-2, and no real credential was needed for source verification.

## References

- [VOCA-2](https://github.com/yokeloop/voca/issues/2)
- [Current architecture and source verification](../architecture.md)
- [Roadmap](../roadmap.md)
- [Historical grill and current index](../index.md)
- [Historical ADR-0001](0001-rewrite-v3.md)
