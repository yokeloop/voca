# AGENTS.md — VOCA

VOCA v4 is a local voice assistant: LiveKit carries the voice, audio.cpp
recognises and speaks, llama-server answers, and longer work is delegated to
Hermes. This file is the working contract for agents and contributors.

## Layout

- `agent/` — the Python package `voca` (uv project, Python 3.13). `voca web`
  serves the page, `GET /healthz` and `POST /api/token`.
- `web/` — the page (Vite + TypeScript, `livekit-client`); `pnpm --dir web build`
  writes `web/dist/`, which `voca web` serves.
- `bench/` — stand scripts and their reports; they run in the `agent/`
  environment (`bench/README.md`).
- `scripts/` — the stand: `up.sh`, `down.sh`, `fetch.sh`, `env.sh`, the
  `SHA256SUMS` of every download and the audio.cpp `server.json` template.
- `.local/` — only what VOCA itself creates: logs, pid files, the assembled
  `server.json`, recordings. Never committed.

## Checks

From the repository root:

```sh
(cd agent && uv sync --locked --group dev)
(cd agent && uv run --offline --no-sync ruff format --check src tests ../bench)
(cd agent && uv run --offline --no-sync ruff check src tests ../bench)
(cd agent && uv run --offline --no-sync mypy)
(cd agent && uv run --offline --no-sync pytest -q)
pnpm --dir web install --frozen-lockfile
pnpm --dir web format:check
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build
bash -n scripts/*.sh
git diff --check
```

The tests need no network, no models and no livekit-server. CI runs exactly
these commands and never starts the stand. `scripts/fetch.sh --check` checks the
stand's downloads; it is a stand check, not a CI check.

## Stand variables

`scripts/env.sh` is the only place of defaults; every script sources it. It reads
`.env` from the repository root when present. `.env` is a bash file (copy
`.env.example`), and a value already set in the environment wins over it.
`voca web` reads the `VOCA_*` variables from the environment that `scripts/env.sh`
exports; it does not read `.env` itself.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOCA_APPS_DIR` | `$HOME/Apps` | Root of downloaded tools, `apps/` in `SHA256SUMS` |
| `VOCA_MODELS_DIR` | `$HOME/models` | Root of downloaded models, `models/` in `SHA256SUMS` |
| `VOCA_LIVEKIT_BIN` | `$VOCA_APPS_DIR/livekit/1.13.11/livekit-server` | livekit-server release binary |
| `VOCA_LIVEKIT_NODE_IP` | `127.0.0.1` | `--node-ip`: the address livekit-server announces for media |
| `VOCA_LIVEKIT_API_KEY` | `devkey` | Key that signs tokens; the built-in key of `livekit-server --dev` |
| `VOCA_LIVEKIT_API_SECRET` | `secret` | Secret that signs tokens; the built-in secret of `livekit-server --dev` |
| `VOCA_WEB_HOST` | `127.0.0.1` | Address `voca web` listens on |
| `VOCA_WEB_PORT` | `8083` | Port `voca web` listens on |
| `VOCA_PUBLIC_LIVEKIT_URL` | `ws://127.0.0.1:7880` | LiveKit URL returned with each token |
| `VOCA_HERMES_URL` | empty | Hermes API server URL for `bench/hermes_runs.py`; `https://hermes.priney.com` on the stand |
| `VOCA_HERMES_KEY` | empty | `API_SERVER_KEY` of the Hermes profile; a secret, only in `.env` |

Everything downloaded lives outside the clone, in the machine's shared
directories `~/Apps/<tool>/<version>/` and `~/models/`; a variable with that
default names each path. `devkey`/`secret` cannot change without a
livekit-server config.

## Downloads

`scripts/SHA256SUMS` lists every download, one file per line, fields separated
by spaces, `#` starts a comment:

```
<sha256>  <root>/<tool>/<version>/<file>  <source>
```

- `<root>`: `apps` resolves to `$VOCA_APPS_DIR`, `models` to `$VOCA_MODELS_DIR`.
  The path is relative and has no `..`.
- `<source>`: `https://…` downloads the file; `tar:<archive>` extracts `<file>`
  from `<archive>` in the same directory, listed on an earlier line.

`scripts/fetch.sh` walks the file in order. An existing file is verified and
never overwritten: a mismatch stops with «удалите файл и повторите». A missing
`https` file is downloaded to `<file>.part`, verified and moved in place; a
missing `tar:` file is extracted and verified. Each line prints
`fetched <path>` or `ok <path>`. An unknown root, an absolute path or `..` stops
with the line number.

`scripts/fetch.sh --check` writes nothing and prints `OK`, `MISSING` or
`MISMATCH <path>` per line; it exits 1 when any line is not `OK`.

A new download (audio.cpp, llama-server, models) is one line in `SHA256SUMS`
plus a path variable with its default in `scripts/env.sh` and `.env.example`.

## Stand

`scripts/up.sh [livekit|audio|llm|web …]` (no arguments: all four) checks every
named service first and starts nothing when a check fails: the binary or build
exists, the service is not already running, its ports are free. It then starts
them in order with logs in `.local/logs/<name>.log` and pids in
`.local/run/<name>.pid`, and waits up to 20 s for each. `scripts/down.sh [names…]`
stops them in reverse order. `audio` and `llm` are placeholders until VOCA-5
(0.2) and VOCA-4 (0.3) and always fail their check.

livekit-server runs as `--dev --bind 0.0.0.0 --node-ip "$VOCA_LIVEKIT_NODE_IP"`.

`scripts/check-rtc.sh [page-url]` (default `http://$VOCA_WEB_HOST:$VOCA_WEB_PORT`)
checks LiveKit signalling as a real client would. It takes a token from
`<page-url>/api/token`, maps the returned `url` (`ws`/`wss`) to `http`/`https`,
and expects `200` from both validate paths of livekit-client:

- `/rtc/validate?access_token=<token>` — the v0 signal path;
- `/rtc/v1/validate?access_token=<token>&join_request=<…>` — the v1 path, which
  livekit-server rejects with 400 `join_request is required` without the second
  parameter.

`join_request` is generated, never hard-coded, the same way livekit-client 2.23
builds it in `createJoinRequestConnectionParams`: a `livekit.JoinRequest` with
`ClientInfo` (`sdk` JS, `version` from `web/package.json`, `protocol` 17 and
`client_protocol` 2 — the client's `protocolVersion` and `clientProtocol`) and
`ConnectionSettings(auto_subscribe=True)`, gzip-compressed into a
`livekit.WrappedJoinRequest` (`compression` GZIP), URL-safe base64. The protobuf
classes come from `livekit-api` in the `agent/` environment.

## Ports

| Port | Service |
| --- | --- |
| 7880 TCP | livekit-server HTTP and WebSocket signalling |
| 7881 TCP | livekit-server media over TCP |
| 7882 UDP | livekit-server media over UDP |
| 8083 TCP | `voca web` (`VOCA_WEB_PORT`) |
| 8082 TCP | audio.cpp server (0.2) |
| 8081 TCP | llama-server (0.3) |

Deviation from `derive:eb1id854:§1`: `voca web` listens on 8083, not 8090,
because 8090 is taken on the stand host (hyperhdr).

## References

- `derive:8kki4ynb` — target architecture v4.
- `derive:eb1id854` — detailed design.
- E0 epic research: https://draft.yokeloop.com/artifacts/voca-1-e0-llm-hermes-iim7wlw3
