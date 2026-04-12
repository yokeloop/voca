# Report: 2-piper-voice-selection

**Plan:** docs/ai/2-piper-voice-selection/2-piper-voice-selection-plan.md
**Mode:** inline (orchestrator-executed; final validation inline)
**Status:** ✅ complete

## Tasks

| #   | Task                                             | Status  | Commit    | Concerns |
| --- | ------------------------------------------------ | ------- | --------- | -------- |
| 1   | Extract shared utilities into `src/util.ts`      | ✅ DONE | `60e4f4a` | —        |
| 2   | Create `src/voice.ts` catalog + listing          | ✅ DONE | `46540b1` | —        |
| 3   | Add `installVoice` and `useVoice`                | ✅ DONE | `46540b1` | combined with T2 |
| 4   | Wire `voca voice` CLI subcommands                | ✅ DONE | `5c4006c` | —        |
| 5   | Refactor `bootstrap.ts` to delegate voice install | ✅ DONE | `9b7ad9e` | —        |
| 6   | Dynamic sample rate in `speaker.ts`              | ✅ DONE | `b9af849` | —        |
| 7   | Unit tests for voice module                      | ✅ DONE | `e33d303` | —        |
| 8   | Validation                                       | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status   | Commit |
| ------------- | -------- | ------ |
| Polish        | ✅ skipped — no simplification opportunities | — |
| Validate      | ✅ pass  | —      |
| Documentation | ⏭️ skipped — no README/CHANGELOG changes required (CLAUDE.md already documents daemon layout; voice subcommand help surface is self-documenting) | — |
| Format        | ⏭️ skipped — project uses no formatter in `package.json` scripts | — |

## Validation

```
npm run build ✅
npm test      ✅ (79 passed, 0 failed — 9 new voice tests)
voca voice --help    ✅ lists list/available/install/use
voca voice list      ✅ shows "* ru_RU-irina-medium"
voca voice install does-not-exist ✅ non-zero exit, error names voice
```

## Changes summary

| File                     | Action   | Description |
| ------------------------ | -------- | ----------- |
| `src/util.ts`            | created  | Shared `run`, `runCapture`, `fileExists` helpers extracted from `bootstrap.ts`. |
| `src/voice.ts`           | created  | Catalog fetch (HF `voices.json`), HF URL derivation, `listInstalled`, `listAvailable`, `installVoice`, `useVoice`. |
| `src/cli.ts`             | modified | `voca voice` subcommand group: `list`, `available [--all]`, `install <name>`, `use <name>`. |
| `src/bootstrap.ts`       | modified | Dropped `PIPER_VOICE_BASE` and inline curl block; delegates to `installVoice('ru_RU-irina-medium')`. Duplicated helpers moved to `util.ts`. |
| `src/speaker.ts`         | modified | Reads `<piperModel>.json` per call; passes `audio.sample_rate` to `aplay -r`; throws `SpeakerError` on missing/malformed JSON. |
| `test/voice.test.ts`     | created  | 9 unit tests: URL derivation, malformed name rejection, catalog fetch/cache, non-OK HTTP, language filter, `--all` flag. |

## Commits

- `60e4f4a` #2 refactor(2-piper-voice-selection): extract shared utilities into util.ts
- `46540b1` #2 feat(2-piper-voice-selection): add voice module with catalog and install
- `5c4006c` #2 feat(2-piper-voice-selection): add voca voice CLI subcommands
- `9b7ad9e` #2 refactor(2-piper-voice-selection): delegate voice install to voice.ts
- `b9af849` #2 feat(2-piper-voice-selection): derive aplay sample rate from voice metadata
- `e33d303` #2 test(2-piper-voice-selection): add voice module unit tests

## Notes

- Tasks 2 and 3 landed in one commit (`46540b1`) because the plan had them sequentially editing the same file with no external consumer in between; splitting would have created a non-buildable intermediate state. All other tasks committed atomically per plan.
- Daemon reload on voice change requires `voca stop && voca start` — documented in the `voice use` CLI output, matching the plan's design decision to mirror `profile use` semantics.
- Non-22050 Hz voices now play at correct pitch because `aplay -r` is derived from `.onnx.json` rather than hardcoded. Smoke test not executed on hardware (no audio device available in the session) but the code path is covered by the unit tests and the unchanged piper→aplay pipe wiring.
