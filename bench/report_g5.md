# Отчёт G5 — перебивание на колонках (VOCA-14, 1.1)

Стенд: этот хост (omarchy), 2026-11-12 — 2026-11-13. Инструмент — `bench/barge_in.py`; записи — `.local/g5/<run>/` (в репозиторий не кладутся).

**Итог.**

- **G5 — не проведён**: на стенде нет колонок, только гарнитура HyperX; Line Out пуст. Решение инженера 2026-11-13: «Отложить G5 до колонок».
- Пороги перебивания остаются умолчаниями `Settings`: `interruption_min_duration` 0.5, `interruption_min_words` 0, `aec_warmup` 3.0.
- Прогон подготовлен: запись, замер, подсчёт ложных перебиваний и сверка истории готовы и покрыты тестами (раздел 2); двухканальная запись проверена на стенде (раздел 3).
- F1 страницы VOCA-13 инженер подтвердил вживую; F2 не проверялся (раздел 8).

## 1. Версии и стенд

- livekit-agents 1.8.11 (`agent/uv.lock`), livekit-server 1.13.11, PipeWire 1.6.9 (`pw-record`, compiled with libpipewire 1.6.9).
- Стенд — пять сервисов `scripts/up.sh` из worktree VOCA-14 без `.env`, страница `http://127.0.0.1:8083/?device=host`.
- Устройства PipeWire: Line Out `alsa_output.pci-0000_00_1f.3.analog-stereo` (sink 45, порт `…:monitor_FL`) — колонки к нему не подключены; гарнитура `alsa_output.usb-Kingston_Technology_Company_HyperX_Cloud_Flight_Wireless-00.analog-stereo` (sink 34); микрофон `alsa_input.usb-Kingston_Technology_Company_HyperX_Cloud_Flight_Wireless-00.mono-fallback` (source 46, порт `…:capture_MONO`).

## 2. Чем подготовлен прогон

**JSONL агента** (`agent/src/voca/telemetry/jsonl.py`): строка `user_state` (`old`, `new`) на каждую смену состояния пользователя по VAD; в строке `turn` — `started_speaking_at` и `stopped_speaking_at` (секунды `time.time()`, из `assistant.metrics` фреймворка, `SP/voice/agent_activity.py:3970-3972`; `SP` = `agent/.venv/lib/python3.13/site-packages/livekit/agents/`). Строки `interruption` и `false_interruption` не менялись.

**`bench/barge_in.py`**:

- `record --run NAME [--mic SOURCE] [--speaker SINK]` — одна двухканальная запись `pw-record` 48 кГц без автоматических связей (узел `voca-g5`), связи `pw-link`: FL — микрофон, FR — monitor колонок; `audio.json` — часы начала и конца, устройства, порты и значения `VOCA_INTERRUPTION_MIN_DURATION`, `VOCA_INTERRUPTION_MIN_WORDS`, `VOCA_AEC_WARMUP` из окружения процесса агента.
- `analyze` — задержка «onset речи пользователя → прекращение старого звука» по звуку: огибающие 10 мс, задержка monitor → микрофон и усиление эха по опорному окну 2 с, onset — 3 кадра подряд на 10 дБ выше ожидаемого эха, прекращение — 300 мс тишины monitor ниже −60 dBFS; `delay = (прекращение на monitor + lag) − onset на микрофоне`. Метки JSONL (`agent_state` `speaking` → `listening` без строки `turn` в пределах 0.1 с) дают только окна поиска.
- `false --mode speech|silence` — ложные перебивания по JSONL: в речи агента `false_interruption`, паузы, перебитые `turn`, `user_input_transcribed` внутри речи и `speaking_s`; в тишине `user_input_transcribed`, `turn`, `thinking`, отдельно `vad_speaking`.
- `history` — отрезок monitor перебитой реплики распознаётся `recognize_batch` и сверяется с текстом истории: хвосты совпадают с точностью ±2 слова после нормализации чисел.

Тесты — `agent/tests/test_barge_in.py` (синтетика: onset, прекращение, lag и задержка в пределах ±10 мс, паузы, счётчики, сверка хвоста) и `agent/tests/test_telemetry.py`.

## 3. Что проверено на стенде

- Запись `dry` (2026-11-12): тон 440 Гц через `pw-play` на Line Out; во время записи `pw-link -l` показывает две связи `capture_MONO → voca-g5:input_FL` и `monitor_FL → voca-g5:input_FR`; пик FR −18.1 dBFS; `audio.json` содержит оба порта и `variant` со значениями `"default"`.
- Проверка микрофона в сессии (2026-11-12): пик FL −13.9 dBFS.
- Прогоны `md050` не состоялись (раздел 7). Записи `dry` и трёх попыток `md050` удалены без архива по решению лида: перебиваний в них нет, monitor пуст.

## 4. `min_duration`: нижняя граница

Перебивание срабатывает, когда `speech_duration` VAD достигает `min_duration` (`SP/voice/agent_activity.py:2520`). Silero начинает отсчёт речи только после `min_speech_duration` 0.05 с (`SP/../plugins/silero/vad.py:63,530-537`), поэтому значение ниже 0.05 не действует. Физически задержка «onset → прекращение» не меньше `min_duration`.

## 5. `min_words` = 0

По коду, без прогона: `SP/voice/agent_activity.py:2390-2399` при `min_words > 0` проверяет текущий транскрипт, а STT агента — `stt.StreamAdapter`, который выдаёт только FINAL после конца речи по VAD (`SP/stt/stream_adapter.py:129-156`). `min_words > 0` задерживает перебивание до ответа STT, то есть до конца фразы пользователя.

## 6. `aec_warmup`: поправка к исследованию

