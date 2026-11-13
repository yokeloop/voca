# Отчёт G6 — сквозная задержка, телефон и закрытие E1 (VOCA-16, 1.2)

Стенд: этот хост (omarchy), 2026-11-13. Инструмент — `bench/report.py`; снимки прогонов — `.local/g6/<run>/` (в репозиторий не кладутся), архив — раздел 16.

**Итог.**

- **G6 — не пройден, bottleneck TTS TTFB.** Критерий (`derive:8kki4ynb:§9`): p50 end-of-speech → first audio ≤ 800 мс, p95 ≤ 1500 мс. Замер — один прогон `mac-base`, 13 реплик с удалённого Mac в tailnet: `e2e_latency_ms` агента p50 2855 / p95 3738 мс, `client_e2e_ms` страницы p50 3635 / p95 4681 мс. Самое тяжёлое звено разложения — `tts_ttfb` p50 938 мс (40 % суммы `eou + llm_ttft + tts_ttfb`).
- Решение инженера 2026-11-13 на вопрос с этими цифрами: «Зафиксировать, закрыть E1». Следующие шаги — отдельные будущие задачи: короче первый фрагмент или быстрее TTS; почему медиа идёт по TCP (раздел 12). E1 закрывается.
- Объём замера сокращён инженером: «давай 12 и только с удаленного мак, с телефона не буду делать». Хост, телефон и рычаги не замерены (раздел 14).
- С телефона (iphone, сотовая сеть, Tailscale) разговор работает в обе стороны, медиа — UDP 7882 напрямую (раздел 12).

## 1. Итог

| Устройство | Фаза | n | `e2e` p50 / p95, мс | `client_e2e` p50 / p95, мс |
| --- | --- | --- | --- | --- |
| mac | all | 13 | 2855 / 3738 | 3635 / 4681 |

Хоста и телефона в таблице нет: прогоны `host-base` и `phone-base` не делались по решению инженера. Статус — по правилу плана (пункт 18.8): p50 `e2e` 2855 > 800 мс и p95 3738 > 1500 мс → «не пройден, bottleneck TTS TTFB». Решение инженера, 2026-11-13: «Зафиксировать, закрыть E1».

## 2. Версии и стенд

- Код — `task/VOCA-16-e1-phone-g6` на `45aafba` (страница с замером, агент со строкой `client`); livekit-agents 1.8.11 (`agent/uv.lock`), livekit-server 1.13.11, livekit-client 2.23.0, llama.cpp b12167 с `lfm2.5-2.6b`, audio.cpp 0.9.4 (Parakeet q8_0, Supertonic 3 q8_0, голос F4).
- Стенд — пять сервисов `scripts/up.sh` из worktree VOCA-16 с доменным `.env` (блок `voca.priney.com`: `VOCA_WEB_HOST` и `VOCA_LIVEKIT_NODE_IP` = `100.108.140.49`, `VOCA_PUBLIC_LIVEKIT_URL=wss://voca.priney.com`). Агент на умолчаниях (`run.json` прогона: `env` пустой, `variant` `base`).
- Клиент замера — MacBook Pro в tailnet (`mac`, `100.110.133.108`), страница `https://voca.priney.com/?device=mac`; браузер по `ClientInfo` в логе livekit-server — Chrome 156 на macOS (строка ОС `Mac OS X 10.15.7` — замороженный user agent, не версия системы). Браузер инженер не называл.
- Телефон — iphone (`100.118.233.80`), по `ClientInfo` — iOS 18.7, Mobile Safari 26.6.1; страница `https://voca.priney.com/?device=phone`.

## 3. Отход стенда

- План вёл хост в гарнитуре HyperX вместо колонок (колонок нет, G5 отложен решением инженера 2026-11-13 «Отложить G5 до колонок»). Хостовых прогонов в итоге нет, замер — с удалённого Mac; повтор на колонках — часть G5.
- Перебивание на динамике телефона (`phone-barge`) не проверялось: прогон не делался по решению инженера (раздел 14).

## 4. Настройки

