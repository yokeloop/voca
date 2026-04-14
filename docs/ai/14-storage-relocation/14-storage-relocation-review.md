# Review: 14-storage-relocation

**PR:** https://github.com/yokeloop/voca/pull/15
**Branch:** `issue-14-storage-relocation`
**Scope:** Fix Critical + Important; skip Minor

## Summary

PR переносит runtime-данные VOCA из `~/.openclaw/assistant/` в пользовательский корень (по умолчанию `~/.voca`) с discovery-цепочкой `VOCA_HOME` → pointer-файл → ошибка. Вся склейка путей централизована в `src/paths.ts`; path-зависимые модули резолвят пути лениво. Bootstrap получил Step 0 с prompt на путь. Добавлены `test/paths.test.ts` и глобальный `test/setup.ts` для seed `VOCA_HOME`. CLAUDE.md и README.md дополнены секциями по storage-layout и миграции.

## Issues found: 8 (0 Critical / 2 Important / 6 Minor)

### Fixed (2)

| # | Severity | Category | Location | Description | Commit |
|---|----------|----------|----------|-------------|--------|
| 1 | Important (70) | bugs | `src/config.ts:27-28` | `readConfig` резолвил `piperBin/piperModel` в абсолютные пути, а `writeConfig` сохранял их обратно, превращая относительные дефолты в жёстко привязанные к текущему корню. Ломало migration-сценарий из task verification. Теперь `readConfig` возвращает значения как есть; добавлены `resolvePiperBin(cfg)` / `resolvePiperModel(cfg)`; `useVoice` сохраняет `bin/<name>.onnx`. | 05d0e2c |
| 2 | Important (55) | bugs | `src/paths.ts:38-40` | `VOCA_HOME` принимался без проверки абсолютности, в отличие от pointer-файла. Добавлена проверка `path.isAbsolute` и тест. | 05d0e2c |

### Skipped (6, Minor)

| # | Severity | Category | Location | Description | Reason |
|---|----------|----------|----------|-------------|--------|
| 3 | Minor (40) | bugs | `src/config.ts:41-45` | Абсолютный путь без суффикса `.onnx` добавлял суффикс — покрыто изменением в #1 (теперь `resolvePiperModel` возвращает абсолют + `.onnx` или уже готовый `.onnx`). Фактически закрыто. | Closed by #1 |
| 4 | Minor (35) | quality | `src/bootstrap.ts:374-376` | `writePointerFile` перед `mkdir(root)` — при ошибке mkdir pointer указывает на несуществующий каталог. | Excluded by user (minor) |
| 5 | Minor (30) | tests | `test/setup.ts:6` | Tmp-каталог не удаляется после прогона. | Excluded by user (minor) |
| 6 | Minor (25) | style | `src/paths.ts:14` | `readFileSync` без комментария-обоснования. | Excluded by user (minor) |
| 7 | Minor (20) | documentation | `src/paths.ts` | Нет JSDoc на экспортируемых функциях центрального модуля. | Excluded by user (minor) |
| 8 | Minor (15) | quality | `src/bootstrap.ts:11` | `DEFAULT_ROOT` вычисляется на уровне модуля. | Excluded by user (minor) |

## Validation

- `npm run build` — clean
- `npm test` — 119/119 tests pass (3 new tests added for round-trip, absolute pass-through, VOCA_HOME validation)

## Follow-ups

Minor issues посты в PR как комментарии — можно добить отдельным PR при желании.