Поправка к § 2.3 и § 4.11 исследования VOCA-11 (там `aec_warmup` описан как «первые 3 с речи агента» вообще): прогрев одноразовый на сессию. Таймер ставится только при первом переходе в `speaking` (`SP/voice/agent_session.py:2255-2266`), по истечении остаток становится 0 (`:2146-2153`), заново взводится только при связывании участника и только когда значение не задано явно (`:2174-2175`, `:2186`); у нас оно задано (`Settings.aec_warmup`). Пока прогрев активен, перебивание игнорируется (`SP/voice/agent_activity.py:2380`), а звук пользователя для STT заменяется тишиной (`:1706-1726`). Формулировка `VOCA_AEC_WARMUP` в `.env.example` и `AGENTS.md` исправлена: «первые N с первого ответа сессии».

## 7. Сессия 2026-11-12 — 2026-11-13

- Первый прогон `md050` потерян: фоновый `record` остановился сам через 2 часа, раньше начала перебиваний.
- Второй прогон потерян: при включении гарнитура забрала вывод по умолчанию, агент звучал в наушниках, monitor Line Out показывал −120 dBFS.
- Третий прогон инженер остановил: колонок на хосте нет. Решение инженера 2026-11-13: «Отложить G5 до колонок».

## 8. F1/F2 страницы VOCA-13

- F1 — подтверждён инженером вживую: строка «агент» после окончания реплики становится окончательной. Версия браузера не записана.
- F2 — не проверялся.

## 9. Отход О3

Запасной путь `mode:"adaptive"` (`derive:eb1id854:§12`) не используется: adaptive — облачный детектор. Страховка от эха — `resume_false_interruption` True и `false_interruption_timeout` 2.0, умолчания фреймворка (`SP/voice/turn.py:191-199`). Риск § 6 исследования (синхронизатор оценивает позицию по слогам сырого текста с цифрами, `SP/voice/transcription/synchronizer.py:22,317-324`) остаётся непроверенным до прогона `history`.

## 10. Сценарий будущего прогона

Команды — в корне клона, где запущены пять сервисов. `B` = `uv run --project agent python bench/barge_in.py`; `R` = `B record --speaker <sink колонок> --mic <source микрофона> --run`. Перезапуск варианта: `scripts/down.sh agent`, затем `VOCA_INTERRUPTION_MIN_DURATION=<x> VOCA_AEC_WARMUP=<y> scripts/up.sh agent`; на странице «Отключиться», «Подключиться» (новая сессия).

Уроки сессии 2026-11-12:

- Гарнитура при включении забирает вывод по умолчанию. Перед каждым прогоном проверить, куда реально идёт поток браузера: `pactl list sink-inputs` — у потока страницы `Sink` должен быть sink колонок; иначе `wpctl set-default <id колонок>` (или `pactl move-sink-input <id> <sink колонок>`) и переподключение.
- До начала перебиваний убедиться, что monitor FR не −120: короткая запись `R check`, пока агент говорит, Enter — «пик FR» выше −30 dBFS, «пик FL» выше −60 dBFS; `rm -rf .local/g5/check`.
- У фоновой записи не должно быть лимита по времени: `record` запускается в своём терминале или в фоне без таймаута и живёт до Enter или SIGINT.

Шаги:

1. Вывод по умолчанию — колонки (`wpctl set-default <id колонок>`), ввод — микрофон; страница `http://127.0.0.1:8083/?device=host`, «Подключиться», микрофон разрешён; записать версию браузера; проверки из «Уроков».
2. F1 на первом ответе; F2 — удалить комнату `voca` через `livekit.api` (`LiveKitAPI("http://127.0.0.1:7880", "devkey", "secret").room.delete_room(DeleteRoomRequest(room="voca"))`): страница показывает «Подключиться», один клик подключает, агент отвечает.
3. Варианты `min_duration` (`aec_warmup` 3.0): `md050`, `md030`, `md015`, `md008` — 0.5, 0.3, 0.15, 0.08. В каждом: `R <run>`; «Расскажи подробно …», через ≈ 5 с ответа перебить новой фразой «Расскажи подробно …» о другом, три перебивания подряд в одной сессии, последний ответ дослушать; Enter; `B analyze --run <run>`.
4. Кандидат — наибольшее значение, у которого все три `valid` задержки ≤ 150 мс; иначе — с наименьшей максимальной задержкой.
5. Варианты `aec_warmup` при кандидате: `warm000`, `warm050`, `warm100`, `warm300` — 0, 0.5, 1.0, 3.0, по порядку: `R <run>`, 5 переподключений, в каждой свежей сессии один вопрос, тишина до конца ответа; Enter; `B false --run <run> --mode speech`; выбирается первое значение, чистое во всех пяти сессиях; если чистых нет — 3.0.
6. Итоговые значения: прогон `final`, три перебивания подряд; `B analyze --run final`, `B history --run final`.
7. 10 мин тишины: `R silence`, свежая сессия, тишина 10 мин; `B false --run silence --mode silence`.
8. 10 мин речи агента: `T=$(date +%s)`, `R speech`; вопрос за вопросом без перебиваний; ход — `B false --run speech-progress --mode speech --since $T --until $(date +%s)`; Enter только при `speaking_s` ≥ 600; затем `B false --run speech --mode speech`; `rm -rf .local/g5/speech-progress`.
9. Если окна 7–8 не чисты — следующий больший `min_duration` из шага 3, шаги 6–8 как `final2`, `silence2`, `speech2`; не выше 0.5.
10. Решение: G5 «пройден», если все три задержки `final` ≤ 150 мс, оба окна чисты и история сверена (±2 слова); иначе «не пройден» с лучшим значением. Выбранные значения — в `Settings`, `agent/tests/test_wiring.py`, `.env.example`, `AGENTS.md`, README 1.1; записи — в архив `~/Archive/voca/`.
