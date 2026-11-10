# Отчёт G1, G2 — стенд аудио (VOCA-5, 0.2)

Стенд: этот хост (omarchy), 2026-11-10. Прогоны — `bench/stt_ru.py`, `bench/tts_ru.py`, `bench/audio_parallel.py`; таблицы и аудио — `.local/bench/{stt,tts,parallel}/`, лог сервера — `.local/logs/audio.log` (в репозиторий не кладутся). Все числа ниже — из этих файлов.

**Итог.**

- «STT батчем» — **решено**: `openai.STT` работает и прямым `recognize`, и через `stt.StreamAdapter` с Silero VAD.
- «TTS pcm/частота» — **решено**: `openai.TTS(response_format="pcm", sample_rate=44100)` получает SSE-поток, кадры 44100 Гц, длительность совпадает с WAV того же запроса.
- «STT во время TTS» — **решено**: все 5 запросов STT закончились раньше длинного TTS; медиана ≤ 2× одиночной. Второй экземпляр сервера не нужен.
- «Потоки при decode» — **замерено**: decode llama-server под аудио-нагрузкой 69.9–73.0 tok/s при `threads` 2/4/6; выбрано `threads: 6`.
- **G1 — @@G1_STATUS@@**.
- **G2 — @@G2_STATUS@@**.

## 1. Стенд

- Хост: Intel Core i5-10600KF (6 ядер / 12 потоков); аудио на CPU. llama-server VOCA-4 на :8081 работал всё время замеров.
- audio.cpp 0.9.4 — готовый релиз, сборка из клона не нужна:
  - `https://github.com/0xShug0/audio.cpp/releases/download/v0.9.4/audio-v0.9.4-bin-ubuntu-x64-cpu-portable.tar.gz` — sha256 `02756c556d78ccca8040cab6fbc6c7bf57d4f6ed70fe534e8a9e846bab464687`, в `~/Apps/audio.cpp/0.9.4/`;
  - `audiocpp_server` (из архива) — sha256 `b4c3e9881165ded6fd63b05e8cefcbf7517bb4c364a518e783d51d2f1a29c6dd`; рядом `libggml.so.0`, `libggml-base.so.0` и 14 `libggml-cpu-*.so`, sha256 каждого — в `scripts/SHA256SUMS`. На этом CPU ggml загружает `libggml-cpu-haswell.so` (строка в логе).
  - `audiocpp_server --version`:

    ```
    audio.cpp 0.9.4
    git: f7ac446f 2026-11-04
    build: Release, gcc 15.2.0, Linux x86_64
    backends: cpu
    ```

  - Каталог `model_specs/` архива серверу не нужен: обе модели загружаются со спецификацией, встроенной в бинарник.
- Модели — HF `audio-cpp/audio.cpp-gguf`, ревизия `6f45df32f8fb283e4cfce3d3e1cb7688986fcbe6`, готовые GGUF (без `audiocpp_gguf`):
  - `Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-q8_0.gguf`, 915 735 760 байт, sha256 `6fa498f10f8cb606f041aa9512733cfee842a932359b00b0c45b9b723c4f4f4b`;
  - `Supertonic-3-GGUF/supertonic-3-q8_0.gguf`, 454 076 228 байт, sha256 `9edd05cf92feab49c6b29721d14c86566459dca7f4a6eeb99306372cdce03697`.
- `.local/server.json` (рендер `scripts/server.json`, путь заменён на `${VOCA_MODELS_DIR}`):

  ```json
  {
    "host": "127.0.0.1",
    "port": 8082,
    "backend": "cpu",
    "threads": 6,
    "lazy_load": false,
    "max_loaded_models": 2,
    "ui": false,
    "models": [
      {
        "id": "asr",
        "family": "parakeet_tdt",
        "path": "${VOCA_MODELS_DIR}/audio-cpp/Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-q8_0.gguf",
        "task": "asr",
        "mode": "offline"
      },
      {
        "id": "tts",
        "family": "supertonic",
        "path": "${VOCA_MODELS_DIR}/audio-cpp/Supertonic-3-GGUF/supertonic-3-q8_0.gguf",
        "task": "tts",
        "mode": "streaming",
        "default_voice_preset": {"voice_id": "F1"}
      }
    ]
  }
  ```

