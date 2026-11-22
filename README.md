# VOCA

**VOCA v4** is a local voice assistant: a phone or browser talks to it over LiveKit, audio.cpp recognises and speaks, llama-server answers, and longer work is delegated to Hermes.

## Layout

`agent/` is the Python package `voca` (`voca web`, `voca agent`), `web/` the page, `bench/` the stand measurements, `scripts/` the stand itself. The full layout, the checks, the stand variables and the download format are in [AGENTS.md](AGENTS.md).

## Воспроизведение с нуля

### 0.1 Repository and launch

**Toolchain.** `mise.toml` pins uv 0.12.23, Node 24.21.0 and pnpm 12.6.0; `mise install` (mise 2026.11.3) installs them. If mise reports that `mise.toml` is not trusted on a new path, run `mise trust` in the clone first. uv installs Python 3.13.16 from `agent/.python-version`.

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

**G2 result.** Voice `F4`, Supertonic q8_0, the server's 8 inference steps, `language: "ru"`; f16 and 16 steps sounded no different. G2 passes on condition that the text is prepared before TTS. The wiring for 1.1 (two LLM turns: A without thinking, facts or numbers; B with thinking and numbers as words, then stress marks; `tools` always passed) is in [bench/report_g1_g2.md](bench/report_g1_g2.md), section 6.

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

`bench/tts_pipeline.py run` also needs `scripts/up.sh llm` (0.3). A person records the G1 phrases with `bench/stt_ru.py record` and listens with `bench/tts_ru.py listen`, `ab`, `ab2` and `bench/tts_pipeline.py listen`; recordings and synthesis stay in `.local/bench/`. `ab-render`, `ab`, `ab2-render`, `ab2` and `voiceprobe` use Supertonic q8_0 and f16 as two models of one server: `jq '.max_loaded_models = 3 | .models += [(.models[1] | .id = "tts-f16" | .path |= sub("q8_0"; "f16"))]' .local/server.json > .local/bench/server-ab.json` and start `$VOCA_AUDIOCPP_BIN --config .local/bench/server-ab.json --no-ui` instead of `scripts/up.sh audio`.

**Variables** (`.env.example`): `VOCA_AUDIOCPP_BIN`, plus `VOCA_APPS_DIR` and `VOCA_MODELS_DIR` from 0.1.

### 0.3 LLM stand

llama-server with the agent's model Nemotron Bolt-E4B (profile `bolt-e4b`, the default since VOCA-36, 0.3.1); LFM2.5-2.6B (`lfm2.5-2.6b`) is an alternative. Gates G3 and G4 were measured on Bolt-E4B in profile B (the per-layer embeddings stay in RAM) on b11869: [bench/report_g3_g4.md](bench/report_g3_g4.md). LFM2.5-2.6B on the E0 cases: [bench/report_lfm2_5_2_6b.md](bench/report_lfm2_5_2_6b.md).

**llama.cpp.** CUDA release `b12167` (`build 12167, commit 240d68fcf`), build `ubuntu-cuda-13.4-x64`, two archives:

- `https://github.com/ggml-org/llama.cpp/releases/download/b12167/llama-b12167-bin-ubuntu-cuda-13.4-x64.tar.gz` — sha256 `adf9d677741afd3bcf1f8debea51926b0341cb94c2758afa727cb3a38f0b2ac4`;
- `https://github.com/ggml-org/llama.cpp/releases/download/b12167/cudart-llama-b12167-bin-ubuntu-cuda-13.4-x64.tar.gz` — sha256 `db0c22114db5751a12ff1c970ceed6e805fb0dbde12f57ca7ddc28414079b0f5`.

`scripts/fetch.sh` downloads both to `~/Apps/llama.cpp/llama-b12167/` and unpacks them there (`untar:` in `scripts/SHA256SUMS`); `llama-server` (sha256 `5f542c73d6e2604d6e070d1435d33e39354da60c0aba36ce852cee0c3055d6be`) and its llama, ggml and CUDA libraries are verified against `scripts/SHA256SUMS`. `VOCA_LLAMA_BIN` points at `llama-server`.

`b11869` (`build 11869, commit d327bf1f0`; `llama-b11869-bin-ubuntu-cuda-13.4-x64.tar.gz` sha256 `554f4006f4e2456c4dcc0482a207230602758d3f5a700f55c8754c58f4088a29`, `cudart-llama-b11869-bin-ubuntu-cuda-13.4-x64.tar.gz` sha256 `d879e8e8070bcfcfd4f8bc01664e52787b2253409b5be4c3dd6cfd7e1409f385`, `llama-server` sha256 `ad9fa5d2ace5684e506288a3b3e117a9bf0ebcf320e1e0bdeb3bd04da82a628a`, same release URLs with `b11869`) is the build of the VOCA-4/5 measurements; it stays in `scripts/SHA256SUMS`, the stand no longer runs it.

**Model.** Hugging Face `LiquidAI/LFM2.5-2.6B-GGUF`, revision `e7caca5d835a3901a8e0d63e94009429bafafdfc`, file `LFM2.5-2.6B-Q4_K_M.gguf` (1 674 455 040 bytes, sha256 `02a8b7e17487d326e46d68ce0ba24211e1b80a14c4cd0597fa73c1cd697f52ed`), placed by `scripts/fetch.sh` in `$VOCA_MODELS_DIR/LiquidAI/`. `scripts/fetch.sh` also places the models of the alternative profiles `bolt-e4b` and `lfm3-1.7b` (0.3.1).

**Requirements.** x86_64 Linux, an NVIDIA GPU and a driver with CUDA 13.4 (the stand: RTX 4070 12 GB, driver 615.78.08). llama-server with LFM2.5-2.6B takes about 2.0 GB VRAM and 1.2–1.6 GB RAM ([bench/report_lfm2_5_2_6b.md](bench/report_lfm2_5_2_6b.md), section 7). Bolt-E4B, if started, takes about 3.8 GB VRAM and 2.6–5 GB RAM ([bench/report_g3_g4.md](bench/report_g3_g4.md)). No nvcc or CUDA toolkit: the CUDA libraries come from the cudart archive.

**Steps**, from the repository root:

```sh
cp .env.example .env
scripts/fetch.sh
scripts/fetch.sh --check
(cd agent && uv sync --locked)
scripts/up.sh llm
curl http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/v1/models   # "id": "bolt-e4b"
```

