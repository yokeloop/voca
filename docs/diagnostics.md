# Local diagnostics

VOCA-13 emits bounded local JSONL metadata, not LangSmith tracing. `bootstrap/settings.py` reads database/log paths and levels. `infrastructure/observability/logging.py` configures the concrete handler; `diagnostics.py` provides operation scopes. Application use cases own their own session scopes, while presentation owns transport scopes. This keeps instrumentation independent of logging factories.

Records use an allowlist: timestamp, level, static event/component/outcome, operation/session/command/server-event IDs, duration and safe reason. No raw frames, request headers, paths, environment, SQL parameters, exception rendering or payloads are collected. INFO records normal work; WARNING covers rejected commands, timeout and cancellation; ERROR covers failures. A handler rotates at 262144 bytes with three backups, limits a record to 2048 bytes, and emits exactly one safe degraded warning on write failure before dropping later records. SQLite continues when logs are degraded.

The future voice/task/tracing map remains in [architecture](architecture.md): local logs, SQLite facts and future LangSmith traces are distinct. Cancellation and timeout remain terminal outcomes with explicit operation/session correlation; local diagnostics neither satisfy nor weaken the future LangSmith gate.