Умолчания `Settings`, прогон их не менял: `VOCA_LLM_THINKING_BUDGET` 128, `VOCA_VAD_MIN_SILENCE` 0.3, `VOCA_INTERRUPTION_MIN_DURATION` 0.5, `VOCA_INTERRUPTION_MIN_WORDS` 0, `VOCA_AEC_WARMUP` 3.0, `VOCA_ENDPOINTING_MIN_DELAY` 0.5, `VOCA_PREEMPTIVE_GENERATION` false. Варианты рычагов (`min_delay=0.3`, `preemptive=true`) не запускались.

## 5. Протокол

- Корпус `bench/corpus/g6_turns.txt`, голос инженера: 2 сессии по 6 реплик — строки 1–6 и 7–12. Новая сессия — «Отключиться», строка `close` в `.local/events.jsonl`, «Подключиться»; cold — первая реплика сессии (поле `cold` строки `turn`).
- Окно прогона — `report.py start`/`stop`: `mac-base` 1794576135.359–1794577733.037; 195 строк, 13 `turn`, 2 сессии, 12 `client`.
- 13-я строка `turn` — лишняя: первая (cold) реплика первой сессии распознана как «Yeah, yeah.» (ответ «Привет! Чем могу помочь?»), до строки корпуса 1. Строка осталась в снимке и в статистике, поэтому cold первой сессии — не реплика корпуса. Повторённых реплик лид не записывал.
- `e2e_latency_ms` = `null` — 0, перебитых — 0.

## 6. p50/p95 «устройство × cold/warm»

`uv run --project agent python bench/report.py summary --run mac-base`; повторный запуск совпал с `summary.json` сессии.

| вариант | устройство | фаза | n | e2e null | перебито | e2e p50 | e2e p95 | client p50 | client p95 | client null | eou p50/p95 | stt p50/p95 | llm_ttft p50/p95 | first_sentence p50/p95 | tts_ttfb p50/p95 | prepare p50/p95 | разложение p50/p95 | Markdown, % | медиана, симв. | leftover_digits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | mac | cold | 2 | 0 | 0 | 3308 | 4099 | 5159 | 5159 | 1 | 657/695 | 657/694 | 905/916 | 1184/1386 | 1460/2089 | 9/11 | 3022/3625 | 0 | 121 | 0 |
| base | mac | warm | 11 | 0 | 0 | 2855 | 3366 | 3630 | 4210 | 0 | 525/583 | 525/582 | 833/876 | 1316/1633 | 938/1560 | 5/60 | 2287/2937 | 9 | 273 | 0 |
| base | mac | all | 13 | 0 | 0 | 2855 | 3738 | 3635 | 4681 | 1 | 528/652 | 527/652 | 834/903 | 1316/1631 | 938/1837 | 6/59 | 2317/3294 | 8 | 224 | 0 |

У cold 2 значения (у `client` — 1): p95 по ним условен. `client без пары: mac-base 0`.

## 7. Замер в странице

- Метод (`web/src/timing.ts`, `AGENTS.md` «Client channel»): пара открывается, когда `lk.agent.state` агента переходит в `thinking` (или в `speaking` прямо из `listening`), одна пара на переход. Конец речи — последний кадр микрофона до перехода выше и шумового пола (10-й перцентиль за 3 с) + 15 дБ, и пика реплики − 20 дБ; реплика не короче 150 мс; пока агент `speaking`, микрофон не учитывается. Первый звук — первый кадр после перехода, где удалённый трек агента два тика по 10 мс выше −50 dBFS. Разность уходит агенту по `voca.metrics`, агент пишет строку `client`; `report.py` сопоставляет её со строкой `turn` по `(session, turn)`.
- `client_e2e − e2e` по 12 парам: p50 787 мс, от 592 до 972 мс. Сюда входят uplink звука пользователя до агента, downlink первого кадра до страницы, jitter-буфер и декодирование в браузере, а также разница между концом речи по порогу страницы и по VAD агента.
- Ограничения: звук агента снимается в WebAudio до устройства вывода (задержка вывода не входит); микрофон — после эхоподавления браузера; конец речи — по порогу, а не по VAD агента.
- Строк `client` без пары — 0; одна строка `turn` без `client` — лишняя cold-реплика «Yeah, yeah.» (раздел 5).
- Первая версия замера на разогреве хоста давала ложные пары (конец речи держался на шуме, лишние пары на паузах между предложениями агента); исправлено до прогона (`45aafba`), `mac-base` снят на исправленной странице. Разогрев телефона со строкой `client` не делался.

