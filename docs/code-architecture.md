# Code architecture

VOCA-13 is a persisted-session foundation, not a voice runtime. The repository explicitly separates `backend/` and `frontend/`. The [Clean Architecture dependency rule](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html) here means source dependencies point inward and boundaries use simple data; it does not require a framework or a fixed number of classes.

## Backend

```text
bootstrap/app -> presentation/http, presentation/control
bootstrap/app -> application/sessions -> application/ports, domain/session
bootstrap/app -> infrastructure/sqlite -> application/ports, domain/session
bootstrap/app -> infrastructure/observability
```

`backend/src/voca/domain/session.py` is the immutable session entity. `application/ports.py` contains the session-store and narrow diagnostics protocols; `application/sessions.py` owns create/get/resume/error use cases and their session-operation scopes. The presentation layer validates wire values once in `schemas.py`, maps HTTP in `http.py`, binds sequential WebSocket commands in `control.py`, and keeps payload/timeout helpers in `responses.py`. It never reads a store directly.

`infrastructure/sqlite.py` owns one connection, `RLock`, `BEGIN IMMEDIATE`, rollback, FULL synchronous mode and error translation; `sqlite_schema.py` owns version-1 schema validation. Observability configuration is separate from concrete diagnostics. Lifespan opens/closes concrete resources; `create_app` opens none.

HTTP is `POST /api/sessions` with strict `{}`, `201`, `Cache-Control: no-store`, and safe 400/415/503/500 mappings. WS validates canonical UUID/envelopes and gives snapshot-only recovery. `session.resume` decodes `eN` once, allocates one durable response cursor, and rejects a future cursor with that same allocated event; no replay, ledger or retry exists. Sends and closes share a five-second bound; cancellation does bounded cleanup then propagates.

## Frontend

`frontend/src/app/App.tsx` renders only the public API of `pages/session-console`. The page owns one real slice: `api` contains HTTP/protocol/the sole WebSocket owner, `model` contains storage, projection and React orchestration, `ui` contains JSX, and `lib` contains identifiers. This is [pages-first FSD](https://feature-sliced.design/docs/guides/migration/from-custom): no empty shared/entities/features/widgets/processes layers, no wildcard barrel, and external code enters only `pages/session-console/index.ts` ([layers](https://feature-sliced.design/docs/reference/layers), [slices and segments](https://feature-sliced.design/docs/reference/slices-segments), [public API](https://feature-sliced.design/docs/reference/public-api)).

The connection object fences replaced sockets before closing them. Requested restore cursor is never the projection watermark. A projection stores its session, last **applied** event cursor, snapshot and error; it compares cursors as `BigInt`, ignores foreign/duplicate/older events, and preserves a snapshot when an error arrives. A valid future requested cursor may yield an applied `invalid_cursor` event; a later null restore starts a fresh projection. Storage retains only `{session_id,last_event_id}`, safely clears invalid entries, and does not block a current session when unavailable.

## Queue map

| Issue | Future placement and preserved proof |
|---|---|
| VOCA-4 | domain/application conversation and instructions; infrastructure audio/providers/tracing; presentation control/audio; frontend page-local media. Runtime/cancellation, LangSmith and browser gates remain separate. |
| VOCA-5 | domain/application history/instructions; infrastructure SQLite/JSONL; page projection. Snapshot and playback facts remain durable. |
| VOCA-6 | domain/application delegation rules/use cases; infrastructure Pi/SQLite; presentation control; page task projection. Atomic delivery and independent task cancellation remain required. |
| VOCA-7 | infrastructure Hermes adapter only; its SSE/approval/terminal mapping preserves task invariants. |
| VOCA-8 | infrastructure OpenClaw adapter only; its WS/runId/wait recovery remains distinct. |
| VOCA-9 | `frontend/src` page-local control/task projection; entities/features appear only when a real responsibility justifies them. |
| VOCA-10 | domain/application conversation end-of-turn use cases and provider boundary; H6 corpus remains the evidence. |
| VOCA-11 | application activation before capture, presentation/browser boundary; false/missed/latency gate remains separate. |
| VOCA-12 | infrastructure additional STT adapter/config; Whisper baseline and H1/H2/H3 checks remain required. |
| VOCA-1 | application provider use cases and infrastructure LLM adapter/config; normalized calls and H1/H2/H7/H3 evidence remain required. |

These are placement plans, not files created by this PR.
