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
