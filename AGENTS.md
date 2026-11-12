# AGENTS.md — VOCA

VOCA v4 is a local voice assistant: LiveKit carries the voice, audio.cpp
recognises and speaks, llama-server answers, and longer work is delegated to
Hermes. This file is the working contract for agents and contributors.

## Layout

- `agent/` — the Python package `voca` (uv project, Python 3.13). `voca web`
  serves the page, `GET /healthz` and `POST /api/token`.
  `voca agent {dev|start|console}` runs the LiveKit worker: `app/worker.py`
  (prewarm, entrypoint, `WorkerOptions`), `app/wiring.py` (the plugins,
  `PreparedTTS`, `build_session`, `SpeechLanguageClient`), `conversation/`
  (`VocaAgent`: the tool-call markup filter in `llm_node`, the `delegate`
  stub, the constant instructions), `telemetry/` (`.local/events.jsonl`).
  `agent/src/voca/speech/` prepares text before TTS: the tool-call markup
  filter, LaTeX and tables, numbers as words, stress and the first fragment.
  `markup`, `numbers` and `prepare` are pure modules, which
  `tests/test_import_boundaries.py` checks.
- `web/` — the page (Vite + TypeScript, `livekit-client`); `pnpm --dir web build`
  writes `web/dist/`, which `voca web` serves.
- `bench/` — stand scripts and their reports; they run in the `agent/`
  environment (`bench/README.md`).
