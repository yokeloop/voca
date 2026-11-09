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
scripts/down.sh
```

`scripts/up.sh livekit web` starts `livekit-server --dev --bind 0.0.0.0 --node-ip "$VOCA_LIVEKIT_NODE_IP"` and `voca web`; logs and pids go to `.local/`. `POST /api/token` with `{"device": "<name>"}` returns a LiveKit token for the room `voca` and the URL in `VOCA_PUBLIC_LIVEKIT_URL`.

**Ports.** 7880 TCP (LiveKit HTTP and signalling), 7881 TCP and 7882 UDP (LiveKit media), 8083 TCP (`voca web`, `VOCA_WEB_PORT`).

**Variables** (`.env.example`, defaults in [AGENTS.md](AGENTS.md#stand-variables)): `VOCA_APPS_DIR`, `VOCA_MODELS_DIR`, `VOCA_LIVEKIT_BIN`, `VOCA_LIVEKIT_NODE_IP`, `VOCA_LIVEKIT_API_KEY`, `VOCA_LIVEKIT_API_SECRET`, `VOCA_WEB_HOST`, `VOCA_WEB_PORT`, `VOCA_PUBLIC_LIVEKIT_URL`. The local defaults need no edits.

**Route `voca.priney.com`.** Reachable only inside the owner's tailnet:

- Cloudflare DNS: A record `voca.priney.com` → `100.108.52.92` (the Traefik host in the tailnet), DNS only, not proxied.
- Traefik on that host, TLS by Let's Encrypt: `/` → `http://100.108.140.49:8083` (`voca web`), `/rtc` → `http://100.108.140.49:7880` (LiveKit signalling).
- Media goes directly over the tailnet to `100.108.140.49`: UDP 7882 and TCP 7881.
- In `.env`, uncomment the three values of the `voca.priney.com` block: `VOCA_WEB_HOST=100.108.140.49`, `VOCA_LIVEKIT_NODE_IP=100.108.140.49`, `VOCA_PUBLIC_LIVEKIT_URL=wss://voca.priney.com`.

The route holds no secrets. Host and Traefik details live in the owner's deployment documentation.

## Contributing

Open an issue or a pull request. Run the checks from [AGENTS.md](AGENTS.md#checks) before sending a change.

## License

MIT, see [LICENSE](LICENSE).
