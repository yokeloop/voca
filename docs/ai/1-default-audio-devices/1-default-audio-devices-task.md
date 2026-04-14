# Use system default audio input/output devices on startup

**Slug:** 1-default-audio-devices
**Ticket:** https://github.com/yokeloop/voca/issues/1
**Complexity:** medium
**Type:** general

## Task

Make `inputDevice` and `outputDevice` in `~/.openclaw/assistant/config.json` optional; if they are not set, the daemon uses the system default for the microphone (PyAudio) and the speaker (`aplay` without `-D`). Also fix a bug in `src/daemon.ts:53`: `listener.py` receives a hardcoded `deviceIndex: 0` instead of the value from the config.

## Context

### Area architecture

Microphone flow: `daemon.ts:49-54` spawns `listener.py` via `spawnListener()` (`src/listener.ts:14-74`). `listener.py:73-80` opens a PyAudio stream with `input_device_index=<int|None>`; on `None` PyAudio uses the system default.

Speaker flow: two ALSA `aplay -D <device>` consumers:

- `src/speaker.ts:28-35` — piper → aplay for TTS (used in `daemon.ts:225-230`).
- `src/sounds.ts:18-28` — `playSound('wake'|'stop'|'error')` (used in `daemon.ts:131, 163, 259`).

`voca start` reads the config once via `readConfig()` (`src/config.ts:23-34`), passes it into `VocaDaemon` (`src/cli.ts:93-94`, `src/daemon.ts:30-33`) and routes it to consumers.

### Files to change

- `src/types.ts:1-10` — make `inputDevice?: string`, `outputDevice?: string`; add `inputDeviceIndex?: number` (for PyAudio).
- `src/config.ts:8-17` — remove `'plughw:2,0'` from `defaultConfig`; `inputDevice`, `outputDevice`, `inputDeviceIndex` are absent from defaults.
- `src/daemon.ts:49-54` — replace `deviceIndex: useStub ? undefined : 0` with `deviceIndex: useStub ? undefined : this.config.inputDeviceIndex` (`listener.ts:22-24` already skips undefined correctly).
- `src/speaker.ts:14-35` — make `device?: string` in `opts`; when `undefined`, do not pass `-D` to `aplay`. Build the argv array via conditional spread.
- `src/sounds.ts:18-28` — make `device?: string`; when `undefined`, do not pass `-D`.
- `src/bootstrap.ts:146-179` — in `selectDevice()` put `Use system default (recommended)` as the first item. On selection, remove the field from `config` via `delete config[opts.field]`. Before the list, print a one-line warning: the ALSA identifier `plughw:X,Y` may change after reboot or USB re-plug — an explicit pick will then break.
- `src/cli.ts` — no changes in the `start` command action; the daemon already reads the optional config.

### Patterns to reuse

- Conditional CLI args: `src/listener.ts:22-24` — `if (!opts.stub && opts.deviceIndex !== undefined) args.push(...)`. Repeat the pattern in `speaker.ts` / `sounds.ts` for `-D`.
- `VocaConfig` already uses optional fields via spread with defaults (`src/config.ts:27`). New optional fields do not break the merge.
- The interactive `select()` in `src/bootstrap.ts:60-124` accepts a list of strings — it is enough to prepend an item to the `options` array in `selectDevice()`.

### Tests

- `test/config.test.ts:20-62` — 5 tests check defaults and merge. Rewrite for "these fields are not in defaults": change `expect(read.inputDevice).toBe(defaultConfig.inputDevice)` at `:32` and `expect(defaultConfig.inputDevice).toBe('plughw:2,0')` / `outputDevice` at `:53-54`.
- `test/daemon.test.ts:53-54, 94-95` — fixtures with `inputDevice: 'hw:0,0'` and `outputDevice: 'hw:0,0'` remain valid (this is now the "explicit override" case). Add a test: a config without `inputDevice` / `outputDevice` / `inputDeviceIndex` → listener is spawned without `--device-index`, speaker/sounds spawn `aplay` without `-D`.
- Manual check on a Raspberry Pi: `voca start` with a clean config → the wake word triggers via the system default microphone; TTS and beeps play through the system default speaker.

