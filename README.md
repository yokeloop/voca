# VOCA

**VOCA v4** is a local voice assistant: a phone or browser talks to it over LiveKit, audio.cpp recognises and speaks, llama-server answers, and longer work is delegated to Hermes.

## Layout

`agent/` is the Python package `voca` (`voca web`), `web/` the page, `bench/` the stand measurements, `scripts/` the stand itself. The full layout, the checks, the stand variables and the download format are in [AGENTS.md](AGENTS.md).

## Воспроизведение с нуля

### 0.1 Repository and launch

**Toolchain.** `mise.toml` pins uv 0.12.23, Node 24.21.0 and pnpm 12.6.0; `mise install` (mise 2026.11.3) installs them. uv installs Python 3.13.16 from `agent/.python-version`.

**livekit-server.** Release v1.13.11, `https://github.com/livekit/livekit/releases/download/v1.13.11/livekit_1.13.11_linux_amd64.tar.gz`. `scripts/fetch.sh` downloads the archive to `~/Apps/livekit/1.13.11/`, extracts `livekit-server` next to it and verifies both against the sha256 in `scripts/SHA256SUMS`; `scripts/fetch.sh --check` verifies without writing. `VOCA_LIVEKIT_BIN` points at the binary. The `SHA256SUMS` format is described in [AGENTS.md](AGENTS.md#downloads).

**Steps**, from the repository root:

```sh
cp .env.example .env
scripts/fetch.sh
scripts/fetch.sh --check
(cd agent && uv sync --locked)
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
scripts/up.sh livekit web
curl http://127.0.0.1:8083/healthz
scripts/check-rtc.sh
scripts/down.sh
```

`scripts/up.sh livekit web` starts `livekit-server --dev --bind 0.0.0.0 --node-ip "$VOCA_LIVEKIT_NODE_IP"` and `voca web`; logs and pids go to `.local/`. `POST /api/token` with `{"device": "<name>"}` returns a LiveKit token for the room `voca` and the URL in `VOCA_PUBLIC_LIVEKIT_URL`. `scripts/check-rtc.sh [page-url]` takes such a token and checks both LiveKit signalling paths the way livekit-client does (see [AGENTS.md](AGENTS.md#stand)); both lines must print `200`.

**Ports.** 7880 TCP (LiveKit HTTP and signalling), 7881 TCP and 7882 UDP (LiveKit media), 8083 TCP (`voca web`, `VOCA_WEB_PORT`).

**Variables** (`.env.example`, defaults in [AGENTS.md](AGENTS.md#stand-variables)): `VOCA_APPS_DIR`, `VOCA_MODELS_DIR`, `VOCA_LIVEKIT_BIN`, `VOCA_LIVEKIT_NODE_IP`, `VOCA_LIVEKIT_API_KEY`, `VOCA_LIVEKIT_API_SECRET`, `VOCA_WEB_HOST`, `VOCA_WEB_PORT`, `VOCA_PUBLIC_LIVEKIT_URL`. The local defaults need no edits.

**Route `voca.priney.com`.** Reachable only inside the owner's tailnet:

- Cloudflare DNS: A record `voca.priney.com` → `100.108.52.92` (the Traefik host in the tailnet), DNS only, not proxied.
- Traefik on that host, TLS by Let's Encrypt: `/` → `http://100.108.140.49:8083` (`voca web`), `/rtc` → `http://100.108.140.49:7880` (LiveKit signalling).
- Media goes directly over the tailnet to `100.108.140.49`: UDP 7882 and TCP 7881.
- In `.env`, uncomment the three values of the `voca.priney.com` block: `VOCA_WEB_HOST=100.108.140.49`, `VOCA_LIVEKIT_NODE_IP=100.108.140.49`, `VOCA_PUBLIC_LIVEKIT_URL=wss://voca.priney.com`.

Check the route with `scripts/check-rtc.sh https://voca.priney.com`. The route holds no secrets. Host and Traefik details live in the owner's deployment documentation.

### 0.3 LLM stand

llama-server with the Bolt-E4B model in profile B (the per-layer embeddings stay in RAM); gates G3 and G4. The result is [bench/report_g3_g4.md](bench/report_g3_g4.md).

**llama.cpp.** CUDA release `b11869` (`build 11869, commit d327bf1f0`), build `ubuntu-cuda-13.4-x64`, two archives:

- `https://github.com/ggml-org/llama.cpp/releases/download/b11869/llama-b11869-bin-ubuntu-cuda-13.4-x64.tar.gz` — sha256 `554f4006f4e2456c4dcc0482a207230602758d3f5a700f55c8754c58f4088a29`;
- `https://github.com/ggml-org/llama.cpp/releases/download/b11869/cudart-llama-b11869-bin-ubuntu-cuda-13.4-x64.tar.gz` — sha256 `d879e8e8070bcfcfd4f8bc01664e52787b2253409b5be4c3dd6cfd7e1409f385`.

`scripts/fetch.sh` downloads both to `~/Apps/llama.cpp/llama-b11869/` and unpacks them there (`untar:` in `scripts/SHA256SUMS`); `llama-server` (sha256 `ad9fa5d2ace5684e506288a3b3e117a9bf0ebcf320e1e0bdeb3bd04da82a628a`) and its llama, ggml and CUDA libraries are verified against `scripts/SHA256SUMS`. `VOCA_LLAMA_BIN` points at `llama-server`.

**Model.** Hugging Face `unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF`, revision `b738967d6da3441a7d1c71fcec15e49e32e16ecd`, file `NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf` (5.06 GB, sha256 `84ff34f5834b929043f5ae74799e8d562d4056998f6a5923ceb44831e8ad84d6`), placed by `scripts/fetch.sh` in `$VOCA_MODELS_DIR/unsloth/`.

**Requirements.** x86_64 Linux, an NVIDIA GPU and a driver with CUDA 13.4 (the stand: RTX 4070 12 GB, driver 615.78.08). llama-server takes about 3.8 GB VRAM and 2.6–5 GB RAM (measured in the report). No nvcc or CUDA toolkit: the CUDA libraries come from the cudart archive.

**Steps**, from the repository root:

```sh
cp .env.example .env
scripts/fetch.sh
scripts/fetch.sh --check
(cd agent && uv sync --locked)
scripts/up.sh llm
curl http://127.0.0.1:8081/health
uv run --project agent python bench/llm_template.py
uv run --project agent python bench/llm_ru_tools.py
uv run --project agent python bench/llm_speed.py
scripts/down.sh llm
```

`scripts/up.sh llm` runs `scripts/llama.sh`, which checks the binary, the model and port 8081, then starts:

```sh
"$VOCA_LLAMA_BIN" -m "$VOCA_MODELS_DIR/unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf" --alias bolt-e4b \
  --jinja --host 127.0.0.1 --port 8081 \
  -c 16384 -fa on -ctk q8_0 -ctv q8_0 \
  --cache-reuse 256 --parallel 1 -t 2 \
  -ngl 99 --override-tensor 'per_layer.*=CPU' \
  --temp 1.0 --top-p 0.95
```

It waits up to 120 s for `/health`. Port 8081 listens on `127.0.0.1` only. The chat template kwargs `enable_thinking=false` and `conversational_mode=true` are sent by the client (`openai.LLM(extra_body={"chat_template_kwargs": …})`); the server sets none.

**Variables** (`.env.example`): `VOCA_LLAMA_BIN`, plus `VOCA_APPS_DIR` and `VOCA_MODELS_DIR` from 0.1.

### 0.4 Hermes preflight

`bench/hermes_runs.py` plays the VOCA agent against the Hermes Runs API and checks gate G7; the result is [bench/report_g7.md](bench/report_g7.md).

**What it needs.**

- The main Hermes profile with its API server on port 8642 (`API_SERVER_ENABLED=true`, `API_SERVER_KEY`, `API_SERVER_HOST`, `API_SERVER_PORT=8642` in the profile's `.env`).
- The entry `https://hermes.priney.com`: Cloudflare DNS-only A record → `100.108.52.92`, Traefik on that host, reachable only inside the owner's tailnet; every request needs the key.
- MCP mypi in that profile (`mcp_servers.mypi`, `http://100.108.140.49:8737/mcp`) with a mypi write token, for `delegate`.

**Variables** (`.env.example`):

- `VOCA_HERMES_URL` — `https://hermes.priney.com`.
- `VOCA_HERMES_KEY` — the `API_SERVER_KEY` from the Hermes profile's `.env`; it stays in your `.env` and is never committed.

**Steps**, from the repository root:

```sh
cp .env.example .env
# set VOCA_HERMES_URL and VOCA_HERMES_KEY in .env
(cd agent && uv sync --locked)
set -a; . ./.env; set +a
uv run --project agent python bench/hermes_runs.py
```

Without a subcommand the script runs `all`: `preflight`, `create`, `resume` (a separate process, the agent restarted while the run is running), `conflict`, `session`; each prints `PASS <phase>` or `FAIL <phase>`, and the exit code is 0 only when all pass.

These subcommands change state outside the repository and need the owner's permission:

- `delegate` — Hermes writes a journal note to the mypi task VOCA-3 through MCP mypi.
- `restart --phase start`, then a gateway restart, then `restart --phase verify` — interrupts the Hermes gateway and every session it serves.
- `backend-down [--deadline 600]` — needs a temporary provider `voca-unreachable` in the profile's `config.yaml`, removed afterwards.

Phase logs (requests without keys, responses, SSE events) go to `.local/bench/hermes/`. Host, Hermes profile and Traefik details live in the owner's deployment documentation.

## Contributing

Open an issue or a pull request. Run the checks from [AGENTS.md](AGENTS.md#checks) before sending a change.

## License

MIT, see [LICENSE](LICENSE).
