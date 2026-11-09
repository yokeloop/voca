# G7 — Hermes: Runs API, сессии, MCP mypi, отказы (VOCA-3, 0.4)

Итог: **G7 провален** по одному критерию из пяти — недоступный бэкенд не даёт `failed`, потому что профиль переключается на `fallback_providers`. Остальные четыре критерия пройдены. Решение по провалу — на приёмку эпика (раздел 8).

## 1. Стенд

- Дата прогона: 2026-11-09, 19:31–19:44 UTC.
- Hermes: `/health` → `{"status": "ok", "version": "0.22.2"}`; `hermes --version` — `Hermes Agent v0.22.2+1642.g3cbe7b1 (2026.9.24)`; установка — git checkout `~/.hermes/hermes-agent` на коммите `3cbe7b19db144d73b3ea55ddb1f5d8cd2913f008` (2026-10-18).
- Запуск: system-unit `hermes-gateway.service` на rpi, `ExecStart=… -m hermes_cli.main gateway run`, пользователь владельца, `Restart=always`, `TimeoutStopSec=210`, `KillMode=mixed`.
- Профиль: основной (default), `HERMES_HOME=~/.hermes` пользователя на rpi. Профили `masha`, `nastya`, `public` — отдельные gateway, не затронуты.
- Модель: `model.default: glm-5.3-flash`, `model.provider: custom:litellm`, `base_url: http://127.0.0.1:4000` (LiteLLM на rpi); в журнале агента runs обслужены `model=glm-5.3-flash provider=custom`. `runtime` в ответах Runs API этой версии пуст: `{"provider": "", "model": "", "route_source": "global"}`.
- `VOCA_HERMES_URL=https://hermes.priney.com` (Traefik на rpi, только tailnet, TLS Let's Encrypt). Без ключа `/v1/models` → 401.
- Изменения профиля, сделанные задачей:
  - `.env` профиля, в конце блок с комментарием `# VOCA-3 (2026-11-09)`: `API_SERVER_ENABLED=true`, `API_SERVER_KEY` (32 байта hex, значение только на rpi), `API_SERVER_HOST=172.20.0.1` (шлюз `docker_gwbridge`; не tailscale0 и не LAN), `API_SERVER_PORT=8642`. `API_SERVER_MODEL_NAME` не задан, `/v1/models` отдаёт `hermes-agent`.
  - `config.yaml` не изменён: временная запись `voca-unreachable` в `providers:` добавлялась на время прогона `backend-down` и удалена.
  - MCP mypi уже был подключён до задачи: `mcp_servers.mypi` (`http://100.108.140.49:8737/mcp`, `streamable_http`, `timeout: 180`) с существующим write-токеном mypi `hermes` (sender `global`). Новый токен не выпускался, блок не менялся. После рестарта gateway: `MCP server 'mypi' (HTTP): registered 9 tool(s)`.
  - Резервные копии до правок: `~/.hermes/.env.bak-voca3`, `~/.hermes/config.yaml.bak-voca3`.

## 2. Сверка с исходниками

Установленная версия `3cbe7b1` старше `fd267bb` (2026-11-03). Файлы установки: `gateway/platforms/api_server_runs.py` (1523 строки), `api_server.py` (4611), `api_server_run_idempotency.py` (235).

| Факт | Установлено (3cbe7b1) | fd267bb |
| --- | --- | --- |
| Приоритет `session_id` | `api_server_runs.py:758` `session_id = body.get("session_id") or stored_session_id`; `:781` «body session_id > response chain > X-Hermes-Session-Key» | совпадает (`:801`, `:824`) |
| `X-Hermes-Session-Id` на `/v1/runs` | в `api_server_runs.py` не читается; в `api_server.py` только заголовок ответа (`:3375`), CORS (`:698`) и `session_continuity_header` в capabilities (`:75`) | совпадает |
| `Idempotency-Key` | `:738` читается, 1–255 видимых ASCII; повтор — 202 с `Idempotency-Replayed: true` (`:501`); другое тело — 409 `idempotency_key_conflict` (`:512`) | совпадает |
| `RETENTION_SECONDS` | `api_server_run_idempotency.py:58` `24 * 60 * 60`, хранилище `$HERMES_HOME/runs_idempotency.db` (SQLite WAL) | совпадает |
| TTL статуса без ключа | `api_server.py:4343` `_RUN_STATUS_TTL = 3600`, только в памяти процесса | совпадает (`:3842`) |
| Поведение при рестарте | `_durable_run_status` (`api_server_runs.py:410`): нетерминальная запись с ключом и мёртвым владельцем становится `interrupted`; при штатной остановке drain (`restart_drain_timeout: 180` профиля) отменяет runs сам — наблюдение в разделе 3 | совпадает по коду |
| Буфер SSE | только в памяти: `BACKLOG_LIMIT = 1000`, `SUBSCRIBER_QUEUE_LIMIT = 256`, `_RUN_STREAM_TTL = 300` с; `last_seq` или `Last-Event-ID` | совпадает |

## 3. Сценарии

| Фаза | Что сделано | Наблюдение | Критерий | Итог |
| --- | --- | --- | --- | --- |
| `preflight` | `/health`, `/v1/models`, `/v1/capabilities` | 200; `version=0.22.2`; модели `hermes-agent`; `run_submission`, `runs_idempotency.supported`, `durable` — true; `retention_seconds=86400`; `run_unknown_resolution=true` | API доступен, ключ обязателен | PASS (`20261109T193107Z-preflight.json`) |
| `create` | run `sleep 30` с ключом, процесс вышел после первого `tool.started` | 202, `replayed=false`, `run_10ef746b3c524475864350f12ce4f3bb`; `tool.started` `terminal` на `seq=0` | — | PASS (`20261109T193111Z-create.json`) |
| `resume` | новый процесс, тот же POST и ключ, `events?last_seq=0` | 202, `replayed=true`, `Idempotency-Replayed: true`, тот же `run_id`; статус на момент возврата `running`; события `seq` 1..4 строго растут; `completed`, `output="Готово"` | один run на поручение | PASS (`20261109T193142Z-resume.json`) |
| `conflict` | тот же ключ, другое `input` | 409 `idempotency_key_conflict` | — | PASS (`20261109T193142Z-conflict.json`) |
| `session` | run A и B с `session_id`; run C с `X-Hermes-Session-Id`, без `session_id` в теле и без `X-Hermes-Session-Key` | B вспомнил слово, `session_id` A = B; run C получил `session_id = run_id` (`run_da70…`), `header_honored=False`; каждый run 2–6 с | — | PASS (`20261109T193153Z-session.json`) |
| `delegate` | поручение записать note в журнал VOCA-3 через MCP mypi | `tool_describe`, затем `tool.started`/`tool.completed` `mcp__mypi__note` (`error=false`); `completed`, `output="Записано"`; в журнале mypi `task:VOCA-3` запись `VOCA-3 G7 delegate bce19a4f00ca` (2026-11-09T19:32:17Z) | MCP mypi доступен; сырой вывод tools не в озвучке | PASS (`20261109T193218Z-delegate.json`) |
| `restart --phase start` | run из трёх `sleep 240` с ключом | 202, `run_a47aee73e263462fafa519f96f04bf17`, `tool.started` `terminal` | — | PASS (`20261109T193308Z-restart-start.json`) |
| рестарт gateway | `systemctl restart hermes-gateway` на rpi | stop 19:33:20Z; `Gateway drain timed out after 180.1s with … 1 api_server run(s)`; инструмент получил `[Command interrupted]`; SIGKILL и новый процесс 19:36:50Z; простой ≈ 3,5 мин | — | выполнено |
| `restart --phase verify` | `/health`, GET прерванного run, повтор POST, `/events`, GET прошлого `completed` | прерванный run — `cancelled`, `error` и `output` пусты (ожидалось `interrupted`/`unknown`); повтор с тем же ключом — 202, `replayed=true`, тот же `run_id`, статус `cancelled`, второго run нет; `/events` → 404; `run_10ef…` из `resume` → 200, `completed`, `output="Готово"` | результат доступен после рестарта | фаза FAIL только по ожидаемому статусу (`20261109T194258Z-restart-verify.json`; остальные наблюдения — `20261109T194317Z-restart-verify-rest.json`); критерий пройден |
| `backend-down` | run с `provider=voca-unreachable` (`http://127.0.0.1:9/v1`) | две попытки `APIConnectionError` (19:44:02, 19:44:05), затем `Fallback to ollama-cloud/glm-5.2`; `completed`, `output="да"` через ≈ 10 с; `runtime.provider` пуст, `runtime.requested.provider=voca-unreachable` | недоступный бэкенд даёт `failed` | FAIL (`20261109T194410Z-backend-down.json`) |

Логи лежат в `.local/bench/hermes/` клона и в репозиторий не кладутся. Все runs шли с `Idempotency-Key`. Ни один run не запросил одобрения (`approvals.mode: manual`, `sleep` не опасная команда).

Попутные факты:

- Длинное поручение фазы `restart` — три `sleep 240` вместо одного `sleep 120` по плану: с `restart_drain_timeout: 180` run на 120 с успел бы завершиться за время drain, а `terminal.timeout: 300` не даёт одной команде идти дольше 300 с.
- Run без `session_id` в теле, но с `X-Hermes-Session-Key: voca` продолжает последнюю сессию этого ключа: run `backend-down` попал в сессию `run_98c2…` от `delegate` (`execution_generation=2`).

## 4. Проверка первого часа «хранение результатов Runs API»

Решена наблюдением:

- run с `Idempotency-Key` хранится в `runs_idempotency.db` 86400 с (`retention_seconds` из `capabilities`) и переживает рестарт: `run_10ef…` после рестарта отдал `completed` и `output`, повтор POST прерванного run вернул тот же `run_id`;
- без ключа статус живёт только в памяти процесса 3600 с после терминального статуса и теряется при рестарте (по исходникам, `_RUN_STATUS_TTL`);
- буфер SSE — только в памяти, после рестарта `/v1/runs/{id}/events` отвечает 404.

Вывод для `delegation/hermes.py`: каждый `create_run` идёт с `Idempotency-Key` = `tool_call_id` (`derive:eb1id854:§5`); результат после рестарта берётся через `GET /v1/runs/{id}`, а не из SSE; SSE — только для прогресса, с `last_seq` при переподключении.

## 5. Сессия (для E3)

- Продолжение сессии работает через поле тела `session_id`: run B вспомнил слово из run A, `GET.session_id` совпал.
- `X-Hermes-Session-Key` задаёт область памяти; без `session_id` в теле run с этим заголовком продолжает последнюю сессию ключа.
- `header_honored=False`: на `/v1/runs` заголовок `X-Hermes-Session-Id` в установленной версии не действует — run C получил новую сессию `session_id = run_id`. Этот заголовок Hermes только отдаёт в ответах других эндпоинтов.
- Расхождение: карточка и `derive:8kki4ynb:§9` говорят о `X-Hermes-Session-Id`; в `derive:eb1id854` `create_run(key, text, session_id)` должен передавать `session_id` полем тела, а постоянный `X-Hermes-Session-Key` — заголовком.

## 6. Недоступный бэкенд и таймауты

- `failed` не наступает: через ≈ 10 с после POST run завершился `completed` на `fallback_providers` (`ollama-cloud`, `glm-5.2`, `https://ollama.com/v1`).
- Ошибка основного провайдера: `APIConnectionError` «Connection error.», две попытки с паузой ≈ 2,8 с, затем переключение.
- Профиль: `agent.api_max_retries: 3`, `auto_recovery_cycles` не задан (умолчание версии), `request_timeout_seconds` у временной записи 20, у `ollama-cloud` 600; `agent.gateway_timeout: 1800`.
- API этой версии не сообщает о переключении: `runtime.provider`/`model` пусты, `runtime.requested` содержит запрошенного провайдера. Клиент не отличит ответ fallback от ответа основной модели.
- Рекомендация: клиентский дедлайн `hermes_run_timeout` и `POST /v1/runs/{id}/stop` по нему нужны (вечный `running` возможен, если откажет и fallback); поведение «`failed` при недоступном LiteLLM» в этом профиле требует решения (раздел 8).

## 7. Маппинг статусов Hermes → `TaskStatus`

Черновик для `delegation/hermes.py`:

| Hermes | TaskStatus | Примечание |
| --- | --- | --- |
| POST не подтверждён (сеть, 5xx, 503 `runtime_draining`) | `QUEUED` | повтор с тем же ключом |
| `started` (ответ POST), `queued`, `running`, `stopping` | `RUNNING` | |
| `waiting_for_approval` | `RUNNING` | в `TaskStatus` нет ожидания пользователя — вопрос для E3 |
| `completed` | `SUCCEEDED` | `output` → `result`; может быть ответом fallback-провайдера (раздел 6) |
| `failed` | `FAILED` | `error` из run |
| `cancelled` | `FAILED` | `error = "cancelled"`; исправлено по наблюдению: так же заканчивается run, прерванный штатным рестартом gateway (drain), `error` у него пуст |
| `interrupted` | `FAILED` | `error` из run, без автоповтора; по исходникам — run с ключом, чей процесс-владелец умер без drain |
| `unknown` (канонический режим) | `UNKNOWN` | `resolve-unknown`, затем `FAILED` |
| 404 на `GET` после рестарта (run без ключа) | `FAILED` | `error = "lost"` |

## 8. Статус G7

| Критерий (`derive:8kki4ynb:§9`) | Статус | Основание |
| --- | --- | --- |
| Один run на поручение | пройден | `resume` и повтор после рестарта вернули тот же `run_id`, `replayed=true` |
| Результат доступен после рестарта | пройден | `run_10ef…` после рестарта gateway — `completed` с `output`; прерванный run виден как `cancelled` |
| Недоступный бэкенд даёт `failed` | провален | профиль переключился на `fallback_providers` и вернул `completed` |
| MCP mypi доступен | пройден | `mcp__mypi__note` в SSE, запись `VOCA-3 G7 delegate bce19a4f00ca` в журнале mypi |
| Сырой вывод tools не попадает в озвучку | пройден | `output` — одно слово («Записано», «Готово»); `preview` у `tool.completed` пуст (`tool_preview_length: 0` профиля), поэтому проверка вхождения preview не понадобилась |

**Итог G7: провален** по критерию «недоступный бэкенд даёт `failed`». Запасные пути `derive:eb1id854:§12` для G7 описаны только для сессии и хранения результата. Для этого пункта решение на приёмку эпика — один из вариантов:

- принять fallback как штатное поведение профиля (поручение выполняет другая модель, клиент об этом не знает), а `failed` ждать только при отказе всей цепочки — с клиентским дедлайном и `/stop`;
- выключить `fallback_providers` основного профиля (изменение профиля владельца, вне задачи) и повторить `backend-down`;
- запускать поручения VOCA на отдельном профиле Hermes без fallback.