## Requirements

1. `VocaConfig.inputDevice` and `VocaConfig.outputDevice` are optional (`string | undefined`). Add an optional `inputDeviceIndex?: number` field to pass to PyAudio.
2. `defaultConfig` in `src/config.ts` no longer contains `inputDevice`, `outputDevice`, `inputDeviceIndex`. Existing configs with `"inputDevice": "plughw:2,0"` are read as an explicit override — merge via `{ ...defaultConfig, ...parsed }` works unchanged.
3. If `config.outputDevice` is not set, `src/speaker.ts` and `src/sounds.ts` spawn `aplay` **without the `-D` flag** (and without `-D default`). ALSA will pick `pcm.!default` from `~/.asoundrc` / PipeWire / PulseAudio.
4. If `config.inputDeviceIndex` is not set, `src/daemon.ts:49-54` passes `deviceIndex: undefined` to `spawnListener`; `listener.py` receives `input_device_index=None`, and PyAudio uses the system default.
5. `src/daemon.ts:53` does not contain a hardcoded `0`; the index comes only from `this.config.inputDeviceIndex`.
6. `voca bootstrap`, microphone and speaker selection steps: the first list item is `Use system default (recommended)`; on selection, the corresponding field is removed from the config. Before the list, a warning is printed about `plughw:X,Y` volatility after reboot and USB re-plug.
7. `voca start` on a fresh install (config without device fields) starts the daemon, reacts to the wake word, and responds via TTS without manually running `voca bootstrap`.
8. Existing unit tests are updated for the new defaults; at least one new test is added for the "no device fields → spawn without `-D` and without `--device-index`" scenario.

## Constraints

- Do not touch `src/recorder.ts` — dead code (recording lives in `listener.py`); fiddling with it is out of scope.
- Do not add CLI flags like `voca start --input-device ...` — override goes only through `~/.openclaw/assistant/config.json`.
- Do not migrate or rewrite existing `config.json`: fields with values remain as "explicit override". No warnings or migrations on `voca start`.
- Do not introduce a sentinel string `"default"` — optionality is enough.
- Do not change the `plughw:X,Y` format and do not try to resolve an ALSA string into a PyAudio index: these are two independent fields (`outputDevice` for aplay, `inputDeviceIndex` for PyAudio).
- Do not change the listener.py ↔ listener.ts protocol (JSON events, signals). Only launch arguments.
- Do not break the listener stub mode (`--stub` in `src/listener.ts:18-20`).

## Verification

- `npm run build` → no TypeScript errors.
- `npm test` → existing tests green; new "config without device fields" test green.
- `rm ~/.openclaw/assistant/config.json && voca bootstrap` → in steps 1 and 2 the first item is `Use system default (recommended)`, and the warning about `plughw:X,Y` volatility is printed before the list. After selecting this item, the saved `config.json` contains no `inputDevice` / `outputDevice` keys.
- After `voca start` with a config without device fields: `pgrep -af 'aplay'` during TTS does not contain `-D`; `pgrep -af 'listener.py'` does not contain `--device-index`.
- `voca start` with config `{ "inputDevice": "plughw:2,0", "outputDevice": "plughw:2,0" }` (existing override): aplay is spawned with `-D plughw:2,0`; listener.py — without `--device-index` (`inputDeviceIndex` not set).
- Reboot scenario: "Use system default" was selected, the actual USB microphone moved from `card 2` to `card 3` → `voca start` works unchanged (PyAudio picked up the new default).
- `voca start` with PulseAudio/PipeWire stopped and an empty `~/.asoundrc` → `aplay` without `-D` uses the ALSA default PCM; the daemon does not crash (silence is possible, but spawn succeeds).

## Materials

- [GitHub Issue #1](https://github.com/yokeloop/voca/issues/1)
- `~/.openclaw/assistant/config.json`
- `src/types.ts`, `src/config.ts`, `src/daemon.ts`, `src/listener.ts`, `src/speaker.ts`, `src/sounds.ts`, `src/bootstrap.ts`
- `listener.py`
- `test/config.test.ts`, `test/daemon.test.ts`
