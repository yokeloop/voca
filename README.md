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

### 0.2 Audio stand

audiocpp_server on the CPU with Parakeet-TDT 0.6B v3 (speech recognition) and Supertonic 3 (speech synthesis); gates G1 and G2. The result is [bench/report_g1_g2.md](bench/report_g1_g2.md).

**audio.cpp.** Release 0.9.4 (`git: f7ac446f 2026-11-04`, `backends: cpu`), build `ubuntu-x64-cpu-portable`: `https://github.com/0xShug0/audio.cpp/releases/download/v0.9.4/audio-v0.9.4-bin-ubuntu-x64-cpu-portable.tar.gz` — sha256 `02756c556d78ccca8040cab6fbc6c7bf57d4f6ed70fe534e8a9e846bab464687`. `scripts/fetch.sh` downloads the archive to `~/Apps/audio.cpp/0.9.4/` and extracts next to it `audiocpp_server` (sha256 `b4c3e9881165ded6fd63b05e8cefcbf7517bb4c364a518e783d51d2f1a29c6dd`), `libggml.so.0`, `libggml-base.so.0` and every `libggml-cpu-*.so` (ggml picks the variant for the CPU at start; the binary finds them through RUNPATH `$ORIGIN`); each file is a `tar:` line in `scripts/SHA256SUMS`. No build from source. `VOCA_AUDIOCPP_BIN` points at `audiocpp_server`.

**Models.** Hugging Face `audio-cpp/audio.cpp-gguf`, revision `6f45df32f8fb283e4cfce3d3e1cb7688986fcbe6`, ready GGUF files (no `audiocpp_gguf` conversion), placed by `scripts/fetch.sh` in `$VOCA_MODELS_DIR/audio-cpp/`:

- `Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-q8_0.gguf` (915 735 760 bytes) — sha256 `6fa498f10f8cb606f041aa9512733cfee842a932359b00b0c45b9b723c4f4f4b`;
- `Supertonic-3-GGUF/supertonic-3-q8_0.gguf` (454 076 228 bytes) — sha256 `9edd05cf92feab49c6b29721d14c86566459dca7f4a6eeb99306372cdce03697`.

**Server config.** `scripts/server.json` is a template; `scripts/audiocpp.sh` renders it with `envsubst '${VOCA_MODELS_DIR}'` into `.local/server.json`, checks the binary, both model files and the port, then `exec`s `audiocpp_server --config .local/server.json --no-ui`. `envsubst` (gettext) and `jq` are required. The config: `127.0.0.1:8082`, `backend: cpu`, `threads: 6` (measured against llama-server decode in the report), `lazy_load: false`, `max_loaded_models: 2`, `ui: false`, two models:

- `asr` — Parakeet, `mode: offline`: `openai.STT` posts each segment whole, without `stream=true`, and offline mode resamples any input rate;
- `tts` — Supertonic, `mode: streaming`: `openai.TTS` always asks for `stream_format: "sse"`, which the server accepts only from a streaming model; `default_voice_preset` `F4` (chosen in G2). Voices are `M1…M5`/`F1…F5`; the plugin default `ash` is rejected. Requests send `language: "ru"`; without it Supertonic reads the text as English.

**Fallback quants.** `scripts/SHA256SUMS` also lists `Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-f16.gguf` (sha256 `1720770887bbb6ae633b3375c6ce4b76b3eaff9cd4efd7b890db734cc0adfe80`) and `Supertonic-3-GGUF/supertonic-3-f16.gguf` (sha256 `7a44c47d1f387e4fb163e2eed9615b58470c77e26ac8b2b1a6be49dfb8670899`) from the same revision; the stand does not load them, the report compares them with q8_0.

**Text before TTS.** Supertonic reads digits badly and does not decline numbers, so the reply text is prepared: the LLM writes numbers as words (one line in the system prompt) and ruaccent 1.5.8.3 (`agent/` dependency, with `transformers` kept below 5: with 5 ruaccent fails on words outside its dictionary) places stress marks (U+0301). Its models are Hugging Face `ruaccent/accentuator` at revision `b78ae5ea1e62beaf138bed1865cd8c3b0b5ca855`, 51 files placed by `scripts/fetch.sh` in `$VOCA_MODELS_DIR/ruaccent/accentuator-b78ae5e/`; `bench/tts_pipeline.py` loads them offline (`HF_HUB_OFFLINE=1`). ruaccent imports its rule engine code (`koziev/`) from its own package directory and otherwise downloads it unpinned, so `scripts/ruaccent-setup.sh` verifies the `koziev/` files of that set against `scripts/SHA256SUMS` and links them into the installed package in `agent/.venv`; it is idempotent and has to run again after `agent/.venv` is recreated.

**Requirements.** x86_64 Linux; no GPU. Both models load before `/health` answers; `scripts/up.sh audio` waits up to 120 s.

**Steps**, from the repository root:

```sh
cp .env.example .env
scripts/fetch.sh
scripts/fetch.sh --check
(cd agent && uv sync --locked)
scripts/ruaccent-setup.sh
scripts/audiocpp.sh --check
scripts/up.sh audio
curl http://127.0.0.1:8082/health
scripts/down.sh audio
```

**Benches** (server up), the gate details are in [bench/README.md](bench/README.md):

```sh
uv run --project agent python bench/stt_ru.py samples
uv run --project agent python bench/stt_ru.py rtf5
uv run --project agent python bench/tts_ru.py check
uv run --project agent python bench/tts_ru.py render
uv run --project agent python bench/tts_ru.py soak --variant ru
uv run --project agent python bench/audio_parallel.py
uv run --project agent python bench/tts_pipeline.py accent
uv run --project agent python bench/tts_pipeline.py run
uv run --project agent python bench/tts_pipeline.py chunks
uv run --project agent python bench/tts_pipeline.py think
```

`bench/tts_pipeline.py run` also needs `scripts/up.sh llm` (0.3). A person records the G1 phrases with `bench/stt_ru.py record` and listens with `bench/tts_ru.py listen`, `ab`, `ab2` and `bench/tts_pipeline.py listen`; recordings and synthesis stay in `.local/bench/`. `ab-render`, `ab`, `ab2-render` and `ab2` compare Supertonic q8_0 and f16 as two models of one server: `jq '.max_loaded_models = 3 | .models += [(.models[1] | .id = "tts-f16" | .path |= sub("q8_0"; "f16"))]' .local/server.json > .local/bench/server-ab.json` and start `$VOCA_AUDIOCPP_BIN --config .local/bench/server-ab.json --no-ui` instead of `scripts/up.sh audio`.

**Variables** (`.env.example`): `VOCA_AUDIOCPP_BIN`, plus `VOCA_APPS_DIR` and `VOCA_MODELS_DIR` from 0.1.

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
