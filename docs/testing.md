# Testing

VOCA-13 uses the cheapest sufficient test for the risk. There is no coverage or line-count target, no test infrastructure for future layers, and no browser/provider/GPU simulation.

## Today

| Risk | Evidence |
|---|---|
| session creation, cursor durability and restart | real SQLite plus `TestClient` in `test_session_flow.py` |
| HTTP/WS validation and recovery after errors | parameterized real control workflow in `test_session_flow.py` |
| lifespan ownership and unusable storage | `test_app_lifecycle.py` with actual close and incompatible paths/schema |
| metadata safety, filtering, rotation and logging degradation | focused `test_diagnostics.py` |
| browser wire client correctness | TypeScript typecheck/lint/build; manual browser walkthrough is separate evidence |

The suite has no sleeps, async plugin, mock server, generic fixture factory, log snapshots or duplicated unit/API/browser assertions. Fakes are only appropriate later for expensive boundaries that actually exist. Hardware/browser/provider gates remain separate: a green API suite does not prove microphone, playback flush, GPU cancellation or credentials.

## Reproducible checks

From the application root after the documented locked bootstrap:

```sh
uv run --offline --no-sync ruff format --check src tests
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync mypy src/voca
uv run --offline --no-sync pytest -q
pnpm --dir web format:check
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build
git diff --check
```

A small smoke is `uv run --offline --no-sync pytest tests/test_session_flow.py -q`; it requires no models, GPU, credentials, network service or provider call after bootstrap. The separate browser walkthrough records HTTP POST, WebSocket frames, reload/reconnect and backend restart using the same SQLite file. If browser access is unavailable, that acceptance evidence is explicitly unverified rather than inferred from TestClient.

The GitHub workflow runs this same set with locked Python/uv/Node/pnpm dependencies and read-only repository contents. It has no secrets, GPU, external service, browser download matrix or coverage gate.
