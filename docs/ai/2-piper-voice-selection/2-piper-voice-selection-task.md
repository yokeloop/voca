# Allow selecting alternative Piper TTS voices

**Slug:** 2-piper-voice-selection
**Тикет:** https://github.com/yokeloop/voca/issues/2
**Сложность:** medium
**Тип:** general

## Task

Add a `voca voice` CLI command group (`list`, `available`, `use`, `install`) that fetches the Piper catalog from HuggingFace, downloads voices into `~/.openclaw/assistant/bin/`, persists the selection in `config.piperModel`, and makes `speaker.ts` pass the voice's native sample rate to `aplay`.

## Context

### Архитектура области

Voice flows as a config value through the daemon:

```
config.json.piperModel (absolute .onnx path)
  → readConfig() resolves bare names via resolvePiperModel() (config.ts:36)
  → daemon.ts passes piperBin + piperModel to speak()
  → speaker.ts spawns `piper --model <path> --output_raw | aplay -r 22050 -f S16_LE -c 1`
```

Piper voice assets live in `~/.openclaw/assistant/bin/`:
- `piper` (binary, from bootstrap.ts:189–212)
- `<voice>.onnx` + `<voice>.onnx.json` pair (currently only `ru_RU-irina-medium`)

The `.onnx.json` carries the native sample rate: `{"audio": {"sample_rate": <int>, ...}}`. Sample rates vary by quality (low = 16000 Hz, medium = 22050 Hz, high = 22050 Hz). The hardcoded `-r 22050` at `speaker.ts:30` silently mispitches any non-22050 voice.

Voice names follow `<lang_code>-<name>-<quality>` (e.g. `ru_RU-irina-medium`, `en_US-lessac-high`). HuggingFace path convention: `<lang>/<lang_code>/<name>/<quality>/<name>.onnx[.json]`, where `<lang>` is the first two letters of `<lang_code>` lowercased (`ru_RU` → `ru`, `en_US` → `en`).

Catalog source: `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json`. Keys are full voice names; each entry carries `language.code`, `quality`, `files`.

### Файлы для изменения

- `src/cli.ts:44–72` — mirror the `profile` subcommand block to register `voice` with four actions. Import the new voice module instead of inlining handlers.
- `src/cli.ts:8` — import the new voice module.
- **new** `src/voice.ts` — `listInstalled()`, `listAvailable(languageFilter?)`, `installVoice(name)`, `useVoice(name)`. Hosts catalog fetching, HF URL construction, download logic.
- `src/bootstrap.ts:189–234` (`installPiper`) — extract the `.onnx`/`.onnx.json` download block at lines 215–233 into `voice.ts`'s `installVoice()` and call it from bootstrap with `ru_RU-irina-medium`.
- `src/bootstrap.ts:17–18` — drop the hardcoded `PIPER_VOICE_BASE`; derive it from the voice name.
- `src/speaker.ts:28–35` — read sibling `<piperModel>.json` before spawn, pass `audio.sample_rate` to `aplay -r`. Keep `-f S16_LE -c 1` constant.
- `src/config.ts:36–40` (`resolvePiperModel`) — already maps bare name to absolute path. Reuse unchanged.
- **new** `test/voice.test.ts` — cover HF URL construction, catalog parsing, language filter, and `installVoice` path resolution (mock network).
- `test/config.test.ts:58` — leave unchanged; default voice stays the same.

### Паттерны для повторения

- **Subcommand group structure** (`cli.ts:44–72`): `program.command('voice').description(...)` as parent; `.command('list')`, `.command('available')`, `.command('use <name>')`, `.command('install <name>')` with `.action(async () => { ... })` handlers that `readConfig()` → mutate → `writeConfig()`. On invalid input, `console.error` then `process.exitCode = 1; return`. Match the "unknown X: Valid X: ..." phrasing from `profile use`.
- **Download** (`bootstrap.ts:205, 227, 230`): `run('curl', ['-L', '-o', <path>, <url>])`. Reuse the exported `run()` helper — move it from bootstrap.ts into a shared module or re-export from `voice.ts`.
- **File existence check**: `fileExists()` at `bootstrap.ts:51–58` — extract to a shared util or duplicate.
- **Catalog fetch**: use Node 20's global `fetch` (no new dep). Cache the parsed JSON in-process for the CLI invocation's lifetime — single-shot commands need no persistent cache.

### Тесты

- `test/config.test.ts` covers config read/write and the `piperModel` default — do not regress.
- No existing tests for `speaker.ts` or `bootstrap.ts`. The new voice logic is the only mandatory test target this task: catalog parsing, HF URL derivation, language filter, and the `.onnx.json` sample-rate extraction used by `speaker.ts`.

