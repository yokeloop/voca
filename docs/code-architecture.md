# Code architecture

VOCA-13 implements a local diagnostic session foundation, not voice. It makes one durable session through HTTP, returns an empty authoritative snapshot through the control WebSocket, and survives reconnect and application restart. The normative future voice lifecycle, provider and task contracts remain in [architecture](architecture.md).

## Implemented boundary

```text
React panel -> HTTP/WS control -> session service -> SQLite
                 |                  |
                 +-> safe JSONL <---+ diagnostics
```

`src/voca/app.py` composes the application. `create_app` receives explicit `Settings`; its lifespan owns the opened `SQLiteSessionStore` and configured diagnostic handler and closes both in `finally`. No resource is opened at import or factory call.

`src/voca/config.py` has the small, used configuration surface: database path, log directory, global level and component levels. Relative `.local` paths are relative to the documented application working directory.

`src/voca/core/session.py` contains `Session`, the narrow `SessionStore` protocol, `SessionService`, and domain errors. It depends only on the standard library and optional diagnostic event calls; it does not import FastAPI, Pydantic transport models, SQLite or provider SDKs. The service owns create/resume/error use cases, while the store owns durable cursor allocation.

`src/voca/adapters/sqlite.py` implements that protocol with one lifespan-owned connection and an `RLock`. Every connection operation, including close, is serialized. Short create/advance transactions use `BEGIN IMMEDIATE`; network work is never inside them. HTTP and WebSocket handlers invoke synchronous storage work through Starlette's threadpool boundary.

`src/voca/adapters/control.py` owns strict HTTP/WS wire validation, safe error mapping and wire-to-service conversion. A WebSocket handler owns only its socket and sequential commands; it does not become a second session cache or state machine. `POST /api/sessions` creates a new UUID session. `session.resume` advances the durable numeric cursor and returns a snapshot with empty real histories. Cursor `eN` is a wire encoding, not a client command ID.

`src/voca/diagnostics.py` provides bounded local JSONL metadata logging. It is not a tracing adapter and does not collect content.

`web/src/control.ts` owns the small HTTP/WS client and explicit incoming wire validation. `web/src/App.tsx` owns its socket, displayed state and last applied cursor only; it projects server snapshots and does not own conversation business state.

## Dependency and resource direction

```text
composition(app) -> control -> core
composition(app) -> SQLite -> core SessionStore
composition(app) -> diagnostics
web -> control wire contract
```

The composition root is the only place that joins concrete resources. `SessionStore` is deliberately a current-use protocol, not a generic Repository or Unit-of-Work layer. There is no DI container, plugin system, event bus, background queue, task supervisor, `TaskGroup`, session cache, ORM or empty future directory tree.

The current shutdown has no workers to stop. When voice arrives, its runtime must own active turn/context/generation and audio queues; capture must not wait for cancellation of old inference, and callbacks of old generations must be discarded. Browser playback/flush acknowledgement remains browser-owned. Future task supervision and trace export have independent cancellation lifecycles, not one fail-fast `TaskGroup`. `CancelledError` must propagate after cleanup, and SQLite must not await network inside transactions.

## Implemented control subset

`POST /api/sessions` accepts only JSON `{}` and returns `201` with a UUID and `last_event_id: null`; it is intentionally not idempotent. The panel blocks duplicate clicks and reports a lost response as uncertain rather than silently creating another session.

A control connection validates its canonical UUID and existing session before binding. It does not create a session, snapshot, or cursor. A valid `session.resume` matching the path emits one full snapshot with `connection: "disconnected"`, `conversation: "off"` and empty `messages`, `tasks`, and `deliveries`. Repeating resume is business-idempotent but can allocate a later response cursor. Invalid, unsupported and mismatched commands receive safe session-level errors with a newly durable cursor; a later valid resume remains possible. A future cursor is rejected after allocation. There is no event replay or command ledger yet. VOCA-4 must add durable deduplication before it adds side effects such as start or dispatch.

## Queue map

| Issue | Future seam | Preserved boundary and future proof |
|---|---|---|
| [VOCA-4](https://github.com/yokeloop/voca/issues/4) | conversation/instructions, audio, providers, tracing, browser media | Runtime owns turns; initial valid instruction snapshot; provider cancel/capabilities; independent trace export. Unit decisions, storage/control integration, browser H1 and hardware H2/H7. LangSmith preflight first. |
| [VOCA-5](https://github.com/yokeloop/voca/issues/5) | instructions/history, SQLite snapshots/playback, JSONL, control | Immutable turn snapshot and generated != heard. SQLite restart/export facts and browser walkthrough. |
| [VOCA-6](https://github.com/yokeloop/voca/issues/6) | delegation, Pi adapter, durable tasks/delivery, task UI | Validate calls before dispatch; settled/unknown mapping; atomic delivery/context/claim; barge-in does not abort. |
| [VOCA-7](https://github.com/yokeloop/voca/issues/7) | Hermes adapter | Its SSE/approval/terminal mapping preserves task/delivery invariants. |
| [VOCA-8](https://github.com/yokeloop/voca/issues/8) | OpenClaw adapter | Its WS/runId/wait mapping has separate recovery checks. |
| [VOCA-9](https://github.com/yokeloop/voca/issues/9) | `web/src` | UI remains a control projection; component/browser tests cover new interactions. |
| [VOCA-10](https://github.com/yokeloop/voca/issues/10) | conversation/audio end-of-turn seam | Semantic end detection is compared with VAD using an H6 corpus. |
| [VOCA-11](https://github.com/yokeloop/voca/issues/11) | activation before capture | Opt-in activation preserves speech start and validates H6 false/missed/latency thresholds. |
| [VOCA-12](https://github.com/yokeloop/voca/issues/12) | additional STT adapter/config | Whisper remains baseline; H1/H2/H3 and capability tests decide. |
| [VOCA-1](https://github.com/yokeloop/voca/issues/1) | LLM provider/config research | Compare after VOCA-12 with H5 normalized calls and H1/H2/H7/H3 checks. |

These names are placement plans, not files created by this PR.
