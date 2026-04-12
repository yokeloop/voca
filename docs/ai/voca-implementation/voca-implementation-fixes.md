# Fixes: voca-implementation

## Fix 1: Replace bootstrap text prompts with arrow-key selection

**Status:** done
**Commit:** `7a0e429` fix(voca-implementation): replace bootstrap text prompts with arrow-key selection

**Problem:** Bootstrap prompts required typing device names and profile names manually. Not obvious what to enter.

**Changes:**

| File | Description |
|---|---|
| `src/bootstrap.ts` | Added `select()` function with raw stdin mode, arrow-key navigation, ANSI rendering. Added `parseDeviceList()` to extract ALSA devices from arecord/aplay output. Updated `selectDevice()` and `selectProfile()` to use arrow-key menu. |

**Validation:** build ✅, 67 tests ✅

## Fix 2: Read profiles from openclaw config dynamically

**Status:** done
**Commit:** `ff16b80` fix(voca-implementation): read profiles from openclaw config instead of hardcoding

**Problem:** Profile selection was hardcoded to `['personal', 'public']`. Should read available profiles from openclaw config at `~/.openclaw/openclaw.json`.

**Changes:**

| File | Description |
|---|---|
| `src/config.ts` | Added `getAvailableProfiles()` — reads `agents.list[].id` from `~/.openclaw/openclaw.json`, falls back to `['personal', 'public']`. |
| `src/cli.ts` | Removed hardcoded `VALID_PROFILES` array. `profile use` and `profile list` now call `getAvailableProfiles()`. |
| `src/bootstrap.ts` | `selectProfile()` now calls `getAvailableProfiles()` to populate arrow-key menu. |

**Validation:** build ✅, 67 tests ✅

## Fix 3: Check and install portaudio19-dev before pip install in bootstrap

**Status:** done
**Commit:** `ea2db02` fix(voca-implementation): check and install portaudio19-dev before pip install

**Problem:** `pip install pyaudio` failed during bootstrap on systems without `portaudio19-dev` because pyaudio requires the native PortAudio headers to compile. There was no check or prompt to install it.

**Changes:**

| File | Description |
|---|---|
| `src/bootstrap.ts` | In `installPythonVenv()`: added `dpkg -s portaudio19-dev` check before pip install. If not installed, prompts user and runs `sudo apt-get install -y portaudio19-dev`. |

**Validation:** build ✅, 67 tests ✅

## Fix 4: Fix openwakeword 0.4.0 API compatibility and broken model download URLs

**Status:** done
**Commits:** 
- `4ee6319` fix(voca-implementation): fix openwakeword 0.4.0 API and model download URLs
- `200ed56` docs(voca-implementation): update docs for fix-4

**Problem:** openwakeword 0.4.0 introduced breaking API changes. The `Model()` constructor changed parameter names, the old inference framework parameter was removed, and hardcoded model download URLs were broken or unavailable.

**Changes:**

| File | Description |
|---|---|
| `listener.py` | Updated `Model()` constructor: `wakeword_models` → `wakeword_model_paths`, removed `inference_framework="onnx"`. Made stop model optional (loaded only if file exists). Fixed `predict()` key lookup to use filename stems instead of CLI args. |
| `src/bootstrap.ts` | Updated WAKE_MODEL_URL to v0.5.1/hey_jarvis_v0.1.onnx, removed STOP_MODEL_URL. Restructured `installModels()` to copy wake model from venv pip package first, with curl --fail download as fallback. Removed stop model download entirely. |

**Validation:** type-check ✅, test ✅ (67/67), build ✅, python-syntax ✅

## Fix 5: Unify mic access — recording moved from sox to listener.py

**Status:** done
**Commits:** 
- `176f3bc` fix(voca-implementation): unify mic access by recording in listener.py instead of sox
- `b64015d` docs(voca-implementation): update docs for fix-5

**Problem:** Audio recording was split between two separate tools: listener.py detected wake words and listener.py recorded audio via PyAudio, but the daemon also spawned a separate sox process for recording. This created unnecessary complexity and potential for race conditions. The listener process already had access to the audio stream via PyAudio, so recording should be unified there.

**Changes:**

| File | Description |
|---|---|
| `listener.py` | Added recording mode triggered by SIGUSR1 (start recording) and SIGUSR2 (stop recording). Records audio directly to WAV via PyAudio, detects silence via RMS threshold, enforces timeout (120s), and cancels if no speech detected (30s). Emits `{"event": "recorded", "path": "..."}` on successful recording or `{"event": "cancelled"}` on timeout/silence. |
| `src/listener.ts` | Added parsing and emission of 'recorded' and 'cancelled' events from listener.py stdout. |
| `src/daemon.ts` | Removed sox-based recorder spawning. Recording lifecycle now managed entirely through listener events. |
| `src/types.ts` | Added 'recorded' and 'cancelled' event types to ListenerHandle events union. |
| `src/recorder.ts` | No longer used by daemon (sox process removed). |
| `test/daemon.test.ts` | Updated tests to use listener recording events instead of mock recorder. |

**Validation:** type-check ✅, test ✅ (67/67), build ✅, python-syntax ✅
