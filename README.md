# VOCA

**VOCA v3** is a local voice-assistant project. So far it implements only a diagnostic persisted-session foundation: a SQLite session, HTTP bootstrap, control WebSocket snapshots and a browser panel. Voice, models, microphone/media, providers and external services remain future work.

## Layout and bootstrap

`backend/` is the Python/FastAPI application with its own package, lockfile and tests. `frontend/` is the Vite/React diagnostic page with its own lockfile and tests. From the repository root, with uv 0.12.4, Python 3.13.15, Node 22.23.2 and pnpm 10.34.5:

```sh
(cd backend && uv sync --locked --group dev)
pnpm --dir frontend install --frozen-lockfile
```

Before changing into `backend`, resolve the selected relative paths from the original repository cwd so an existing `.local/` database remains the selected data source:

```sh
REPO_ROOT="$(pwd -P)"
export VOCA_DB_PATH="${VOCA_DB_PATH-.local/voca.sqlite3}"
export VOCA_LOG_DIR="${VOCA_LOG_DIR-.local/logs}"
case "$VOCA_DB_PATH" in /*) ;; *) VOCA_DB_PATH="$REPO_ROOT/$VOCA_DB_PATH" ;; esac
case "$VOCA_LOG_DIR" in /*) ;; *) VOCA_LOG_DIR="$REPO_ROOT/$VOCA_LOG_DIR" ;; esac
(cd "$REPO_ROOT/backend" && uv run --offline --no-sync uvicorn voca.bootstrap.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --ws websockets --ws-max-size 8192)
```

In another terminal from the repository root:

```sh
pnpm --dir frontend dev --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173`. Stop only the backend process with SIGINT before a restart and repeat the same absolute-path exports.

## Checks

```sh
(cd backend && uv run --offline --no-sync ruff format --check src tests)
(cd backend && uv run --offline --no-sync ruff check src tests)
(cd backend && uv run --offline --no-sync pytest -q)
pnpm --dir frontend test
```

The full list, including type and lint checks, is in [Testing](docs/testing.md).

## Contributing

Open an issue or a pull request. Run the checks above before sending a change; the code boundaries are described in [Code architecture](docs/code-architecture.md).

## Docs

- [Code architecture](docs/code-architecture.md)
- [Testing](docs/testing.md)
- [Diagnostics](docs/diagnostics.md)
- [Future architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Glossary](CONTEXT.md)
- [Documentation index](docs/index.md)
- [ADR-0003](docs/adr/0003-code-and-test-foundation.md)

## License

MIT, see [LICENSE](LICENSE).