These measurements belong to `bolt-e4b`; the LFM2.5-2.6B runs and their commands are in [bench/report_lfm2_5_2_6b.md](bench/report_lfm2_5_2_6b.md). Repeating G3/G4 of [bench/report_g3_g4.md](bench/report_g3_g4.md) on `bolt-e4b`:

```sh
scripts/down.sh llm
scripts/up.sh llm
uv run --project agent python bench/llm_template.py
uv run --project agent python bench/llm_ru_tools.py
uv run --project agent python bench/llm_speed.py
scripts/down.sh llm
```

`scripts/up.sh llm` runs `scripts/llama.sh`, which loads the profile `VOCA_LLM_PROFILE` (0.3.1), checks the binary, the model and port 8081, then starts; for the default profile `bolt-e4b`:

```sh
~/Apps/llama.cpp/llama-b12167/llama-server -m ~/models/unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf --alias bolt-e4b \
  --jinja --host 127.0.0.1 --port 8081 \
  -c 16384 -fa on -ctk q8_0 -ctv q8_0 \
  --parallel 1 -t 2 -ngl 99 \
  --cache-reuse 256 --override-tensor 'per_layer.*=CPU' --temp 1.0 --top-p 0.95 \
  --reasoning-budget-message $'\n\nTime is up. I stop thinking and give the user the final answer now, in Russian, without my reasoning.\n'
```

