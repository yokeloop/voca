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
**Commit:** pending `docs(voca-implementation): update docs for fix-3`

**Problem:** `pip install pyaudio` failed during bootstrap on systems without `portaudio19-dev` because pyaudio requires the native PortAudio headers to compile. There was no check or prompt to install it.

**Changes:**

| File | Description |
|---|---|
| `src/bootstrap.ts` | In `installPythonVenv()`: added `dpkg -s portaudio19-dev` check before pip install. If not installed, prompts user and runs `sudo apt-get install -y portaudio19-dev`. |

**Validation:** build ✅, 67 tests ✅
