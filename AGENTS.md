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
  (`VocaAgent`: the tool-call markup filter in `llm_node`, the `delegate`,
  `list_tasks`, `cancel_task`, `steer_task`, `deny_approval` and `repeat_approval`
  tools, the delegation facts in the context and the delivery of results, approval announcements and the approval phrase checked first in `on_user_turn_completed`, instructions, accepting a finished
  summary and the emergency context trim in `on_user_turn_completed`, the threshold trigger `maybe_compact`,
  the idle and daily check `check_due`, failures spoken aloud;
  `instructions.py`: `InstructionLoader` with the last valid set; `context.py`: `trim_chat_ctx` — the emergency path at
  `hard_limit`; `approval.py`: the approval phrase parse (the first word, the tail, a near miss); `descriptions.py`: the Russian descriptions of Hermes approval requests; `compaction.py`: the compaction plan and the summariser
  prompt; `summarizer.py`: the call to `[compression.model]`), `clientio/`
  (the `voca.cards` and `voca.history` channels, the `tasks` list on join, `actions.py`: the page's RPC), `telemetry/` (`.local/events.jsonl`), `session/`
  (`SessionStore`: the session snapshot and the archive in one SQLite file,
  «Session store»; `voca archive search`), `delegation/` (`DelegationService`:
  tasks given to Hermes through the Runs API, the state machine, polling, the
  deadline with `/stop`, `recover()` and `approve()`; `hermes.py` — the Runs
  API adapter and the only status mapping; `sqlite.py` — the task file
  `[delegation].path`; `voca bench delegate`), `config.py` (`Settings`: the
  environment and the agent config file, «Agent config»).
  `agent/src/voca/speech/` prepares text before TTS: the tool-call markup
  filter, LaTeX and tables, the quotes audio.cpp rejects (`„‟‚‹` → `«`,
  `“”›` → `»`), numbers as words, stress and the first fragment.
  `markup`, `numbers`, `prepare`, `conversation/context`,
  `conversation/compaction`, `conversation/approval`, `conversation/descriptions`, `conversation/instructions`, `session/model`,
  `delegation/model`, `delegation/ports` and `delegation/service` are pure
  modules, which
  `tests/test_import_boundaries.py` checks.
- `instructions.example/` — a sample `SOUL.md`, `user.md` and `agent.md`; the
  real files live outside the repository, in `VOCA_INSTRUCTIONS_DIR`.
- `config.example.toml` — a sample agent config; the real file is
  `~/.voca/config.toml` (`VOCA_CONFIG`), outside the repository.
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
| `VOCA_LLM_PROFILE` | `bolt-e4b` | llama-server model profile, `scripts/llm-profiles/<name>.sh`; only this model is loaded. `bolt-e4b` is the agent's model; `lfm2.5-2.6b` and `lfm3-1.7b` are alternatives, set explicitly |
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
| `VOCA_HERMES_URL` | empty | Hermes API server URL for `bench/hermes_runs.py` and the agent (`voca bench delegate`, the `delegate` tool); `https://hermes-voca.priney.com` (the Hermes profile `voca`) on the stand |
| `VOCA_HERMES_KEY` | empty | `API_SERVER_KEY` of the Hermes profile `voca`; read by `bench/hermes_runs.py` and the agent; a secret, only in `.env` |
| `VOCA_LITELLM_KEY` | empty | LiteLLM virtual key `voca` for `https://litellm.priney.com` (the session summariser); a secret, only in `.env` |

Everything downloaded lives outside the clone, in the machine's shared
directories `~/Apps/<tool>/<version>/` and `~/models/`; a variable with that
default names each path. `devkey`/`secret` cannot change without a
livekit-server config.

### Agent config

The agent's behaviour lives in `~/.voca/config.toml`, outside the repository;
`VOCA_CONFIG` sets another path, and `config.example.toml` is the sample. A
missing file means the defaults of `Settings` (`agent/src/voca/config.py`). The
file is read at the start of each job; there is no hot reload.

Sources in order: constructor arguments → environment → file → defaults. A
one-off override for one run is `VOCA_<SECTION>__<FIELD>`, e.g.
`VOCA_COMPRESSION__THRESHOLD=0.08` or `VOCA_COMPRESSION__MODEL__TIMEOUT_S=9`.
The former flat names of these settings (`VOCA_` plus the field name, without
a section) are no longer read.

- `[model]` — the context window, the reasoning budget, sampling.
- `[agent]` — the seven pipeline settings; G5/G6 tune them.
- `[session]` — the session history file and how much of it a client gets.
- `[compression]` — the compression triggers and the trim.
- `[compression.model]` — the summariser.
- `[delegation]` — the task file and the run deadline.
- `[approval]` — the voice approval words: `confirm` and `deny` (single words;
  the first of each is spoken and shown on the card), `refusal_stems`,
  `refusal_words`, `confirm_stems` and `negations` (the guard of
  `deny_approval`), `near` (edits of a near
  miss) and `echo_words` (words in a row that make an echo). No word of the
  voice protocol is in the code. Every entry must hold a word, and each
  `confirm` word must be more than `near` edits from each `deny` word;
  otherwise the config is refused.

`[session].history_on_join` is how many archive rows a joining client gets
in `voca.history` («Client channel»); `[session].path` is the session store
(«Session store»).

`[delegation].path` (default `~/.voca/tasks.sqlite3`, env
`VOCA_DELEGATION__PATH`) is the task file: the directory 0700 and the file 0600
when VOCA creates them, WAL, migrations by `user_version`; a newer schema is
refused. It is separate from the session store and is backed up with `~/.voca/`
(E5). `[delegation].run_timeout` (900 s, `VOCA_DELEGATION__RUN_TIMEOUT`) counts
from a task's creation; after it VOCA sends `/stop` to the run.

Trim: above `hard_limit · context_tokens` estimated tokens (`len(text) // 3`)
the oldest turns are dropped until `trim_to · context_tokens` remain. The rules
are `0 < trim_to < hard_limit < 1` and `threshold < hard_limit`. This trim is
now the emergency path; compaction («Context compaction») runs first.

`context_tokens` is kept equal to `-c 16384` in `scripts/llama.sh` by hand. At
the start of each job the agent reads `GET /props` of llama-server and writes a
`context_mismatch` line when `n_ctx` differs.

The summariser key comes only from `VOCA_LITELLM_KEY` (the table above);
`litellm_key`, `api_key` and any unknown key in the file are rejected. The
Hermes URL and key come only from the environment (`VOCA_HERMES_URL`,
`VOCA_HERMES_KEY`); `hermes_key` in the file is rejected. A file
with an error makes `voca agent` print `конфиг не прочитан` with the path and
exit with code 1.

`VOCA_INSTRUCTIONS_DIR` (default `~/.voca/instructions`) is an environment
variable, not a file field. `[agent].aec_warmup` covers only the first N
seconds from the start of the session's first agent reply; later replies are
interruptible at once.

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
the model card; the client sends `thinking_budget_tokens` and only the sampling
values set in `[model]` («Agent config»).

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

## Client channel

- The agent sends a JSON `state` message on the text stream topic
  `voca.cards`: `{"type": "state", "instructions_sha256",
  "instructions_source": "fresh"|"last_valid", "instructions_error":
  string|null, "components": {"stt", "llm", "tts": "ok"|"down"}}`.
- The message goes out at the start of the session, when a participant
  connects and on every change.
- A component is `down` after an unrecoverable error and `ok` after the first
  metric of that stage.
- `instructions_error` is an addition to `derive:eb1id854:§5`;
  `components.hermes` is not sent: tasks travel as `task` messages.
- The agent sends a JSON `task` message on `voca.cards` on every change of a
  task given to Hermes: `{"type": "task", "tool_call_id", "status":
  "queued"|"running"|"waiting_approval"|"unknown"|"succeeded"|"failed",
  "text", "result": string|null, "error": string|null, "created_at",
  "delivered_at": number|null, "approval": {"description", "command",
  "choices", "request_id", "hint"}|null}`. `approval` is set only while the task is
  `waiting_approval`. `task`, `state` and `tasks` share one send chain, so
  their order is kept. The agent sends them from the `delegate` tool, its
  watchers and the delivery.
- The agent sends a JSON `tasks` message on `voca.cards` to one participant
  (`destination_identities`): `{"type": "tasks", "tasks": [...]}` — the same
  objects without `type`, every task in `[delegation].path`, newest first, no
  limit. Like `history`, it goes out when a participant connects and after
  `ctx.wait_for_participant()` for a participant that joined before the job,
  each time after `state`. When the task file does not open, the agent logs
  `task store not opened` to `.local/logs/agent.log`, keeps running and sends
  no `tasks` message.
- The page replaces its task block with every `tasks` message; a `task`
  message updates one card by `tool_call_id`, and a new one goes on top.
- The task block is separate from the transcript: neither `history` nor a
  disconnect clears it. A card shows the status, the text, the full result,
  the error and «озвучено» once `delivered_at` is set. A `waiting_approval`
  card also shows `description`, `command`, the agent's `hint` (built from
  `[approval]`: «Подтвердите на экране или скажите „<confirm>“ или „<deny>“»)
  and the buttons «Подтвердить» and «Отклонить»
  (only with a `request_id`); every open card has «Отменить задачу».