It waits up to 120 s for `/health`. Port 8081 listens on `127.0.0.1` only. The client sends `thinking_budget_tokens` (`[model].thinking_budget`, 128) in `extra_body` and no chat template kwargs. Sampling comes from the profile on the server; the client sends only the sampling values set in `[model]` of the agent config (`temperature`, `top_k`, `top_p`, `repeat_penalty`; [AGENTS.md](AGENTS.md#agent-config)). LFM2.5-2.6B always thinks: its template opens `<think>` and does not read `enable_thinking` ([bench/report_lfm2_5_2_6b.md](bench/report_lfm2_5_2_6b.md)).

**Variables** (`.env.example`): `VOCA_LLAMA_BIN`, `VOCA_LLM_PROFILE`, plus `VOCA_APPS_DIR` and `VOCA_MODELS_DIR` from 0.1.

### 0.3.1 LLM profiles

`VOCA_LLM_PROFILE` (default `bolt-e4b`, set in `.env` or the environment) picks the model llama-server loads. A profile is the bash file `scripts/llm-profiles/<name>.sh` with two variables: `model` (the GGUF path) and `args` (the profile's own flags: sampling from the model card and special flags). The shared flags (`--jinja`, `127.0.0.1:8081`, `-c 16384`, `-fa on`, q8_0 KV cache, `--parallel 1`, `-t 2`, `-ngl 99`) stay in `scripts/llama.sh`, and `--alias` is the profile name, so `GET /v1/models` names the profile. An unknown profile stops `scripts/llama.sh` with «профиль VOCA_LLM_PROFILE=<name> не найден» and nothing starts.

`bolt-e4b` (Nemotron) is the agent's model: the engineer's decision of 2026-11-17 (VOCA-36, the E3 acceptance) recorded in mypi home `projects/yokeloop/voca/INDEX.md` («Решения инженера по стеку»); it revises the decision of 2026-11-11, which made `lfm2.5-2.6b` the agent's model (measurements in [bench/report_lfm2_5_2_6b.md](bench/report_lfm2_5_2_6b.md)); the architecture documents derive:8kki4ynb and derive:eb1id854 are updated separately. `lfm2.5-2.6b` and `lfm3-1.7b` remain alternatives and start only with an explicit `VOCA_LLM_PROFILE=<name>`.

| Profile | Model | Sampling (model card) | Special flags |
| --- | --- | --- | --- |
| `bolt-e4b` (default, the agent's model) | HF `unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF`, revision `b738967d6da3441a7d1c71fcec15e49e32e16ecd`, `NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf`, 5.06 GB, sha256 `84ff34f5834b929043f5ae74799e8d562d4056998f6a5923ceb44831e8ad84d6` | `--temp 1.0 --top-p 0.95` | `--cache-reuse 256`, `--override-tensor 'per_layer.*=CPU'` (profile B), `--reasoning-budget-message` (when `thinking_budget_tokens` runs out, llama-server closes the thought channel by force; without the message the model goes on reasoning in the answer, and the reasoning is spoken — VOCA-36 voice check 2) |
| `lfm3-1.7b` | HF `LiquidAI/LFM3-1.7B-GGUF`, revision `cd0c74c575b5c416f3011b5b4c298077f26ca8c2`, `LFM3-1.7B-Q4_K_M.gguf`, 1 099 627 808 bytes, sha256 `3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4` | General preset: `--temp 0.1 --top-k 50 --repeat-penalty 1.1` | none: no `--cache-reuse` (unsupported by this context, checkpoints are used), no `per_layer` override; ≈ 1.4 GB VRAM |
| `lfm2.5-2.6b` (alternative) | HF `LiquidAI/LFM2.5-2.6B-GGUF`, revision `e7caca5d835a3901a8e0d63e94009429bafafdfc`, `LFM2.5-2.6B-Q4_K_M.gguf`, 1 674 455 040 bytes, sha256 `02a8b7e17487d326e46d68ce0ba24211e1b80a14c4cd0597fa73c1cd697f52ed` | card: `--temp 0.1 --top-k 50 --repeat-penalty 1.1` | none: no `--cache-reuse` (shifted prefixes are not reused; the prefix cache is), no `per_layer` override; always thinks; ≈ 2.0 GB VRAM |

`scripts/fetch.sh` places the models of all profiles in `$VOCA_MODELS_DIR` (`unsloth/`, `LiquidAI/`).

**Switching the model**, from the repository root:

```sh
scripts/down.sh llm
VOCA_LLM_PROFILE=lfm2.5-2.6b scripts/up.sh llm   # or VOCA_LLM_PROFILE=lfm2.5-2.6b in .env
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/v1/models          # "id": "lfm2.5-2.6b"
```

`scripts/up.sh llm` without `VOCA_LLM_PROFILE` goes back to `bolt-e4b`.

Exactly one model is loaded at a time: there is one `llm` service on one port, 8081.

**Adding a profile:** a file `scripts/llm-profiles/<name>.sh` with `model` and `args`, plus the GGUF line in `scripts/SHA256SUMS`.

The runs on the E0 cases: LFM3-1.7B — [bench/report_lfm3_1_7b.md](bench/report_lfm3_1_7b.md), LFM2.5-2.6B — [bench/report_lfm2_5_2_6b.md](bench/report_lfm2_5_2_6b.md).

### 0.4 Hermes preflight

`bench/hermes_runs.py` plays the VOCA agent against the Hermes Runs API and checks gate G7; the result is [bench/report_g7.md](bench/report_g7.md).

**What it needs.**

- The main Hermes profile with its API server on port 8642 (`API_SERVER_ENABLED=true`, `API_SERVER_KEY`, `API_SERVER_HOST`, `API_SERVER_PORT=8642` in the profile's `.env`).
- The entry `https://hermes.priney.com`: Cloudflare DNS-only A record → `100.108.52.92`, Traefik on that host, reachable only inside the owner's tailnet; every request needs the key.
- MCP mypi in that profile (`mcp_servers.mypi`, `http://100.108.140.49:8737/mcp`) with a mypi write token, for `delegate`.

**Variables** (`.env.example`):

- `VOCA_HERMES_URL` — `https://hermes.priney.com`.
- `VOCA_HERMES_KEY` — the `API_SERVER_KEY` from the Hermes profile's `.env`; it stays in your `.env` and is never committed.

Since VOCA-35 the stand's `.env` points at the Hermes profile `voca` ([1.3](#13-delegation)); to rerun G7 against the main profile, set these two variables to the main profile's URL and key.

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

### 0.5 LiteLLM route

**What it is for.** The agent's session summariser (E2) calls a cloud model, `glm-5.3-flash`, through the owner's LiteLLM proxy. It does not use the local llama-server.

**What it needs.**

- The entry `https://litellm.priney.com`: a Cloudflare DNS-only A record → `100.108.52.92`, and Traefik on that host with a dynamic file that routes the name to the LiteLLM proxy. TLS comes from Traefik's existing DNS-01 resolver. The entry is reachable only inside the owner's tailnet.
- A separate LiteLLM virtual key with `key_alias` `voca`. It can be revoked without touching other clients.
- Every API request (`/v1/...`) needs the key; `/health/liveliness` answers without it.

**Variables** (`.env.example`):

- `VOCA_LITELLM_KEY` — the virtual key; it stays in your `.env` and is never committed.

**Checks**, from the repository root, without printing the key:

```sh
set -a; . ./.env; set +a
getent hosts litellm.priney.com
curl -fsS -H "Authorization: Bearer $VOCA_LITELLM_KEY" https://litellm.priney.com/v1/models | jq -r '.data[].id' | grep -x glm-5.3-flash
curl -s -o /dev/null -w '%{http_code}\n' https://litellm.priney.com/v1/models
```

The first `curl` prints `glm-5.3-flash`, the second prints `401`.

Host, LiteLLM and Traefik details, and how to revoke the key, live in the owner's deployment documentation.

### 1.1 Agent

The `agent` service is the voice agent: a LiveKit worker (`voca agent start`) that joins the room `voca` when a participant enters and talks in Russian — Silero VAD, STT on audio.cpp, the LLM on llama-server with the `delegate` tool (gives a task to Hermes, [1.3](#13-delegation)), text preparation (numbers, stress, the first short fragment) and Supertonic TTS with voice F4. It needs the whole stand: LiveKit, audio.cpp ([0.2](#02-audio-stand)), llama-server ([0.3](#03-llm-stand)) and the page ([0.1](#01-repository-and-launch)).

**Steps**, from the repository root:

```sh
(cd agent && uv sync --locked)
scripts/ruaccent-setup.sh
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
mkdir -p ~/.voca/instructions && cp instructions.example/*.md ~/.voca/instructions/
cp -n config.example.toml ~/.voca/config.toml
scripts/up.sh
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8084/
scripts/down.sh
```

`scripts/ruaccent-setup.sh` is needed once per `agent/.venv`: the agent loads ruaccent in prewarm. `scripts/up.sh` without arguments starts `livekit audio llm decision web agent` in that order; `agent` is up when `http://127.0.0.1:8084/` answers `200` (503 until the worker has registered with livekit-server). `scripts/down.sh` stops `agent` first.

**Talking.** Open `http://127.0.0.1:8083/?device=host`, press «Подключиться» and allow the microphone. The page publishes the microphone with echo cancellation, noise suppression and auto gain, plays the agent, shows the transcript (`вы`/`агент`) and the state («слушает», «думает», «говорит»). «Отключиться» leaves the room. The agent stays in it until livekit-server closes the empty room (`departure_timeout`, 20 s): «Подключиться» within that window returns to the same job and conversation; later the room closes and the next «Подключиться» gets a new job, which lifts the conversation from the session store (`~/.voca/sessions.sqlite3`, [AGENTS.md](AGENTS.md#session-store)): the agent remembers what was said; on every join the page shows the session's last replies (`[session].history_on_join`, 50 by default) and the compaction markers «— контекст сжат —» from the agent's `voca.history` message, then the new ones.

**Port.** 8084 TCP, the worker's health endpoint (127.0.0.1, `VOCA_AGENT_PORT`).

**Decision service.** `decision` is the decision model of the reply sorter and the questions of the approval judge ([1.3](#13-delegation)): LiquidAI d1-2B (`d1-2B-Q4_K_M.gguf`, `scripts/SHA256SUMS`, downloaded by `scripts/fetch.sh`) on a second llama-server (`scripts/decision.sh`), `127.0.0.1:8086`, `POST /v1/systemone`. It runs on the GPU by default (`VOCA_DECISION_NGL`, 99; ~1.3 GB VRAM next to the LLM); `VOCA_DECISION_NGL=0` runs it on the CPU, where one answer takes ~4–5 s ([bench/report_decision.md](bench/report_decision.md)). `scripts/up.sh decision` starts it (ready on `/health`), `scripts/down.sh decision` stops it. The agent reaches it at `[decision].url` (`VOCA_DECISION__URL`); without it nothing is approved or denied by voice, the buttons answer. On every user reply it answers whether the reply is noise or a reply, and while approval requests wait, how the reply relates to them.

**Sorter and glue.** While the agent speaks (its current speech has sounded and is not done and not interrupted, a VAD pause included) the sorter stands in `stt_node`: a final transcript judged noise is not passed on, no turn starts, and the paused speech resumes after livekit's `false_interruption_timeout` (2 s, not a VOCA setting). Before the reply's first sound (thinking) a final transcript passes `stt_node` unjudged, so a continuation of an unanswered fragment can be glued. In silence, and for such a transcript, the sorter is asked in `on_user_turn_completed`, and noise ends the turn with `StopResponse`; a reply already judged in `stt_node` is not asked again, and a reply that glues to a fragment is never dropped as noise. Only a reply of at most `[decision].sorter_max_words` (4) words with none of `sorter_keep_words` (нет, не, ни, стоп, да, давай) is dropped as noise. When the sorter fails or times out (`sorter_timeout_s`, 0.3 s) while the agent speaks, the length threshold decides: a reply of fewer than `sorter_min_words` (2) words without a keep word is dropped; in silence a failure gives an ordinary turn. With `[decision].enabled = false` there is no sorter and no filter. A user fragment the agent did not answer aloud (its reply was interrupted before the first sentence ended) is glued to the next reply when the gap is within `[agent].glue_window_s` (3.0 s): the cancelled turn leaves the context and stays only in the archive. `delegate` waits `[agent].action_silence_s` (0.5 s) of user silence, counted from the end of speech or the last final transcript, whichever is later; when the user spoke again after the last final transcript, it first waits for that speech's final transcript (STT gives it ~0.7–0.9 s after the end of speech), at most 3 s after the end of speech. If the turn is interrupted meanwhile, the task is not sent and the tool answers `"event": "not_sent"`. Measured on the VOCA-46 stand (the 12 questions of the g6 corpus, `bench/report.py`), `[agent].endpointing_min_delay` 0.2 with `preemptive_generation = true` cut the warm `e2e_latency_ms` p50 from 2700 to 1656 ms and p95 from 5066 to 3515 ms against 0.5 and false, so these are the defaults now (in the 0.5/false run the paused reply was not glued because of the `delegate` hold race this task fixed).

**Variables** (`.env.example`, defaults in [AGENTS.md](AGENTS.md#stand-variables)): `VOCA_LIVEKIT_URL`, `VOCA_AUDIO_URL`, `VOCA_LLM_URL`, `VOCA_AGENT_PORT`, `VOCA_STATE_DIR`; the agent's behaviour lives in `~/.voca/config.toml` (`VOCA_CONFIG`, sample `config.example.toml`, [AGENTS.md](AGENTS.md#agent-config)); a one-off override is `VOCA_<SECTION>__<FIELD>`; a broken file stops `scripts/up.sh agent` with the line `конфиг не прочитан`.

**Events.** The agent appends to `.local/events.jsonl` (`VOCA_STATE_DIR`), one JSON object per line with `ts`, `session` (the job id), `turn` (the user's turn number), `device` and `kind`: `user_input_transcribed`, `agent_state`, `user_state`, `metrics`, `false_interruption`, `interruption`, `error` (`component`, `recoverable`, `message`), `instructions`, `context_trimmed`, `context_mismatch` (`n_ctx`, `context_tokens`: llama-server `-c` differs from `[model].context_tokens`), `session_restored` (`session_id`, `items`, `estimate`), `session_write_failed` (`error`), `compaction` (`trigger`: `threshold`, `idle` or `daily`, `estimate_before`, `after`, `folded_turns`, `ms`, `summary_tokens`), `compaction_failed` (`trigger`, `reason`, `estimate_before`, `ms`; `error` — the exception class, only when the summary task raised), `compaction_blocked` (`strikes`, `estimate`, `threshold`, `retry_in_s`: compaction is paused after two adoptions that left the estimate above the threshold), `task` (delegation, [1.3](#13-delegation)), `sorter` (`branch`: `stt`, `hook` or `judge`, `text`, `speaking`, `choice` (`noise` or `reply`) and `confidence`, or `fallback` — the failure reason, `ms`, `dropped`), `glue` (`removed`, `fragment`, `gap_s`), `close` and one `turn` row per agent reply. A `turn` row carries `user_text`, `text`, `e2e_latency_ms`, `first_audio_ms`, `end_of_turn_delay_ms`, `transcription_delay_ms`, `stt_ms`, `llm_ttft_ms`, `first_sentence_ms`, `tts_ttfb_ms`, `tokens_in`, `ctx_size`, `cached_tokens`, `cache_hit`, `first_fragment`, `first_fragment_chars`, `prepare_ms`, `leftover_digits`, `markdown_removed`, `interrupted`, `started_speaking_at`, `stopped_speaking_at` and `cold`. The worker log is `.local/logs/agent.log`.

**G5 (barge-in on speakers).** Needs the speakers as the default output, the host's microphone and PipeWire with `pw-record`, `pw-link` and `pactl`. Per variant: `scripts/down.sh agent`, `VOCA_AGENT__INTERRUPTION_MIN_DURATION=<x> VOCA_AGENT__AEC_WARMUP=<y> scripts/up.sh agent`, then «Отключиться» and «Подключиться» on the page; `uv run --project agent python bench/barge_in.py record --run <run>` runs for the whole run, then `analyze`, `false` and `history` (`bench/README.md`). G5 is prepared but not run: the stand has no speakers, only a headset (the engineer's decision of 2026-11-13: «Отложить G5 до колонок»); the thresholds stay the `Settings` defaults (`[agent]` `interruption_min_duration` 0.5, `interruption_min_words` 0, `aec_warmup` 3.0). The status, the method and the runbook for the future run are in [bench/report_g5.md](bench/report_g5.md).

### 1.2 Product pipeline

**Instructions.** The agent reads `SOUL.md`, `user.md` and `agent.md` from `VOCA_INSTRUCTIONS_DIR` (default `~/.voca/instructions`, a copy of `instructions.example/`) and joins them with an empty line. The set is read at start and before every turn. A set is invalid when a file is missing, is not UTF-8 or is empty. At start an invalid set stops the agent (`scripts/up.sh agent` prints the line `инструкции не загружены`). Later the agent keeps the last valid set: an `instructions` row with `source` `last_valid` and `error` in JSONL, «инструкции: прошлый рабочий набор — …» on the page. There is no length limit.

**Context.** Above `[compression].threshold · [model].context_tokens` (0.60 · 16384 = 9830) estimated tokens (`len // 3`), after the agent's reply, the cloud summariser (`[compression.model]`, `glm-5.3-flash` through LiteLLM, `VOCA_LITELLM_KEY`, [README 0.5](#05-litellm-route)) retells the older turns in the background; the next turn adopts the summary in place of them and keeps the recent turns (`tail_ratio`, `tail_turns_min`), a `compaction` row is written, and that turn pays a full prefill once. The same summary also starts after `[compression].idle_hours` (4) hours without messages and once a day after `[compression].daily_at` (`04:00`, the host's local time); each is checked when a page joins and at every reply — there is no timer — and is adopted one reply later; `0` and `""` turn them off ([AGENTS.md](AGENTS.md#context-compaction)). The turn never waits for the summariser. A failed or keyless summary leaves the context as it was and writes `compaction_failed`; above `hard_limit · context_tokens` (13926) the oldest turns are still dropped until `trim_to · context_tokens` (6553) remain, with a `context_trimmed` row. Summaries are archived as `summary` rows ([AGENTS.md](AGENTS.md#context-compaction)).

**Failures.** What the user hears and sees:

- STT — «не расслышал»; LLM — «не могу ответить сейчас»; TTS — no sound, only the indicator `TTS: down`; while TTS is `down` no phrases are spoken;
- a JSONL `error` row (`component`, `recoverable`, `message`);
- retries: STT and TTS 1, LLM 0, connection timeout 5 s;
- a component returns to `ok` after its first successful call;
- a series of failures does not close the session;
- audio.cpp serves both STT and TTS, so `scripts/down.sh audio` takes both down.

**Page.** The indicator `STT · LLM · TTS · инструкции`, the error line, the `voca.cards` (`state`, `task`, `tasks`) and `voca.history` channels and the task cards ([AGENTS.md](AGENTS.md#client-channel)). After a disconnect the user did not ask for (a livekit restart, the network) the page reconnects by itself: after 1, 2, 4, 8, then every 15 s, until «Отключиться» is pressed. The page does not reconnect when the server removes the participant or the same device joins from another tab (`DUPLICATE_IDENTITY`, `PARTICIPANT_REMOVED`); the error line then shows «отключено сервером: <reason>». On load the page waits for «Подключиться».

**Agent restart.** Known limit: after `scripts/down.sh agent` / `scripts/up.sh agent` while the page stays in the room the agent does not come back by itself. Press «Отключиться», wait up to 20 s for the room to close, then «Подключиться». The same limit and recovery apply when livekit-server is down long enough that the page rejoins before the worker registers again (for example a 30 s outage). The conversation survives the restart: the new job lifts it from the session store. `agent/.venv/bin/voca archive search <query>` searches every reply in it (FTS5 syntax, `word*` for other word forms).

**Events.** New `kind`s: `instructions`, `context_trimmed`, `client` (`seq`, `client_e2e_ms`, `speech_ms`), `compaction`, `compaction_failed`, `compaction_blocked`; new fields of `error`.

**Phone.** The phone must be in the tailnet: the Tailscale app is on and signed in to the same tailnet; a cellular network works. The stand runs with the three values of the `voca.priney.com` block in `.env` ([0.1](#01-repository-and-launch), «Route `voca.priney.com`»). On the phone open `https://voca.priney.com/?device=phone`, press «Подключиться» and allow the microphone: HTTPS gives the browser a secure context. The room `voca` takes one page at a time: press «Отключиться» before connecting from another device. Signaling goes through Traefik (`/rtc`), media go directly over the tailnet to `VOCA_LIVEKIT_NODE_IP`: UDP 7882; without UDP livekit-client falls back to TCP 7881 on the same address, and the route does not change. `.local/logs/livekit.log` shows what was used (`connectionType`, `selected ICE candidate pair`); Chrome on a tailnet Mac was seen on TCP 7881 over IPv6 although UDP 7882 was offered ([bench/report_g6.md](bench/report_g6.md) § 12). If the page does not connect, check Tailscale on the phone and `scripts/check-rtc.sh https://voca.priney.com`.

**G6 (end-to-end latency).** The corpus is `bench/corpus/g6_turns.txt`, 5 sessions of 6 turns per device. A new job: «Отключиться», wait for the `close` row in `.local/events.jsonl` (the room closes after 20 s), «Подключиться»; the first turn of a session is cold. The run window: `uv run --project agent python bench/report.py start --run <run>` and `… stop --run <run>`; the summary: `… summary --run <run> …`. A lever: `scripts/down.sh agent`, then `VOCA_AGENT__ENDPOINTING_MIN_DELAY=<x>` or `VOCA_AGENT__PREEMPTIVE_GENERATION=true` before `scripts/up.sh agent`, then reconnect. The page measures `client_e2e_ms` itself (end of speech on the microphone → first agent sound in the browser, sent on `voca.metrics`, [AGENTS.md](AGENTS.md#client-channel) «Client channel»); `summary` puts it next to `e2e_latency_ms`; the difference is the network, the buffer and decoding. Sound after WebAudio (the output device latency) is not included. The result is in [bench/report_g6.md](bench/report_g6.md): G6 is not passed, bottleneck TTS TTFB — one run of 13 turns from a remote Mac in the tailnet (the engineer cut the host, phone and lever runs), `e2e_latency_ms` p50 2855 / p95 3738 ms, `client_e2e_ms` p50 3635 / p95 4681 ms, `tts_ttfb_ms` p50 938 ms.

### 1.3 Delegation

**What it is.** VOCA gives a task to Hermes through the Runs API (`POST /v1/runs` with `Idempotency-Key` = the task key, `X-Hermes-Session-Key: voca`, `session_id` = the task key). It polls `GET /v1/runs/{id}` every 2 s. The task is written before the request is sent, so a repeated key gives the same task and no second run. The voice tool `delegate` and `voca bench delegate` drive the core.

**Needs.** `VOCA_HERMES_URL` = `https://hermes-voca.priney.com` and `VOCA_HERMES_KEY` = `API_SERVER_KEY` of the Hermes profile `voca`, in the environment; `set -a; . ./.env; set +a` before the command.

**Hermes profile `voca`.** Delegation goes to a separate Hermes profile `voca`: an empty profile, not a clone, with its own gateway (a user service) and API server on port 8643, its own `API_SERVER_KEY`, behind `https://hermes-voca.priney.com` (Cloudflare DNS-only A record, Traefik, tailnet-only, like [0.4](#04-hermes-preflight)). Its `config.yaml` sets `approvals.mode: manual` (the default `smart` lets an LLM guardian approve by itself), `approvals.unattended_mode: deny`, `approvals.timeout: 300`, `command_allowlist: []`, `terminal.backend: local`, and only the MCP server `mypi` (its own write token). The model and its provider are the main profile's. Secrets (the provider key, the mypi token, the API key) are in the profile's `.env` and referenced as `${env:…}`. Why: the main profile's `command_allowlist` holds a permanent «recursive delete» approval, so `rm -r` ran there without asking (the VOCA-31 stand below). `/v1/models` of this profile lists the model id `voca`. Host, profile and Traefik details, and how to remove the profile, live in the owner's deployment documentation.

**CLI.** `agent/.venv/bin/voca bench delegate "<text>"` — a new key `cli-<uuid>`, printed first, then every state change (`<key> <status> <run_id>`), then `result:` or `error:`. Other forms:

- `--key K "<text>"` — the same task again;
- `--recover` — carries every open task to its end and lists the undelivered ones; only `cli-*` tasks are marked delivered;
- `--timeout S` — a deadline for this run;
- `--approve once|deny --key K` — answers a pending approval.

Exit codes: 0 succeeded, 1 failed or unavailable, 2 usage, config or missing variables, 130 interrupted.

**States.** `queued` → `running` → `succeeded` | `failed`, plus `waiting_approval` (Hermes asks to approve a dangerous command: `description`, `command`, `choices` are printed) and `unknown` (Hermes canonical mode; VOCA calls `resolve-unknown`). Failure reasons: `unavailable` (the request was refused: no connection, 4xx, 429, 503; an unknown outcome — a read timeout, a cut connection, 5xx other than 503 — is retried once with the same key), `timeout` (`[delegation].run_timeout` passed: VOCA sends `/stop`, waits 30 s for a final status; `completed` within that time still counts), `cancelled` («отменено пользователем»: `cancelled` after a VOCA `/stop` on the user's request — the tool `cancel_task` or the card's button), `interrupted by gateway restart` (`cancelled` without a VOCA `/stop`), `lost` (404), `lost before launch`. The column `stop_reason` (`user`|`timeout`) of `tasks` says why VOCA sent `/stop`, so a restart still maps `cancelled` to the right reason; schema migration 1 → 2 adds it and sets `timeout` on rows already stopped.

**Approval in `session_authority` mode.** Hermes in canonical `session_authority` mode (the `voca` profile, Hermes `3cbe7b1`) returns `approval: null` and puts the request into `pending_controls` (`kind: approval`, `prompt_id`, `execution_generation`); VOCA takes the first such entry and answers `POST /v1/runs/{id}/approval` with exactly `{"request_id": <prompt_id>, "execution_generation": <int>, "choice": "once"|"deny"}`; without `pending_controls` it reads `approval` and sends `{"choice", "request_id"}` as before.

**Task file.** `[delegation].path`, default `~/.voca/tasks.sqlite3` (`VOCA_DELEGATION__PATH`), tables `tasks` and `events` (schema 3); details in [AGENTS.md](AGENTS.md#agent-config). An `events` row is an approval request, a result or a failure of a task, with `state` (`arrived` | `mentioned` | `essence` | `closed`; a request goes `arrived` → `mentioned` (its mention played to the end) → `essence` (the first sentence of the `open_approval` reply played) → `closed`; a result or failure goes `arrived` → `closed`), `created_at` (the arrival time) and `danger` on a request (written only on arrival, by the rule); there is one event per task, kind and `request_id`; a request event is closed (`resolved`) when the task leaves that request, a result event when the task gets `delivered_at`.

**Voice.**

- `delegate` puts the task with the key `tool_call_id`, and the agent itself says at once «Поручил Hermes: «<task text, up to 300 characters>». Сообщу результат.», so the user hears what was sent (a misheard name can be corrected right away). The phrase goes into neither the context nor the archive: the model sees only the `delegate` call and its output, otherwise it copies the phrase into its own replies and the user hears it twice. The tool output is `{"id", "event": "submitted"}` with `reply_required=False`, so the model says nothing of its own on that turn; if the acknowledgement could not be scheduled (no session, or the session is closing), the agent logs a warning and the model is asked to reply instead; if Hermes is not configured (`VOCA_HERMES_URL`/`VOCA_HERMES_KEY`), the task file did not open or Hermes refused, it answers «Делегирование сейчас недоступно», and the refusal is not spoken again.
- The main model speaks of results and failures in its own words: the agent calls `generate_reply` with a one-turn service record (a fixed text and the JSON of the events in the form of `list_notifications`, `tool_choice="none"`). The record goes into neither the context, nor the archive, nor the snapshot; the reply that played goes into the context, the archive and `voca.history` like any answer. All waiting results and failures go into one record and are mentioned in one phrase; the full text is in the card.
- Whatever was played, the delivery also puts the whole result into the model's context once per task: a `delegate` call with the task text and its output `{"status", "result" | "error", "detail"}` (the full result, or the failure reason with the raw error), just before the reply. It is kept in the session snapshot and survives a restart, but it is not an archive row, so `voca archive search` still finds one delivery reply per result.
- The reply waits for a `user-*` participant and for `voca.history` sent to them. If an event came while the agent was thinking or speaking, the reply sounds right after the end of that answer; otherwise after `[agent].event_silence_s` (1.5 s) in which neither the user nor the agent speaks, and any change of their state or any final transcript starts the pause again. The reply never cuts into anyone's speech; waiting approval requests go into the same record, ahead of results.
- Interruption: a reply closes all its results and failures only if it played to the end without an interruption and something was heard. Otherwise the result goes back into the queue and is spoken again in full when the session is idle (the user's speech and the agent's answer are over), after a 2 s pause if nothing was heard and nobody interrupted; there is no limit of repeats. `delivered_at` and «озвучено» on the card appear only after a reply that played to the end. If a reply was interrupted before its first sound, the archive and the history do not have it; the card and the model's context keep the full result, once. If the main model did not answer, the events stay open, and the reply is repeated after the user's next speech or the model's next answer.
- No client: the result waits for the next «Подключиться»; a participant who left closes delivery until someone joins again.
- Restart: a new job delivers what was not delivered and, through `recover()`, carries open tasks to their end, abandoned `cli-*` tasks included.
- Risk: if the process dies between the playout and writing `delivered_at`, the result is spoken again.

**Tools.** Besides `delegate` the model has five tools; it can neither approve nor deny a request: the agent judges the user's reply (Voice approval below), and the buttons answer from the page.

- `list_tasks()` — `{"tasks": [...]}`: every open task by creation time, then up to 5 latest finished ones; each `{"id", "text"` (up to 60 characters)`, "status"}` plus `"result"` (up to 300 characters, cut at a sentence boundary) for `succeeded`, `"error"` (the spoken reason) for `failed`, `"approval": {"request", "command"}` for `waiting_approval`.
- `list_notifications()` — `{"notifications": [...]}`: the events that are not closed, by arrival time; each `{"id"` (a string)`, "task", "kind", "state", "age_s", "text"` (up to 60 characters)`}`, plus `"danger"` (`low` | `medium` | `high`), `"request"`, `"command"` for `approval`, `"result"` (up to 300 characters) for `result`, `"error"` for `failure`. The level comes from the table `delegation/danger.py` by the Hermes description; an unknown description is `high`.
- `cancel_task(id?)` — `/stop` with the reason `user`, waits up to 30 s for a final status (like the deadline), then the task's view `{"id", "status", "error" | "result"}`; a run that completed before the stop stays `succeeded`. The cancel sets the outcome itself and marks the task delivered, so it is not spoken. If the cancel is cut short (a restart during the window), the task's watcher settles it the same way: it keeps polling with the reason `user` instead of the deadline, takes the final status as `cancelled`, marks it delivered, and after 30 s without a final status sets `failed`/`cancelled` itself.
- `steer_task(text, id?)` — `POST /v1/runs/{id}/steer` `{"input": text}`, only for a `running` task; answers `{"id", "event": "steered", "accepted": true}`; the task's state does not change.
- `open_approval(id?)` — the `list_notifications` view of a waiting request: `id` is the event's `id`; without `id` the only open request is taken. The code follows the spoken text of the reply and sets `essence` when its first sentence has finished.
- `id` is the task's `tool_call_id`; a unique prefix is accepted; without `id` the only matching open task is taken. Errors are honest tool errors: «Нет такого поручения», «Неясно, какое поручение: назовите id из list_tasks», «Поручение уже завершено», «Поручение сейчас не принимает уточнений», «Сейчас нет запроса подтверждения», «Нет такого запроса», «Неясно, какой запрос: назовите id из list_notifications», «Не удалось связаться с Hermes».

**Facts in the context.** Every delegation event the model did not cause itself is put into its context as a pair `function_call` + `function_call_output` with the name of a real tool (livekit drops pairs of unknown tools when a context is restored): the result is `delegate` (above), everything else, a voice denial included, is `list_tasks` with the output `{"id", "event", "by": "voice"|"button"|"hermes", …}`.

| `event` | When | Extra fields |
| --- | --- | --- |
| `approval_mentioned` | before the reply that mentions a request, once per request; the pair is `list_notifications`, `id` is the event's id | `request`, `command`, `danger` |
| `approved` | voice (the approval judge) or the button | `request` (voice) |
| `denied` | voice (the approval judge) or the button | `request` (voice) |
| `approval_failed` | the voice answer did not go through (`is_error`) | `error` |
| `approval_expired` | Hermes refused by itself (`by: hermes`) | |
| `cancelled` | the card's «Отменить задачу» | `status`, `error` \| `result` |

The pairs are kept in the session snapshot and survive a restart; they are not archive rows (like the result pair of VOCA-33). A fact goes in before the agent's reply. The model's own tool calls reach the context and the archive as usual.

**Voice approval.**

- Mention: a request that arrives goes into the event speech record, ahead of results, with `REQUEST_PROMPT`; the main model says in its own words how many requests wait, without the action, the command or the id, and asks whether to read them. The fact `approval_mentioned` goes in before that reply; a mention played to the end moves the event to `mentioned`, an interrupted one is spoken again.
- Reading: the main model reads a request with `open_approval(id?)`: the first sentence names the action and its object, then the details and the question. The code follows the spoken text of that reply (after an interruption livekit keeps the played text); when its first sentence has finished, the event moves to `essence`, an interrupted reading included.
- Sorter: while requests wait, every reply gets one `POST /v1/systemone` with the questions `kind`, `relation` (`answers`, `asks`, `other`) and `answer` (`approve`, `deny`), and `target` (`request_1`…`request_n`, `unclear`) with two or more requests; without requests the sorter asks only `kind`, two options.
- Three checks (`conversation/approval_judge.py`): the user knows what the request is about, it is clear which request the reply answers, and the confidence is enough for the danger level. The binding is by content (a stem of 4 letters of a word of the description, or the object of the command: the last path component, the package after `install`/`remove`), by place (the agent's last phrase was the reading, a re-ask, a clarification or the answer to a question about the request) or none. A short «да» approves only by place after the essence; a reply that names the action approves at once, the essence not needed.
- Confidence is the product of the answers (`kind × relation × answer`, `× target` with two or more requests); it is compared with `[decision].threshold_low|medium|high` (0.45/0.50/0.55, the VOCA-44 measurement, [bench/report_decision.md](bench/report_decision.md)) by the level of the rule, and with `threshold_deny` (0.45) for a denial. A denial of the only request needs no essence and no binding.
- Re-ask: a short «да» by place to a `high` request makes the agent ask once more, naming the action; the second «да» approves.
- Clarifications: an unclear request («к какому?») or an unsure answer gives a one-turn record asking the main model one short question; after `[decision].clarify_limit` (2) in a row the agent says that the request stays in the queue.
- Silence: before the answer goes to Hermes the agent waits `[agent].action_silence_s` (0.5 s) of user silence; speech that resumes cancels the decision, and the glued reply is judged again.
- Answer: the fact `approved` or `denied` (`by: voice`, `request`) goes into the context and the main model replies in its own words; a reply that also asks for something else is answered as usual.
- Failure: with the decision model failing or off, nothing is approved or denied by voice; the one-turn record asks the main model to say that the answer was not understood and to offer the buttons.
- Reminder: `[delegation].approval_reminder_s` (60 s) before `approval_timeout_s` (300 s) the agent reminds once, in silence, naming the action; 0 turns the reminder off.
- Buttons: «Подтвердить» and «Отклонить» on the card answer the request; the fact goes into the context, and the main model says one short phrase about the press in the event speech (an interrupted phrase is not repeated).
- Expiry: Hermes refuses by itself after `approvals.timeout`, the task returns to `running`, and the fact `approval_expired` (`by: hermes`) goes into the context.
- Restart: a new job mentions again the requests in `arrived`; `mentioned` and `essence` are not mentioned again, the reminder is armed for every open request.
- The one-turn records (a question about the request, a clarification, a re-ask, «stays in the queue», «waits», the failure) live one turn: they are neither in the context, nor in the archive, nor in the snapshot. Their texts are code constants.
- Danger: only the rule of `delegation/danger.py` sets the level; the model is not asked about it (VOCA-44: it raised the level without a reason).
- Deviation from research 2.7 and 6.3 item 7 (the engineer's decision, VOCA-34): the card shows neither the choice nor «истёк». After an answer or the expiry it is «выполняется» again. The trace of an answer is the `task` row with `approval` and the agent's reply in the archive.

**Events.** The agent writes a `task` row on every change of a task: `tool_call_id`, `status`, `run_id`, `error`, `delivered`; a delivery that did not close writes a `task` row with `interrupted` and `delivered: false`; the closing one has `interrupted: false`, `delivered: true`; an answer writes one with `approval` (`once`|`deny`) and `by` (`voice`|`button`). An `approval_event` row (`event_id`, `tool_call_id`, `state`) marks a request `mentioned` or `essence`; an `approval_read` row (`event_ids`, `played`, `interrupted`, `essence`) closes an `open_approval` reply; an `approval_judge` row (`text`, `requests`, `outcome`, `event_ids`, `binding`, `confidence`, `threshold`, `clarified`) is the judge's decision on a reply while requests wait. While requests wait, a `sorter` row also has `relation`, `answer` and, with two or more requests, `target`. `approval_decision` is no longer written. The agent's answer that was heard also gives `turn` rows like a delivery. A reply about results that was heard also gives a `turn` row: `user_text` from the previous turn, `turn` not incremented; walkthroughs match it to the `task` row by `ts`. `voca bench delegate` writes nothing to `.local/events.jsonl`.

**Cards.** After «Подключиться» the agent sends the page every task (a `tasks` message on `voca.cards`) and the page shows them above the transcript, newest first: the status, the text, the full result, the error and «озвучено» once the result was delivered. A task waiting for approval shows what Hermes asks (`description`, `command`) and the buttons «Подтвердить» and «Отклонить»; every open task has «Отменить задачу». A button calls the agent over RPC (`voca.approve`, `voca.cancel_task`; `voca.cancel_task` answers as soon as the cancel has started, and the outcome comes as a `task` message); while the call runs the card's buttons are disabled, an error re-enables them and shows «ошибка: …», a success is shown by the next `task` message. Tasks of `voca bench delegate` show up too; they are marked delivered by the CLI. A rejoin or a new `history` does not clear the cards. Format: [AGENTS.md](AGENTS.md#client-channel).

**Stand (VOCA-31, 2026-11-16).** Against `https://hermes.priney.com` (Hermes 0.22.2) on a temporary `VOCA_DELEGATION__PATH`: a simple task went `queued` → `running` → `succeeded` in about 4 s; the same `--key` again printed the same `run_id` with one row in `tasks`; a run interrupted by Ctrl-C (exit 130) stayed `running` and `--recover` carried it to `succeeded`. First observation of `/stop`: with `--timeout 20` VOCA sent `/stop` 20.2 s after the task was created; `/stop` answered `200` with `status: stopping` (not a final status); the run became `cancelled` about 0.5 s later (a direct probe), and the task saw it on the first poll of the 30 s window, 2 s after `/stop`: `failed`, `error: timeout`. Approval probe: the task «create `/tmp/voca-approval-once` with `a.txt` and remove it with `rm -r`» never reached `waiting_for_approval` — Hermes ran `rm -r` without asking and the run ended `completed` (`pending_controls` empty, `approval` null); there was nothing to answer there. On the `voca` profile (`https://hermes-voca.priney.com`, Hermes `3cbe7b1`) the `rm -r` task reached `waiting_approval`, and both answers went through `voca bench delegate --approve` from a second process: `once` — the run ended `succeeded` and the directory was removed; `deny` — the run ended `succeeded` with the command not run and the directory kept.

**Stand (VOCA-35, 2026-11-16).** Against `https://hermes-voca.priney.com` (Hermes `3cbe7b1`) on a temporary `VOCA_DELEGATION__PATH`: a simple task created the directory `/tmp/voca-approval-once` with `a.txt`, `queued` → `running` → `succeeded`; the task «`rm -r` that directory» gave `waiting_approval` with `description` «delete in root path» and `command` `rm -r /tmp/voca-approval-once` (choices once/deny/session/always); the answer `once` → `running` → `succeeded`, the directory is gone; on the second directory `/tmp/voca-approval-deny` the answer `deny` → `succeeded` with the command not run, one approval request and no repeat, the directory stays. In this probe `voca bench delegate --approve` still read only `approval` and printed «запрос подтверждения не активен», so both answers were sent to `POST /v1/runs/{id}/approval` directly; the CLI path was fixed in VOCA-31 and checked on this profile (the paragraph above).

**Stand (VOCA-34, 2026-11-17).** Against `https://hermes-voca.priney.com` (Hermes `3cbe7b1`) on a temporary `VOCA_DELEGATION__PATH`: L1 — an interrupted `rm -r` task of `voca bench delegate` reached `waiting_approval` under the agent's `recover()`, the announcement played whole (a `task` row with `announced: true`, `interrupted: false`), `--approve once` → `running` → `succeeded`, the result was spoken once and the directory is gone; the first run found audio.cpp answering HTTP 500 for „“, fixed by the quote mapping in speech preparation. L2 — a task already in `waiting_approval` when the client joined was announced first; `--approve deny` → `succeeded`, the directory stays. L3 — after `scripts/down.sh agent` `.local/logs/agent.log` has no «Task was destroyed but it is pending», «delivery failed» or «delegation task failed».

## Contributing

Open an issue or a pull request. Run the checks from [AGENTS.md](AGENTS.md#checks) before sending a change.

## License

MIT, see [LICENSE](LICENSE).
