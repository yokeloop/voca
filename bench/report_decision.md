# Модель решений голосового подтверждения (VOCA-36)

## История: CPU, вопрос `extra` (первый прогон)

Дата: 2026-11-17. Модель — LiquidAI d1-2B, файл `d1-2B-Q4_K_M.gguf` (1 099 629 888 байт, sha256 `adca5789ad30ae6027e8e936749bb9f19673097492ab26fa1c5b69643ca50b4b`), ревизия `LiquidAI/d1-2B-GGUF@4a181094d1b6addfaa3a0956f78e7ab2aa406a0c`. Сервис — `scripts/decision.sh`: llama.cpp b12167, `-c 2048 --parallel 1 -t 4 -ngl 0 --cache-ram 256`, `CUDA_VISIBLE_DEVICES=` (на CPU, в `nvidia-smi` процесса нет), порт 8086, `POST /v1/systemone`. CPU — Intel Core i5-10600KF (6 ядер, 12 потоков); одновременно подняты llm `bolt-e4b` на GPU и audio.cpp (6 потоков). Вопрос, пороги и описания вердиктов — умолчания `[decision]` (`threshold` 0,6, `extra_threshold` 0,5). Корпус — `bench/corpus/decision.json` (20 фраз), скрипт — `bench/decision_ru.py` (прогрев, 3 прогона).

## Итог

| Прогон | Итог | p50 | p95 | Сбои |
|---|---|---|---|---|
| умолчание `timeout_s` 4 с | **0/20** | 4005 мс | 4006 мс | 60 из 60 — `timeout` |
| `VOCA_DECISION__TIMEOUT_S=30` (только для замера) | **14/20** | 4486 мс | 5368 мс | 0 |

Один вызов — 538 входных токенов (состояние и два вопроса) и ~4,3–5,4 с на CPU. При тайм-ауте 4 с все вызовы обрываются, и агент всегда идёт по запасному пути. Ложных `approve` нет: ни в одном кейсе без `approve` в ожидании модель не выбрала `approve`.

Цифры ниже границы шага 30 плана (18/20, p95 ≤ 2000 мс): вопрос инженеру. Порог, тексты, тайм-аут и модель не подгонялись.

## Кейсы (прогон с тайм-аутом 30 с, первый круг)

| id | фраза | ожидание | вердикт | выбор | уверенность | extra | мс | итог |
|---|---|---|---|---|---|---|---|---|
| d01 | «Ну окей, давай подтвердим его.» | approve | approve | approve | 0.956 | нет | 4587 | PASS |
| d02 | «Подтверждаю.» | approve | approve | approve | 0.975 | нет | 4364 | PASS |
| d03 | «Ммм, подтверждаю, хорошо. Давай.» | unclear, approve | approve | approve | 0.967 | нет | 4846 | PASS |
| d04 | «Нет, не отклоняй» | unclear, approve | unclear | approve | 0.560 | нет | 5073 | PASS |
| d05 | «Не надо это подтверждать» | deny | deny | deny | 0.892 | нет | 4823 | PASS |
| d06 | «Отклоняю, отмени вообще задачу» | deny | deny | deny | 0.976 | нет | 5368 | FAIL |
| d07 | «А что он хочет удалить?» | unrelated | unclear | unrelated | 0.279 | нет | 4724 | FAIL |
| d08 | «Повтори запрос» | repeat | repeat | repeat | 0.909 | нет | 4386 | PASS |
| d09 | «Мне надо, чтобы ты его подтвердил» | approve | approve | approve | 0.779 | нет | 4361 | PASS |
| d10 | «Почему ещё не подтверждено?» | unrelated, unclear | unclear | unclear | 0.292 | нет | 4491 | FAIL |
| d11 | «Давай, разрешаю» | approve | approve | approve | 0.961 | нет | 4287 | PASS |
| d12 | «Стоп, не надо» | deny | deny | deny | 0.980 | нет | 4274 | PASS |
| d13 | «Отклоняю» | deny | deny | deny | 0.939 | нет | 4315 | PASS |
| d14 | «Да» | approve | approve | approve | 0.964 | нет | 4346 | PASS |
| d15 | «Нет» | deny | deny | deny | 0.911 | нет | 4370 | PASS |
| d16 | «Подтверждаю, и скажи потом, сколько времени» | approve | approve | approve | 0.947 | нет | 4586 | FAIL |
| d17 | «Что с поручениями?» | unrelated | unrelated | unrelated | 0.759 | нет | 4525 | FAIL |
| d18 | «Ещё раз, что он просит?» | repeat, unrelated | repeat | repeat | 0.833 | нет | 4529 | PASS |
| d19 | «Подожди, я подумаю» | unclear, unrelated | unclear | unclear | 0.747 | нет | 4475 | PASS |
| d20 | «Какая сегодня погода?» | unrelated | unrelated | unrelated | 0.884 | нет | 4481 | FAIL |

## Провалы

- `d06` «Отклоняю, отмени вообще задачу»: ожидание deny, получено `deny` (выбор `deny`, уверенность 0.976, extra нет).
- `d07` «А что он хочет удалить?»: ожидание unrelated, получено `unclear` (выбор `unrelated`, уверенность 0.279, extra нет).
- `d10` «Почему ещё не подтверждено?»: ожидание unrelated, unclear, получено `unclear` (выбор `unclear`, уверенность 0.292, extra нет).
- `d16` «Подтверждаю, и скажи потом, сколько времени»: ожидание approve, получено `approve` (выбор `approve`, уверенность 0.947, extra нет).
- `d17` «Что с поручениями?»: ожидание unrelated, получено `unrelated` (выбор `unrelated`, уверенность 0.759, extra нет).
- `d20` «Какая сегодня погода?»: ожидание unrelated, получено `unrelated` (выбор `unrelated`, уверенность 0.884, extra нет).