- `scripts/` — the stand: `up.sh`, `down.sh`, `fetch.sh`, `env.sh`, the
  `SHA256SUMS` of every download, the audio.cpp `server.json` template and
  `llm-profiles/`, one llama-server model profile per file.
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
bash -n scripts/*.sh scripts/llm-profiles/*.sh
git diff --check
```

The tests need no network, no models and no livekit-server. CI runs exactly
these commands and never starts the stand. `scripts/fetch.sh --check` checks the
stand's downloads; it is a stand check, not a CI check.

## Stand variables

`scripts/env.sh` is the only place of defaults; every script sources it. It reads
`.env` from the repository root when present. `.env` is a bash file (copy
`.env.example`), and a value already set in the environment wins over it.
`voca web` and `voca agent` read the `VOCA_*` variables from the environment that
`scripts/env.sh` exports; they do not read `.env` themselves.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOCA_APPS_DIR` | `$HOME/Apps` | Root of downloaded tools, `apps/` in `SHA256SUMS` |
| `VOCA_MODELS_DIR` | `$HOME/models` | Root of downloaded models, `models/` in `SHA256SUMS` |
| `VOCA_LIVEKIT_BIN` | `$VOCA_APPS_DIR/livekit/1.13.11/livekit-server` | livekit-server release binary |
| `VOCA_AUDIOCPP_BIN` | `$VOCA_APPS_DIR/audio.cpp/0.9.4/audiocpp_server` | audio.cpp server release binary (CPU); its ggml libraries sit next to it |
| `VOCA_LLAMA_BIN` | `$VOCA_APPS_DIR/llama.cpp/llama-b12167/llama-server` | llama-server of the llama.cpp CUDA release; its libraries sit next to it |
| `VOCA_LLM_PROFILE` | `lfm2.5-2.6b` | llama-server model profile, `scripts/llm-profiles/<name>.sh`; only this model is loaded. `lfm2.5-2.6b` is the agent's model; `bolt-e4b` and `lfm3-1.7b` are alternatives, set explicitly |
| `VOCA_LIVEKIT_NODE_IP` | `127.0.0.1` | `--node-ip`: the address livekit-server announces for media |
| `VOCA_LIVEKIT_API_KEY` | `devkey` | Key that signs tokens; the built-in key of `livekit-server --dev` |
| `VOCA_LIVEKIT_API_SECRET` | `secret` | Secret that signs tokens; the built-in secret of `livekit-server --dev` |
| `VOCA_WEB_HOST` | `127.0.0.1` | Address `voca web` listens on |
| `VOCA_WEB_PORT` | `8083` | Port `voca web` listens on |
| `VOCA_PUBLIC_LIVEKIT_URL` | `ws://127.0.0.1:7880` | LiveKit URL returned with each token |
| `VOCA_LIVEKIT_URL` | `ws://127.0.0.1:7880` | LiveKit URL the agent worker connects to |
| `VOCA_AUDIO_URL` | `http://127.0.0.1:8082/v1` | audio.cpp API of the agent's STT and TTS |
| `VOCA_LLM_URL` | `http://127.0.0.1:8081/v1` | llama-server API of the agent's LLM |
| `VOCA_AGENT_PORT` | `8084` | Health port of the agent worker (127.0.0.1) |
| `VOCA_STATE_DIR` | `$VOCA_ROOT/.local` | Directory of the agent's `events.jsonl` |
| `VOCA_HERMES_URL` | empty | Hermes API server URL for `bench/hermes_runs.py`; `https://hermes.priney.com` on the stand |
| `VOCA_HERMES_KEY` | empty | `API_SERVER_KEY` of the Hermes profile; a secret, only in `.env` |

The agent's pipeline settings (`VOCA_TTS_VOICE`, `VOCA_LLM_THINKING_BUDGET`,
`VOCA_VAD_MIN_SILENCE`, `VOCA_INTERRUPTION_MIN_DURATION`,
`VOCA_INTERRUPTION_MIN_WORDS`, `VOCA_AEC_WARMUP`, `VOCA_ENDPOINTING_MIN_DELAY`,
`VOCA_PREEMPTIVE_GENERATION`) have their defaults in `Settings`
(`agent/src/voca/config.py`); `scripts/env.sh` does not set them, and
`.env.example` lists them commented out. `VOCA_AEC_WARMUP` covers only the
first N seconds from the start of the session's first agent reply; later
replies are interruptible at once.

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
  from `<archive>`, where the member is `<file>` or `./<file>`;
  `untar:<archive>` unpacks the whole `<archive>` without its top-level
  directory. The archive is in the same directory, listed on an earlier line.

`scripts/fetch.sh` walks the file in order. An existing file is verified and
never overwritten: a mismatch stops with «удалите файл и повторите». A missing
`https` file is downloaded to `<file>.part`, verified and moved in place; a
missing `tar:` file is extracted and verified. A missing `untar:` file unpacks
the whole archive into its directory without the archive's top-level directory,
keeping files that already exist, and is verified; the next lines of the same
archive then find their files and only verify them. Each line prints
`fetched <path>` or `ok <path>`. An unknown root, an absolute path or `..` stops
with the line number.

`scripts/fetch.sh --check` writes nothing and prints `OK`, `MISSING` or
`MISMATCH <path>` per line; it exits 1 when any line is not `OK`.

A new download (audio.cpp, llama-server, models) is one line in `SHA256SUMS`
plus a path variable with its default in `scripts/env.sh` and `.env.example`.

## Stand

`scripts/up.sh [livekit|audio|llm|web|agent …]` (no arguments: all five) checks
every named service first and starts nothing when a check fails: the binary or
build exists, the service is not already running, its ports are free; for
`agent` — `agent/.venv/bin/voca`, the ruaccent `koziev` link
(`scripts/ruaccent-setup.sh`) and `VOCA_AGENT_PORT`. It then starts them in
order with logs in `.local/logs/<name>.log` and pids in
`.local/run/<name>.pid`, and waits up to 20 s for each (120 s for `audio` and
`llm`, 60 s for `agent`). `agent` runs `voca agent start`; it is ready when
`http://127.0.0.1:$VOCA_AGENT_PORT/` answers 200 (503 until the worker has
registered with livekit-server). `scripts/down.sh [names…]` stops them in
reverse order, `agent` first.

`scripts/audiocpp.sh [--check]` renders `scripts/server.json` into
`.local/server.json` with `envsubst '${VOCA_MODELS_DIR}'`, then checks that
`VOCA_AUDIOCPP_BIN` is executable, every model path in the rendered config exists
and its port (8082) is free; with `--check` it stops there. Otherwise it `exec`s
`audiocpp_server --config .local/server.json --no-ui` (README 0.2): Parakeet as
`asr` in `mode: offline`, Supertonic as `tts` in `mode: streaming` with voice `F4`.

`scripts/ruaccent-setup.sh` verifies the ruaccent `koziev/` files in
`$VOCA_MODELS_DIR/ruaccent/accentuator-b78ae5e/` against `SHA256SUMS` and links
them into the `ruaccent` package of `agent/.venv`; it is idempotent, and
`bench/tts_pipeline.py` stops without the link.

`scripts/llama.sh [--check]` sources the profile
`scripts/llm-profiles/$VOCA_LLM_PROFILE.sh` (README 0.3.1), which sets `model`
and `args`; an unknown profile or a missing profile file stops it. It then
checks that `VOCA_LLAMA_BIN` is executable, the profile's model exists and port
8081 is free; with `--check` it stops there. Otherwise it `exec`s llama-server
with the shared flags — `--alias "$VOCA_LLM_PROFILE"`, `--jinja`,
`127.0.0.1:8081`, `-c 16384`, `-fa on`, q8_0 KV cache, `--parallel 1`, `-t 2`,
all layers on the GPU — plus the profile's `args`. Extra arguments are
appended to the command. Sampling is set on the server by the profile, from
the model card; chat template kwargs come from the client.

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
| 8082 TCP | audio.cpp server (127.0.0.1) |
| 8081 TCP | llama-server (127.0.0.1) |
| 8084 TCP | voca agent health (127.0.0.1, `VOCA_AGENT_PORT`) |

Deviation from `derive:eb1id854:§1`: `voca web` listens on 8083, not 8090,
because 8090 is taken on the stand host (hyperhdr).

Further deviations from `derive:eb1id854` (E1 research, VOCA-11):

- О4: the stand runs `voca agent start` and waits for its HTTP health
  endpoint, not `dev` and a log line.
- О5: the worker reads `VOCA_LIVEKIT_URL` and `VOCA_LIVEKIT_API_*`, not
  `LIVEKIT_*`; `ws_url` is passed to the worker explicitly.
- О7: the agent has a `delegate` stub that raises
  `ToolError("Делегирование сейчас недоступно")`, not «1.1 without delegate».

## References

- `derive:8kki4ynb` — target architecture v4.
- `derive:eb1id854` — detailed design.
- E0 epic research: https://draft.yokeloop.com/artifacts/voca-1-e0-llm-hermes-iim7wlw3
