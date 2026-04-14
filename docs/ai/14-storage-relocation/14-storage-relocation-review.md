# Review: 14-storage-relocation

**PR:** https://github.com/yokeloop/voca/pull/15
**Branch:** `issue-14-storage-relocation`
**Scope:** Fix all (1 Important + 8 Minor)

## Summary

Второй проход ревью после первого (см. историю git, коммит 05d0e2c). За это время в PR легли ещё 5 коммитов по bootstrap/language: интерактивный выбор языка перед выбором голоса, оконный `select()` для длинных списков, удаление хардкода `language` из `defaultConfig` и перенос дефолта в `transcriber.ts`. Ревью сосредоточилось на новых изменениях; ранее найденные проблемы (piperBin resolve, VOCA_HOME isAbsolute, воркфлоу mkdir/pointer и т.д.) из KNOWN_ISSUES исключены.

## Issues found: 9 (0 Critical / 1 Important / 8 Minor) — все исправлены

### Fixed (9)

| # | Severity | Category | Location | Description | Commit |
|---|----------|----------|----------|-------------|--------|
| 1 | Important (55) | bugs | `src/transcriber.ts:17` | Дефолт языка молча сменён `ru`→`en` для пользователей со старым `config.json` без поля `language`. Два источника истины: bootstrap писал в config, transcriber имел свой дефолт. Убрали дефолт из transcriber: при отсутствии `language` теперь бросается `TranscribeError` с предложением запустить `voca bootstrap` — миграция явная. | 63d1840 |
| 2 | Minor (40) | quality | `src/bootstrap.ts:244-246,306-310` | `writeConfig` вызывался дважды за один выбор голоса (при смене языка и при установке голоса). `setActiveVoice` стал синхронным, `selectVoice` пишет конфиг ровно один раз в конце любой ветки. | 63d1840 |
| 3 | Minor (35) | quality | `src/bootstrap.ts:283` | Параметр `_rl` у `selectVoice` больше не использовался после перехода на кастомный `select()`. Удалён, вызов в `installPiper` обновлён. | 63d1840 |
| 4 | Minor (30) | bugs | `src/bootstrap.ts:35` | `windowSize = Math.max(3, Math.min(len, rows-4))` при `rows<=4` возвращал 3, превышая высоту терминала. Вынесено в `computeWindowSize(len, rows)`: нижняя граница 1, далее `min(len, rows-4)`. | 63d1840 |
| 5 | Minor (30) | quality | `src/bootstrap.ts:260-265` | Построение массива языков из ~1000 записей каталога перед `new Set(...)`. Заменено на одиночный проход с накоплением в `Set`. | 63d1840 |
| 6 | Minor (25) | quality | `src/bootstrap.ts:11-13` | `defaultRoot()` возвращала константное значение — заменена на `const DEFAULT_ROOT` и используется напрямую в `promptStorageRoot`. | 63d1840 |
| 7 | Minor (25) | tests | — | Не было тестов на интерактивный bootstrap. Выделены чистые хелперы `languageOf` и `computeWindowSize` с экспортом, добавлен `test/bootstrap.test.ts` с 7 тестами (извлечение префикса языка, окно при малом терминале и обычном, `undefined` rows). Интерактивный `select()` по-прежнему не покрыт — выходит за рамки локального фикса. | 63d1840 |
| 8 | Minor (20) | documentation | `src/bootstrap.ts:17` | Комментарий к `FALLBACK_VOICES` дополнен: указан URL каталога HF и предупреждение о риске падения установки, если имя в списке протухнет. | 63d1840 |
| 9 | Minor (20) | style | `src/bootstrap.ts:269` | `KEEP` переименован в `CURRENT_LABEL`, ветка возврата текущего языка проверяется через `CURRENT_LABEL && selected === CURRENT_LABEL` вместо сравнения с потенциально `null`. | 63d1840 |

### Skipped (0)

Пользователь выбрал scope "Fix all" — ничего не отложено.

## Validation

- `npm run build` — clean
- `npm test` — 126/126 tests pass (12 test files; добавлен `test/bootstrap.test.ts` с 7 тестами; `test/transcriber.test.ts` обновлён — явная передача `language`, тест на missing-language вместо теста на дефолт `en`)

## Follow-ups

- Интерактивный `select()` и `promptLanguage` остаются без unit-покрытия: регрессии в рендере/обработке клавиш ловятся только вручную. Можно отдельной задачей добавить smoke-тест с моком `process.stdin`.
- `FALLBACK_VOICES` стоит периодически сверять с HF каталогом или валидировать именами в unit-тесте.
