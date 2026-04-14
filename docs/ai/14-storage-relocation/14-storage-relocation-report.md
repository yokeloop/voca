# Report: 14-storage-relocation

**Status:** done (12/12 tasks)
**Plan:** [14-storage-relocation-plan.md](./14-storage-relocation-plan.md)
**Branch:** `issue-14-storage-relocation`

## Summary

Runtime storage relocated out of `~/.openclaw/assistant/` into a user-chosen root (default `~/.voca`) discovered via `VOCA_HOME` env var, then pointer file at `~/.config/voca/root`. All path construction centralised in `src/paths.ts`; existing modules read the active root lazily at call time so tests can redirect it via `VOCA_HOME`. Bootstrap grows a Step 0 prompt that writes the pointer file and creates the root directory; config uses relative defaults (`bin/piper`, `bin/<voice>.onnx`) resolved against the root on read.

## Tasks

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Create `src/paths.ts` | ✅ | 21b84bf |
| 2 | Add `test/paths.test.ts` | ✅ | e7274f1 |
| 3 | Refactor `src/session.ts` | ✅ | a836e5e |
| 4 | Refactor `src/sounds.ts` | ✅ | a836e5e |
| 5 | Refactor `src/voice.ts` | ✅ | a836e5e |
| 6 | Refactor `src/config.ts` (relative defaults) | ✅ | a836e5e |
| 7 | Refactor `src/daemon.ts` | ✅ | a836e5e |
| 8 | Update `test/config.test.ts` | ✅ | acf0400 |
| 9 | Bootstrap Step 0 + refactor | ✅ | b06e0f8 |
| 10 | Update `src/cli.ts` imports | ✅ | b06e0f8 |
| 11 | Update CLAUDE.md + README.md | ✅ | 022c27b |
| 12 | Validation | ✅ | 059f3d1 (test setup fix) |

## Validation

- `npm run build` — clean.
- `npm test` — 116/116 tests pass across 11 files (added global `test/setup.ts` that seeds `VOCA_HOME` for suites that transitively load `paths.ts`).
- `grep -rn "openclaw/assistant"` — only doc mentions remain (CLAUDE.md Migration section, README.md migration note).

## Notes

- `docs/ai/14-storage-relocation/14-storage-relocation-task.md:Requirements` R1–R11 all met.
- No Polish/Document sub-agent passes were needed: the plan was precise enough that implementation went straight through, and doc updates landed as part of T11.
- Migration for existing users is manual and documented in CLAUDE.md and README.md — no auto-migration code, per the plan's constraint.

## Follow-ups

None blocking. A future task could auto-detect an old `~/.openclaw/assistant/` directory during bootstrap and offer to move it, but that is out of scope for #14.
