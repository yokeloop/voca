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