- The page calls RPC methods of the agent participant (the one with
  `lk.agent.state`): `voca.approve` with `{"tool_call_id", "request_id",
  "choice": "once"|"deny"}` and `voca.cancel_task` with `{"tool_call_id"}`,
  response timeout 45 s; `voca.cancel_task` answers once the cancel has
  started, its outcome comes as a `task` message. The answer is `{"ok": true}` or `{"ok": false,
  "error"}`; an invalid payload gives «неверный запрос». The agent answers a
  request only when `request_id` matches the waiting one, speaks nothing and
  puts a fact into the model's context.
- The agent sends a JSON `history` message on the text stream topic
  `voca.history` to one participant (`destination_identities`):
  `{"type": "history", "session_id", "items": [{"id", "at", "who":
  "вы"|"агент"|"сжатие", "text"}]}`.
- `items` are the last `[session].history_on_join` (default 50) `user`,
  `assistant` and `summary` rows of the open session's archive, oldest first;
  `id` is the archive's `item_id`, a `summary` has an empty `text`. Tool calls
  and results are not sent.
- The message goes out when a participant connects and after
  `ctx.wait_for_participant()` for a participant that joined before the job; a
  first join can therefore get it twice. Sends are chained, the archive is read
  at send time.
- On every `history` the page clears its transcript, draws the items
  (`сжатие` as the line «— контекст сжат —») and appends live
  `lk.transcription` segments after them.