## 8. Звенья и разложение (О8)

| Звено | p50, мс | p95, мс |
| --- | --- | --- |
| `end_of_turn_delay` (eou) | 528 | 652 |
| `transcription_delay` | 527 | 652 |
| `llm_ttft` | 834 | 903 |
| `first_sentence` | 1316 | 1631 |
| `tts_ttfb` | 938 | 1837 |
| `prepare` | 6 | 59 |
| сумма `eou + llm_ttft + tts_ttfb` | 2317 | 3294 |
| `e2e_latency` | 2855 | 3738 |

- Сумма derive (2317) меньше `e2e` (2855) на ≈ 540 мс. Причина — ожидание первого фрагмента: TTS получает текст не с первым токеном content (`llm_ttft`), а с первым фрагментом (`first_sentence`), который приходит позже — p50 разности `first_sentence − llm_ttft` по строкам 491 мс. Сумма `eou + first_sentence + tts_ttfb` по строкам — p50 2852, p95 3735 мс, почти точно `e2e`. Перекрытие звеньев (§ 5) видно в том, что `transcription_delay` ≈ `eou`: STT идёт внутри ожидания конца реплики и отдельным слагаемым не входит.
- Против оценки § 5 исследования (≈ 2200–2800 мс) — на верхней границе; против VOCA-13 (`e2e` p50 2300, `tts_ttfb` p50 697) — на ≈ 550 мс хуже, в основном за счёт `tts_ttfb` и более длинных первых фрагментов.

## 9. Рычаги

Не замерены, решение инженера: прогоны `host-md03` (`VOCA_ENDPOINTING_MIN_DELAY=0.3`) и `host-pre` (`VOCA_PREEMPTIVE_GENERATION=true`) не делались, `levers.json` нет. По разложению `min_delay` может снять не больше доли `eou` (p50 528 мс); bottleneck остаётся в TTS.

## 10. Формат ответа (§ 4.4)

- Markdown (`markdown_removed > 0`) — в 1 ответе из 13 (8 %) против 19 из 20 у E0; медиана длины `text` — 224 символа против 676 у E0.
- Ответ с Markdown — на «Чем полезна прогулка перед сном?», список из шести пунктов «- …» (`markdown_removed` 6); фильтры убрали разметку до TTS.
- `leftover_digits` всего — 0.

## 11. Два хода (§ 4.5)

Механика двух ходов в E1 не применялась: модель думает всегда, хода A без рассуждения нет, вторая модель исключена решением инженера (2026-11-12, § 4.5).

## 12. Телефон и сеть

- **Телефон.** iphone (`100.118.233.80`, iOS 18.7, Mobile Safari 26.6.1), сотовая сеть с Tailscale, `https://voca.priney.com/?device=phone`, до версии 2 плана. Слова инженера: «Ну я с агентом поговорил, он меня слышал и я его слышал. Запись я включал экрана но она не пишет звук…». `phone-net/livekit.txt`: у iOS-клиента 2 строки `participant active … connectionType: udp`, выбранные пары ICE — `localProtocol udp`, `localPort 7882`, `remoteAddress 100.118.233.…`. `tailscale ping iphone` — `pong from iphone (100.118.233.80) via 10.22.116.87:36773 in 86ms`: напрямую, без DERP. `scripts/check-rtc.sh https://voca.priney.com` — `200`/`200`.
- Три строки `user-phone` с `connectionType: tcp` в том же файле (13:18–13:19) — не телефон: их `ClientInfo` — Chrome 156 на macOS, адрес пары — `fd7a:115c:a1e0::4b3a:856c`, IPv6 tailnet MacBook Pro. Это Mac, открытый с `?device=phone`.
- **Находка: медиа Mac идут по TCP.** `mac-net/livekit.txt`: 5 из 5 `participant active` у `user-mac` — `connectionType: tcp`; выбранная пара — `localProtocol tcp`, `localPort 7881`, `localAddress fd7a:115c:a1e0::4d2d:8c32` (IPv6 tailnet omarchy) ↔ `remoteCandidateType prflx`, `remoteAddress fd7a:115c:a1e0::4b3a:856c`, хотя кандидаты udp4/udp6 7882 предлагались. `tailscale ping mac` — `via 192.168.1.7:41641 in 3ms` (напрямую по LAN). Маршрут не менялся; почему Chrome на Mac выбирает TCP 7881 по IPv6 — отдельная будущая задача.
- Перебивание на динамике телефона (`phone-barge`) не проверялось (раздел 14).
- Отход О6: маршрут `voca.priney.com` (Traefik `/` и `/rtc` на rpi, медиа напрямую на `100.108.140.49`) без `tailscale serve`.

