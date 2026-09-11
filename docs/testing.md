# Testing

VOCA-13 uses the cheapest sufficient check: real SQLite/lifespan tests for persistence and lifecycle, focused diagnostics tests, pure frontend projection/protocol tests, and short AST/TypeScript-parser import-boundary checks. There is no coverage target, mock infrastructure, browser harness or duplicated layer suite. Browser/reload/reconnect walkthrough is `/review` evidence, not inferred from these checks.

From the repository root after the locked bootstrap:

```sh
(cd backend && uv run --offline --no-sync ruff format --check src tests)
(cd backend && uv run --offline --no-sync ruff check src tests)
(cd backend && uv run --offline --no-sync mypy src/voca)
(cd backend && uv run --offline --no-sync pytest -q)
pnpm --dir frontend format:check
pnpm --dir frontend lint
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
git diff --check
```

The Python suite proves real schema/close/restart/error and bounded transport helpers. The Node built-in test runner imports pure TypeScript through Node strip-types and checks cursor precision, projection semantics and source boundaries. It adds no test dependency. The suite requires no GPU, credentials, models, services or browser after bootstrap.
