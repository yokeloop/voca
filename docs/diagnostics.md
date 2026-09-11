# Local diagnostics

VOCA-13 emits bounded local JSONL metadata, not LangSmith tracing. `bootstrap/settings.py` reads database/log paths and levels. `infrastructure/observability/logging.py` configures the concrete handler; `diagnostics.py` provides operation scopes. Application use cases own their own session scopes, while presentation owns transport scopes. This keeps instrumentation independent of logging factories.

Records use an allowlist: timestamp, level, static event/component/outcome, operation/session/command/server-event IDs, duration and safe reason. No raw frames, request headers, paths, environment, SQL parameters, exception rendering or payloads are collected. INFO records normal work; WARNING covers rejected commands, timeout and cancellation; ERROR covers failures. A handler rotates at 262144 bytes with three backups, limits a record to 2048 bytes, and emits exactly one safe degraded warning on write failure before dropping later records. SQLite continues when logs are degraded.

The future voice/task/tracing map remains in [architecture](architecture.md): local logs, SQLite facts and future LangSmith traces are distinct. Cancellation and timeout remain terminal outcomes with explicit operation/session correlation; local diagnostics neither satisfy nor weaken the future LangSmith gate.

## Future diagnostic map

| Area | Safe metadata and distinct responsibility |
|---|---|
| capture, transport, activation and end-of-turn | start/end/error/cancel/timeout, session/turn/generation IDs and clock domain; no audio or transcript payload |
| STT, instruction/context, LLM and tool validation | lifecycle, IDs, provider outcome and safe reason; never raw prompt, content or credentials |
| TTS, backend queue, browser playback and clear acknowledgement | lifecycle, generation, queue depth/drop reason and browser clock domain; server output does not claim audible playback |
| task dispatch, progress, settled, unknown and recovery | task/session/turn IDs and durable state transitions; task cancellation remains separate from voice cancellation |
| delivery enqueue, claim and context insertion | task/session IDs and atomic outcome; no result content in this local log |
| trace enqueue, export, retry, drop and recovery | trace IDs, queue depth/drop reason and exporter outcome; LangSmith data has its own redaction and entitlement gate |

Late-generation discard records metadata, never audio or text. Browser timing stays in its own clock domain; missing playback acknowledgement does not prove heard audio, and cancellation request/ack does not prove physical inference stopped.