- The page sends the agent JSON on the text stream topic `voca.metrics`:
  `{"type": "client_e2e", "seq", "client_e2e_ms", "speech_ms"}`, one message
  per pair «end of speech → first agent sound».
- A pair opens when the agent's `lk.agent.state` turns `thinking`, or
  `speaking` straight from `listening`; one pair per transition, none while
  the agent is `speaking`. End of speech is the last microphone frame before
  that transition above both the noise floor (the 10th percentile over 3 s)
  + 15 dB and the utterance's peak − 20 dB, the utterance at least 150 ms
  long; the microphone is ignored while the agent is `speaking`. The first
  sound is the first frame after the transition where the agent's remote
  track stays above −50 dBFS for two 10 ms ticks.
- The agent writes a JSONL `client` row; an invalid message only logs a
  warning.

## Session store

- The file is `[session].path` (default `~/.voca/sessions.sqlite3`, env
  `VOCA_SESSION__PATH`). When the agent creates it, the directory gets 0700
  and the file 0600. The file is in WAL mode, so `-wal` and `-shm` lie beside
  it; migrations follow `user_version`. A schema newer than the agent or any
  error opening the file stops `voca agent`: exit code 1 and the line
  `хранилище сессии не открыто`.
- Tables: `sessions` (`id`, `started_at`, `ended_at`, `end_reason`,
  `context_json`, `context_at`, `compacted_at`, `summary_count`); `messages`,
  append-only (`kind` user/assistant/tool_call/tool_result/summary, `item_id`,
  `call_id`, `name`, `text`, `data_json`; `UNIQUE (session_id, item_id)`);
  `messages_fts` (`unicode61 remove_diacritics 2`).
- Restore: at the start of each job the open session (`ended_at IS NULL`) is
  lifted into `VocaAgent(chat_ctx=…)`, and the agent writes a
  `session_restored` line (`session_id`, `items`, `estimate`). `session` in
  the JSONL stays the job id.
- Snapshot: `to_dict(exclude_function_call=False, exclude_timestamp=False,
  exclude_config_update=True)` without system and developer messages; the
  instructions come from `InstructionLoader`. The trim and compaction change
  only the snapshot; archive rows stay.
- Writes: one transaction «new rows + snapshot» after each `turn` line and
  when the job stops and after a summary is adopted (`summary` row,
  `compacted_at`, `summary_count`; «Context compaction»). Tool calls and results wait for the next boundary. A
  failed write gives a `session_write_failed` line (`error`), and the rows
  stay in the buffer.
- Search: «ё/Ё» is folded to «е/Е» in the triggers and in the query; the
  tokenizer does not do it. The stock FTS `'rebuild'` would reindex without
  the fold: rebuild with `'delete-all'` and `INSERT … SELECT id,
  replace(replace(text,'ё','е'),'Ё','Е') FROM messages` (as the comment in
  `001-session.sql` shows).
- `voca archive search <query> [--limit N]` (default 20): FTS5 syntax; another
  word form is found with the prefix `word*`. An output line is `id`, time,
  `kind` and the `snippet` with `[…]`. An invalid query or a missing file
  gives exit code 2, and no file is created.
- The file is the only copy of the history; its backup is E5 (the `~/.voca/`
  directory).

## Context compaction

- Mechanism: Hermes `3cbe7b1` `agent/context_compressor.py`, the tail by the
  `legacy` formula.
