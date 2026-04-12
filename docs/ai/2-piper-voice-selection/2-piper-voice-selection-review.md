# Review: 2-piper-voice-selection

**Scope:** src/util.ts, src/voice.ts, src/cli.ts, src/bootstrap.ts, src/speaker.ts, test/voice.test.ts
**Commits reviewed:** `60e4f4a`..`e33d303`
**Fix scope chosen:** Fix both minors

## Summary

Implementation matches the plan. Build + 79/79 tests green. No Critical or Important issues. Two Minor issues found and fixed.

## Issues

### Minor — `useVoice` forced a catalog fetch for already-installed voices

- **File:** `src/voice.ts:useVoice`
- **Description:** `useVoice(name)` called `fetchCatalog()` before any local check, so switching between already-installed voices required internet even though no network resource was needed.
- **Status:** ✅ Fixed in `905a192`
- **Fix:** Removed the upfront catalog check from `useVoice`; it now only calls `installVoice(name)` (which validates against the catalog) when the local `.onnx` is missing. Already-installed voice switching now works offline.

### Minor — `voice list` active-voice marker used `endsWith` instead of path equality

- **File:** `src/cli.ts:voice list action`
- **Description:** `config.piperModel.endsWith(\`${name}.onnx\`)` worked in practice (bin/ basenames are unique) but was fragile to path shape changes.
- **Status:** ✅ Fixed in `905a192`
- **Fix:** Added `voicePath(name)` export in `voice.ts` that returns the canonical absolute path; `voice list` now compares `config.piperModel === voicePath(name)`.

## Skipped

None.

## Validation

```
npm run build  ✅
npm test       ✅ 79 passed
voca voice list  ✅ "* ru_RU-irina-medium"
```

## Commits

- `905a192` #2 fix(2-piper-voice-selection): skip catalog fetch on useVoice when installed and compare resolved paths in voice list