## 13. Bottleneck и следующий шаг

- Bottleneck — `tts_ttfb`: p50 938 мс, 40 % суммы разложения (2317), p95 1837 мс — самый широкий хвост. Вместе с ожиданием первого фрагмента (≈ 490 мс) синтез первого фрагмента даёт ≈ 1.4 с из 2.86.
- Решение инженера 2026-11-13: «Зафиксировать, закрыть E1». Следующие шаги — отдельные будущие задачи вне VOCA-16: короче первый фрагмент или быстрее TTS; почему медиа идёт по TCP. Умолчания `Settings` не меняются.

## 14. Отличия от протокола карточки

- Walkthrough-записи нет, калибровки записью нет. Ответы инженера 2026-11-13: на «Как мерим G6?» — «Добавить замер в страницу»; на «Видео walkthrough (для архива, не для замера) — оставляем?» — «Убрать». Запись экрана iOS звук не захватывает.
- Объём замера сокращён инженером 2026-11-13: «давай 12 и только с удаленного мак, с телефона не буду делать». Раньше, о сценарии сессии: «я не понял а зачем это? и где это вопросы? и почему их так много?». Поэтому не делались: `host-base` (30 реплик с хоста), рычаги `host-md03` и `host-pre`, `phone-base` (30 реплик с телефона), `phone-barge`. Вместо 30 + 30 реплик — 12 (и одна лишняя) с Mac.
- Два прогона `host-base` прерваны до реплик (без строк `turn`) и сохранены как `stale-host-base-v1` и `stale-host-base-v2`; в статистику не входят.

## 15. Команды

В worktree, `R` = `uv run --project agent python bench/report.py`; выдержки сети — по сценарию плана (пункт 18.7):

```sh
R start --run mac-base
# 2 сессии × 6 реплик с Mac: https://voca.priney.com/?device=mac
R stop --run mac-base
R summary --run mac-base
grep -E 'processing join request|participant active|selected ICE candidate pair' .local/logs/livekit.log | grep -i 'user-mac\|100.110.133.108' > .local/g6/mac-net/livekit.txt
grep -E 'processing join request|participant active|selected ICE candidate pair' .local/logs/livekit.log | grep -i 'user-phone\|100.118.233.80' > .local/g6/phone-net/livekit.txt
tailscale status > .local/g6/mac-net/tailscale-status.txt
tailscale ping -c 5 mac > .local/g6/mac-net/tailscale-ping.txt
tailscale status > .local/g6/phone-net/tailscale-status.txt
tailscale ping -c 5 iphone > .local/g6/phone-net/tailscale-ping.txt
tar -cf ~/Archive/voca/VOCA-16-g6.tar -C .local g6
sha256sum ~/Archive/voca/VOCA-16-g6.tar
```

## 16. Записи

- `.local/g6/mac-base/events.jsonl` — sha256 `856a96d1a3ab938f9b0128fbf3d306bd05a85ac27bf1155d4d03704c15f3aac9`.
- `.local/g6/stale-host-base-v2/events.jsonl` (прерванный прогон, одна строка `close`) — sha256 `459eafa969de9931985c2508b30f80a24c400ca9400e53918151b6063e91f14b`.
- Архив `~/Archive/voca/VOCA-16-g6.tar` — sha256 `a6b31716275a970612ae853f11c367737a67309bbb9ad2ff38ab0774774d72ce`: `g6/mac-base/{run.json,events.jsonl}`, `g6/summary.json`, `g6/mac-net/*`, `g6/phone-net/*`, `g6/session-notes.md`, `g6/stale-host-base-v1/run.json`, `g6/stale-host-base-v2/{run.json,events.jsonl}`.
- Ограничение: запись экрана iOS звук не захватывает; walkthrough-записей нет (раздел 14).