Все провалы, кроме `d07`, — вопрос `extra` («есть ли во фразе ещё вопрос, просьба или поручение»): `noul` ниже 0,5 у всех двадцати фраз, в том числе у вопросов и фраз с поручением. `d07` — верный выбор `unrelated` с уверенностью ниже порога 0,6.

Файлы: `.local/bench/decision/decision.json` (прогон 30 с), `.local/bench/decision/decision-timeout4.json` (прогон 4 с); выводы — папка задачи VOCA-36 `stand/bench-decision.txt`, `stand/bench-decision-timeout30.txt`.

## GPU и вердикты `approve_more`/`deny_more` (2026-11-17)

**Решение инженера 2026-11-17** по итогам прогона на CPU: «да так и делаем и переносим на видеокарту». Сервис `decision` работает на GPU: `scripts/decision.sh` с `-ngl $VOCA_DECISION_NGL` (умолчание 99; `VOCA_DECISION_NGL=0` — CPU), ~1,3 ГБ VRAM рядом с `bolt-e4b`. Вопрос `noul` «extra» удалён: вместо него у выбора два варианта `approve_more` и `deny_more` (ответ на запрос и ещё вопрос, просьба или поручение во фразе). `timeout_s` — 1,5 с: вызов на GPU занимает ~30 мс, запас — на холодный кэш и занятый GPU, при этом пауза перед ответом остаётся в пределах 2 с. Ожидания корпуса: `d06` — `deny_more`, `d16` — `approve_more`, `d07`/`d17`/`d20` — `unrelated`, `d10` — `unrelated` или `unclear`. Умолчания `[decision]`, порог 0,6, тайм-аут по умолчанию.

**decision 16/20, p50 30 мс, p95 32 мс** (прогонов 3, сбоев 0). Ложных `approve` нет. Вывод — папка задачи VOCA-36 `stand/bench-decision-gpu.txt`.

| id | фраза | ожидание | вердикт | выбор | уверенность | мс | итог |
|---|---|---|---|---|---|---|---|
| d01 | «Ну окей, давай подтвердим его.» | approve | approve | approve | 0.781 | 34 | PASS |
| d02 | «Подтверждаю.» | approve | approve | approve | 0.846 | 32 | PASS |
| d03 | «Ммм, подтверждаю, хорошо. Давай.» | unclear, approve | approve | approve | 0.848 | 31 | PASS |
| d04 | «Нет, не отклоняй» | unclear, approve | unclear | deny | 0.359 | 32 | PASS |
| d05 | «Не надо это подтверждать» | deny | deny | deny | 0.819 | 29 | PASS |
| d06 | «Отклоняю, отмени вообще задачу» | deny_more | deny | deny | 0.951 | 30 | FAIL |
| d07 | «А что он хочет удалить?» | unrelated | unclear | unrelated | 0.206 | 28 | FAIL |
| d08 | «Повтори запрос» | repeat | repeat | repeat | 0.921 | 30 | PASS |
| d09 | «Мне надо, чтобы ты его подтвердил» | approve | unclear | approve | 0.531 | 30 | FAIL |
| d10 | «Почему ещё не подтверждено?» | unrelated, unclear | unclear | unrelated | 0.253 | 31 | PASS |
| d11 | «Давай, разрешаю» | approve | approve | approve | 0.849 | 31 | PASS |
| d12 | «Стоп, не надо» | deny | deny | deny | 0.910 | 30 | PASS |
| d13 | «Отклоняю» | deny | deny | deny | 0.900 | 33 | PASS |
| d14 | «Да» | approve | approve | approve | 0.819 | 30 | PASS |
| d15 | «Нет» | deny | deny | deny | 0.836 | 32 | PASS |
| d16 | «Подтверждаю, и скажи потом, сколько времени» | approve_more | unclear | approve | 0.532 | 29 | FAIL |
| d17 | «Что с поручениями?» | unrelated | unrelated | unrelated | 0.660 | 29 | PASS |
| d18 | «Ещё раз, что он просит?» | repeat, unrelated | repeat | repeat | 0.712 | 31 | PASS |
| d19 | «Подожди, я подумаю» | unclear, unrelated | unclear | unclear | 0.679 | 31 | PASS |
| d20 | «Какая сегодня погода?» | unrelated | unrelated | unrelated | 0.904 | 30 | PASS |

### Провалы

- `d06` «Отклоняю, отмени вообще задачу»: ожидание deny_more, получено `deny` (выбор `deny`, уверенность 0.951).
- `d07` «А что он хочет удалить?»: ожидание unrelated, получено `unclear` (выбор `unrelated`, уверенность 0.206).
- `d09` «Мне надо, чтобы ты его подтвердил»: ожидание approve, получено `unclear` (выбор `approve`, уверенность 0.531).
- `d16` «Подтверждаю, и скажи потом, сколько времени»: ожидание approve_more, получено `unclear` (выбор `approve`, уверенность 0.532).

`d06`/`d16`: варианты `*_more` модель не выбирает — выбирает просто `deny`/`approve` (у `d16` с уверенностью ниже порога). `d07`, `d09`: верный выбор с уверенностью ниже порога 0,6. У `d04` «Нет, не отклоняй» выбран `deny` (0,359), ниже порога — итог `unclear`, а не отказ.

Итог ниже границы шага 30 (18/20), вопрос вынесен инженеру. **Решение инженера 2026-11-17: «принять 16/20 как есть».** Порог 0,6, вердикты, тексты и модель d1-2B остаются без изменений; вердикт бенча — 16/20 принят.