- Один экземпляр сервера на :8082 для обеих моделей («STT во время TTS» прошло, раздел 2). Обе модели загружены через ≈ 5 с после старта.
- Python (`agent/uv.lock`): `livekit-agents` 1.8.11, `livekit-plugins-openai` 1.8.11, `livekit-plugins-silero` 1.8.11, `openai` 3.48.0.

## 2. Проверки первого часа

| Проверка | Как | Наблюдение | Итог |
| --- | --- | --- | --- |
| STT батчем | `stt_ru.py samples`: каждый образец — `openai.STT.recognize` и `stt.StreamAdapter(vad=silero.VAD.load(min_silence_duration=0.3))`, кадры по 20 мс, 1 с тишины, `end_input()` | 72 образца, 3 тихих пропущены (`skip`); у остальных 69 оба пути дали текст; RTF батча медиана 0.081, максимум 0.210 (threads 6). Тексты батча и адаптера совпадают по смыслу, адаптер режет речь на 1–9 финальных событий | решено |
| TTS pcm/частота | `tts_ru.py check`: фраза 1 через `openai.TTS(response_format="pcm", sample_rate=44100)` с `language: "ru"` и тот же запрос прямым `POST /v1/audio/speech` → WAV | WAV 44100 Гц; кадры плагина 44100 Гц; плагин 6.160 с, WAV 6.150 с; `content-type` при `stream_format: "sse"` — `text/event-stream; charset=utf-8`. Все четыре `PASS` | решено |
| STT во время TTS | `audio_parallel.py`: весь `tts_ru.txt` одной строкой (≈ 119 с аудио, синтез ≈ 30 с, первый кадр через ≈ 7 с), после первого кадра 5 STT сегмента 5 с | все 5 STT закончились до конца TTS при threads 2, 4, 6; медиана при threads 6 — 0.488 с при одиночной 0.263 с (порог 0.526) | решено, один экземпляр |
| Потоки при decode | `audio_parallel.py --threads-label N`, сервер с `threads` = N, llama-server VOCA-4 без изменений (`-t 2`) | таблица ниже | `threads: 6` |

Потоки при decode (3 запроса `max_tokens 256`, `temperature 0`, медиана; нагрузка — цикл STT 5 с + TTS фразы):

| `threads` | decode без нагрузки, tok/s | decode под нагрузкой, tok/s | RTF STT 5 с под нагрузкой (медиана / максимум) | STT одиночно, с |
| --- | --- | --- | --- | --- |
| 2 | 74.0 | 73.0 | 0.132 / 0.136 | 0.659 |
| 4 | 75.1 | 71.2 | 0.076 / 0.089 | 0.354 |
| 6 | 75.1 | 69.9 | 0.073 / 0.077 | 0.263 |

Правило шага 11 плана: наибольшее N с decode под нагрузкой ≥ 15 tok/s (G4) и RTF STT ≤ 0.3 — подходят все три, выбрано 6. Первый запрос decode при threads 2 (56.5 tok/s) — прогрев; в медиану трёх он входит, но её не меняет.

Отличия от плана, найденные на стенде:

- `openai.TTS` всегда шлёт `speed: 1.0`, а умолчание Supertonic — `speed: 1.05`. Поэтому прямой запрос в `check` тоже передаёт `"speed": 1.0`; без этого WAV на 5 % короче (5.857 с против 6.160 с) при том же разборе PCM.
- Supertonic отдаёт SSE кусками по текстовым фрагментам: на одной фразе — один кусок в конце синтеза (TTFB = полное время), на длинном тексте — 5 кусков по 18–29 с аудио. Первый звук ассистента наступает после синтеза первого фрагмента.
- Без поля `language` Supertonic синтезирует как английский: ответ побайтно равен запросу с `"language": "en"`; с `"ru"` — другой; неизвестный код (`"xx"`) — 500 `invalid Supertonic language`.
- Три образца почти беззвучны: `cat_RussianHockeyCheer_1.wav` (пик 180 из 32767), `eng_RussianHockeyCheer_4.wav` (618), `cat_RussianHockeyCheer_0.wav` (818); у остальных пик ≥ 19 126. На них оба пути дают пустой текст — дефект образцов, не сервера. По решению инженера (2026-11-10, «Пропускать тихие») `samples` помечает образец с пиком ниже 2000 как `skip`, перечисляет его поимённо и не считает ошибкой.
- Образцы `*Russian*` — голоса с «русским» стилем; часть фраз (`eng_*`) — английская речь, Parakeet распознаёт её по-английски.