- Trigger: after each agent reply (`agent_state_changed` → `listening`), when
  the estimate `len(text) // 3` over the context is at least
  `threshold · context_tokens`; `threshold = 0` turns it off. At most one
  request runs at a time; a new trigger while one runs is ignored.
- Idle and daily triggers:
  - `idle_hours` (0 — off): the time since the last message (user,
    assistant, tool call or result; the summary does not count) is at least
    `idle_hours`;
  - `daily_at` (`""` — off): the latest `HH:MM` boundary in the host's local
    time (`datetime.fromtimestamp`, no time zone setting) is later than the
    last compression, or the session start if there was none;
  - both need a message newer than the last compression and a context above
    `tail_ratio · threshold · context_tokens`; idle wins when both are due;
  - checked at the start of a job after the first participant, on
    `participant_connected` of `user-*` and at every turn boundary in
    `on_user_turn_completed`, after the adoption and the trim; there is no
    timer;
  - the same background summary as the threshold, adopted one turn later;
    the first reply after joining may still use the full context; `trigger`
    is `idle` or `daily` in `compaction` and `compaction_failed`;
  - the last message time comes from the restored context, so idle time
    survives an agent restart (unlike Hermes, whose idle clock is in memory).
- Plan: the head is the instructions; the previous summary is found by
  `extra.voca_summary`; the tail is the `split_turns` turns from the end
  within `tail_ratio · threshold · context_tokens`, at least `tail_turns_min`
  and always the last turn; «call — result» pairs are never split; the middle
  goes to the summary.
- Prompt: one user message in Russian, fresh or iterative («ПРЕДЫДУЩИЙ
  ПЕРЕСКАЗ / НОВЫЕ РЕПЛИКИ»), six sections, secrets → `[СКРЫТО]`, today's
  date, the goal «Целевой объём ~N токенов» with N = clamp(20 % of the folded
  part, 400, 1500); `max_tokens` is not sent, as in Hermes (thinking models
  spend it on reasoning); `finish_reason == "length"`, an empty reply or a
  refusal count as failure; `<think>…</think>` is stripped.
- Call: `[compression.model]` (`url`, `model`, `timeout_s`) through
  `openai.AsyncOpenAI`, the key is `VOCA_LITELLM_KEY`, no retries.
- Adoption: in the next `on_user_turn_completed`; the turn neither waits for
  the summary nor cancels it. Only the folded middle and the previous summary
  are removed. The summary is an assistant message with the prefix
  «[СЖАТИЕ КОНТЕКСТА — ТОЛЬКО СПРАВКА]» and an end marker; it stands right
  before the first remaining turn, `created_at` = that turn's
  `created_at − 1e-6`. The first turn after adoption pays one full prefill.
- Record: an archive row `summary` (the body without the prefix, `data_json`:
  `trigger`, `estimate_before`, `after`, `folded_turns`), the snapshot,
  `compacted_at`, `summary_count`; a `compaction` line (`trigger`,
  `estimate_before`, `after`, `folded_turns`, `ms`, `summary_tokens` —
  `usage.completion_tokens` or the `len // 3` estimate).
- Failure: `compaction_failed` (`trigger`, `reason`: `no_key`, `timeout`,
  `connection`, `http_<code>`, `error`, `empty`, `length`, `refusal`;
  `estimate_before`, `ms`; `error` — the exception class name, only when the
  summary task raised outside the API errors, with `reason` `error`); the
  context does not change, the retry comes after the next reply. Without a
  key the line is written on each trigger, with no request. At `hard_limit`
  `trim_chat_ctx` trims to `trim_to` (`context_trimmed`).
- Guard: an adoption that leaves the estimate at or above the threshold adds
  a strike, one below it resets the strikes; at two strikes no summary starts
  and one `compaction_blocked` line is written (`strikes`, `estimate`,
  `threshold`, `retry_in_s`), after 300 s of block one probe runs, the strikes
  live in memory per job, and the `hard_limit` trim works regardless (Hermes
  anti-thrash). The idle and daily triggers bypass the guard: they neither
  check nor change the strikes.

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
- О7 (closed in VOCA-33): the stub is gone; the `delegate` tool gives the
  task to Hermes.

Deviation from `derive:8kki4ynb:§6` and `derive:eb1id854:§5` (VOCA-32): the
page gets the task list as an addressed `tasks` message on `voca.cards`, not
from the RPC `voca.list_tasks`. RPC v1 caps a response at 15 KB (1504
«Response payload too large»); the stand list with one long result was
43208 bytes. A text stream has no such cap, as with `voca.history`.

## References

- `derive:8kki4ynb` — target architecture v4.
- `derive:eb1id854` — detailed design.
- E0 epic research: https://draft.yokeloop.com/artifacts/voca-1-e0-llm-hermes-iim7wlw3
