# Local diagnostics

VOCA-13 logs operation metadata to bounded local JSONL. This is deliberately not LangSmith tracing and does not weaken the mandatory self-hosted LangSmith gate for voice.

## Configuration and retention

`VOCA_DB_PATH`, `VOCA_LOG_DIR`, `VOCA_LOG_LEVEL`, and `VOCA_LOG_LEVELS` are read when the app is created. Unknown components, non-string levels, and invalid JSON are safe configuration failures without echoing values. Supported components are `app`, `control`, `session`, and `storage`.

The application writes UTF-8 `.local/logs/voca.jsonl` using one `RotatingFileHandler`: 262144 bytes per file, three backups, and at most 2048 bytes per serialized record. Retention is size-based: at most four files, about 1 MiB; it is not an audit log or a day-retention promise. Logging failure disables that handler until restart, prints exactly one `diagnostics/write_failed` warning to stderr, then drops records without retries, buffers, exception tracebacks or recursive logging. Storage continues to work.

## Safe event schema

Each event uses only allowlisted fields: UTC `timestamp`, `level`, static `event`, generated UUID `operation_id`, static `component`, `outcome`, and terminal `duration_ms` from a monotonic clock. Existing validated `session_id`, UUID command ID and `eN` server event ID may be added. Event names include `app.startup`, `storage.open`, `storage.schema`, `http.session_create`, `session.create`, `storage.session_insert`, `control.connect`, `control.receive`, `session.resume`, `storage.cursor_advance`, `control.snapshot_send`, `control.command_rejected`, `control.error_send`, `control.disconnect`, `app.shutdown`, and `storage.close`.

HTTP/WS nested stages receive the same explicit operation ID. Session correlation starts only once the actual ID is valid. Invalid and startup paths do not invent a session. Start and terminal outcomes are logged for called operations; terminal entries carry a non-negative duration and safe enum reason where relevant.

INFO records lifecycle and successful session/control operations; DEBUG is storage/validation/send detail; WARNING records rejected commands, timeouts and protocol errors; ERROR records unusable storage or unexpected failures. Component levels filter detail but not terminal session/error facts. Normal socket close is not an error. Repeated lifespans do not duplicate handlers.

Raw request frames, headers, query strings, unknown command text, environment, SQL parameters, user paths, `str(exception)`, tracebacks and `exc_info` are never collected. This is an allowlist policy, not a promise that regex can redact arbitrary secrets. Current diagnostics collect no sensitive content even at DEBUG. Future voice content logging requires separate explicit component/session opt-in, bounded retention, credential removal before queueing, and fail-closed behavior for payloads that cannot be scrubbed.

## Future diagnostic map

| Area | What to record without content |
|---|---|
| capture/transport/activation/end-of-turn | start/end/error/cancel/timeout, session/turn/generation IDs and clock domain |
| STT, instruction/context, LLM/tool validation | operation lifecycle, IDs, provider outcome and safe reason |
| TTS/backend queue/browser playback/clear ACK | operation lifecycle, generation, queue depth/drop reason and browser clock domain |
| task dispatch/progress/settled/unknown/recovery | task/session/turn IDs and durable state transitions |
| delivery enqueue/claim/context insertion | task/session IDs and atomic outcome |
| trace enqueue/export/retry/drop/recovery | trace IDs, queue depth/drop reason and export outcome |

Late-generation discard must record metadata, never audio or text. Browser timing uses its own clock. A missing playback ACK does not prove heard audio, and inference cancellation request/ack does not prove physical inference stopped.