## 3. G1

**Автоматический корпус.** `stt_ru.py samples` (72 файла, эталонов нет, код 0): 3 тихих образца — `skip` (раздел 2), на 69 остальных оба пути работают; RTF батча медиана 0.081, максимум 0.210 — на первом файле прогона (`cat_RussianBabushka_0`, холодный запрос), без него ≤ 0.125. `stt_ru.py rtf5` на `samples/cat_Russian_0.wav`: RTF на 5 с медиана 0.054, максимум 0.070.

@@G1_RECORDINGS@@

## 4. G2

Голос `F1`, `openai.TTS` с `response_format="pcm"`, `sample_rate=44100`, threads 6. `render` — 30 фраз `bench/corpus/tts_ru.txt` × 2 варианта: `default` (без `language`) и `ru` (`language: "ru"` через `SpeechLanguageClient`).

| Вариант | TTFB p50 / p95, с | Полное время p50 / p95, с | Аудио всего, с | RTF синтеза |
| --- | --- | --- | --- | --- |
| default | 0.809 / 1.063 | 0.809 / 1.064 | 125.9 | 0.20 |
| ru | 0.897 / 1.290 | 0.897 / 1.291 | 131.7 | 0.21 |

`soak` — 200 фраз подряд (корпус по кругу) одним экземпляром `openai.TTS`:

| Вариант | Ошибок | Пустых | Флагов длительности | TTFB p50 / p95, с | Полное время p50 / p95, с |
| --- | --- | --- | --- | --- | --- |
| ru | 0 | 0 | 0 | 0.893 / 1.303 | 0.894 / 1.303 |
| default | 0 | 0 | 0 | 0.823 / 1.112 | 0.823 / 1.113 |

@@G2_LISTENING@@

## 5. Wiring для 1.1

- STT:

  ```python
  stt.StreamAdapter(
      stt=openai.STT(
          model="asr", language="ru", base_url="http://127.0.0.1:8082/v1", api_key="none"
      ),
      vad=silero.VAD.load(min_silence_duration=0.3),
  )
  ```

@@WIRING_TTS@@

- `server.json`: Parakeet — `mode: offline` (`openai.STT` шлёт сегмент целиком без `stream=true`, offline сам ресемплирует вход); Supertonic — `mode: streaming` (`openai.TTS` всегда просит `stream_format: "sse"`, сервер принимает его только у модели в `mode: streaming`). `voice` — `M1…M5` или `F1…F5`; умолчание плагина `ash` сервер не примет.
- Language hint у Parakeet нет (в спецификации модели нет опции `language`).

## 6. Записи и файлы

Всё ниже — в `.local/bench/` worktree, в репозитории этих файлов нет:

- `stt/samples.tsv`, `stt/samples.out` — прогон автоматического корпуса;
- `stt/01.wav … 25.wav` — записи инженера (16 кГц, моно), `stt/recordings.tsv` — G1 по ним;
- `tts/default/NN.wav`, `tts/ru/NN.wav` — 60 файлов `render`, `tts/render.tsv`; `tts/listening.tsv` — ответы инженера; `tts/soak-ru.tsv`, `tts/soak-default.tsv` — 200 фраз;
- `parallel/{2,4,6}.tsv`, `parallel/skip.tsv` — «STT во время TTS» и decode; `server-threads-{2,4,6}.json` — конфиги замера, логи `.local/logs/audio-threads-{2,4,6}.log`.