## Requirements

1. `voca voice list` prints installed voice names (bare names, one per line) by scanning `~/.openclaw/assistant/bin/` for `*.onnx` files with a sibling `*.onnx.json`. Mark the active voice (matches `config.piperModel`) with a `*` prefix.
2. `voca voice available` fetches `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json` and prints voice names filtered by `config.language` (prefix match on `lang_code`, e.g. `language: 'ru'` → names beginning with `ru_`). A `--all` flag bypasses the filter. Each line: `<name>  <lang_code>  <quality>`.
3. `voca voice install <name>` downloads `<name>.onnx` and `<name>.onnx.json` from `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/<lang>/<lang_code>/<voice>/<quality>/` into `~/.openclaw/assistant/bin/`. Derive the URL path segments from the name. Reject names absent from the HF catalog with a non-zero exit.
4. `voca voice use <name>` validates the voice against the catalog, auto-downloads via `installVoice()` when not installed locally, writes `config.piperModel = <absolute path to .onnx>`, and prints `Switched to voice: <name>. Restart the daemon (voca stop && voca start) to apply.`.
5. `speaker.ts` reads `<piperModel>.json` before spawning and passes `audio.sample_rate` to `aplay -r <rate>`. On missing or malformed JSON, throw `SpeakerError` naming the voice — never fall back to 22050 silently.
6. `bootstrap.ts` delegates its voice-install step to `voice.ts:installVoice('ru_RU-irina-medium')`. Bootstrap behavior is otherwise unchanged (default voice and interactive prompts preserved).
7. Add unit tests in `test/voice.test.ts` covering URL derivation, language filtering, and catalog parsing (mock `fetch`).

## Constraints

- Keep `~/.openclaw/assistant/bin/` as the voice model directory. Do not relocate to `~/.openclaw/assistant/models/` — that path is reserved for wake-word ONNX files (see `src/listener.py` and CLAUDE.md).
- Add no runtime dependencies; use Node 20 global `fetch` for the catalog and keep `curl` for downloads (matches the existing `run()` pattern).
- Change the `VocaConfig` shape only where strictly needed. Derive the sample rate from `.onnx.json` per call rather than storing it in `config.json`.
- Do not hot-reload voice changes in the running daemon. `voca voice use` must instruct the user to restart, matching how `profile use` behaves — the daemon reads config once at `cli.ts:93`.
- Keep `ru_RU-irina-medium` as the bootstrap first-install voice; existing users rely on it.
- Keep `aplay`'s `-f S16_LE -c 1` constants — only `-r` varies per voice.
- Do not couple `voice.ts` to the daemon runtime; it must run from one-shot CLI commands without spawning `listener.py` or touching PID files.

## Verification

- `npm run build` — compiles with no TypeScript errors.
- `npm test` — existing suite stays green; new `test/voice.test.ts` passes.
- `voca voice list` on a fresh install (only `ru_RU-irina-medium` present) → prints `* ru_RU-irina-medium`.
- `voca voice available` with `config.language = 'ru'` → prints only `ru_*` voices; `voca voice available --all` prints the full catalog (>50 entries).
- `voca voice install en_US-lessac-medium` → creates `~/.openclaw/assistant/bin/en_US-lessac-medium.onnx` and `.onnx.json`; rerunning prints "already installed" and exits 0.
- `voca voice install does-not-exist` → non-zero exit; the error names the voice and suggests `voca voice available`.
- `voca voice use en_US-lessac-medium` when not installed → downloads, then updates `config.json`; `config.piperModel` ends with `en_US-lessac-medium.onnx`.
- Daemon restart after switching to `en_US-lessac-low` (16000 Hz voice) → TTS plays at correct pitch (not chipmunked); `aplay` runs with `-r 16000`.
- Deleting `<voice>.onnx.json` and attempting to speak → `SpeakerError` names the voice; the daemon does not fall back to 22050.
- Offline run of `voca voice available` → fails with a clear network error and non-zero exit; installed voices remain usable.

## Материалы

- [GitHub issue #2](https://github.com/yokeloop/voca/issues/2)
- `src/cli.ts:44-72` — profile subcommand analog
- `src/config.ts:8-17, 36-40` — config defaults + resolvePiperModel
- `src/speaker.ts:28-35` — hardcoded `-r 22050` to make dynamic
- `src/bootstrap.ts:17-18, 189-234` — PIPER_VOICE_BASE + installPiper to refactor
- `src/types.ts:1-10` — VocaConfig
- `test/config.test.ts` — config test pattern
- Piper catalog: `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json`
