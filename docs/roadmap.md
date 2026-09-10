# VOCA v3 — Roadmap

> Docs-only: это последовательность будущей реализации, не отчёт о работающих функциях. VOCA-2 не запускает эксперименты, не создаёт будущие тикеты и не задаёт сроки. Контракты — в [архитектуре](architecture.md), решения — в [ADR-0002](adr/0002-conversation-lifecycle-and-roadmap.md), определения — в [CONTEXT](../CONTEXT.md).

Порядок: **голосовой разговор → контекст/инструкции → Pi-делегация → остальные возможности**. Каждый этап оставляет проверяемый пользовательский результат; ранняя разведка — входная работа первого голосового этапа, не отдельный инфраструктурный проект без пользовательского выхода.

## Gates

| Gate | Протокол / контракт | Что останавливает |
|---|---|---|
| Предпосылки LangSmith | [Official requirements](architecture.md#official-requirements-and-installation-blockers), [H7](architecture.md#h7--tracing-and-langsmith-gate) | Нет подтверждённых entitlement/artifact access, поддерживаемого single-host профиля, storage/resource budget или license egress/offline license — блокер зависимого наращивания этапа 1; LangSmith не подменяется |
| Browser interrupt | [H1](architecture.md#h1--browser-barge-in), [playback boundary](architecture.md#cancellation-and-playback-boundary) | Старый звук не убирается из browser, возобновляется, новая речь теряется — функциональный блокер этапа 1 |
| Русский / совместная память | [H2](architecture.md#h2--russian-voice-and-shared-memory) | Неразборчивый русский, потеря смысла/имён/чисел, OOM, служебный текст в TTS — функциональный блокер этапа 1 и последующих изменений providers |
| Полная доступная трасса | [Trace map](architecture.md#trace-map), [H7](architecture.md#h7--tracing-and-langsmith-gate) | Неполная корреляция, credentials, hidden loss, cloud export — блокер приёмки этапа 1; расширяется на Pi в этапе 3 |
| История и snapshot | [Instructions/history](architecture.md#instructions-and-history), [control](architecture.md#control-contract) | Reload меняет текущий turn, потеря истории или interrupted text выдан за услышанный — блокер этапа 2 |
| Нормализованный tool calling | [H5](architecture.md#h5--lfmminicpm-and-tool-calling) | Неправильные действия/аргументы, partial dispatch, reasoning в TTS — блокер Pi-этапа. Проверяется baseline LFM, MiniCPM не обязателен |
| Pi и durable delivery | [Delegation](architecture.md#delegation), [Task lifecycle](architecture.md#task-lifecycle) | Не тот instance/cwd/config, утрата ограничений, voice abort задачи, повтор после unknown или duplicate result — блокер этапа 3 |
| Realtime цель | [H3](architecture.md#h3--latency) | Около 500 мс end-of-speech → first audible — измеряемая цель; превышение фиксирует bottleneck и недостигнутую цель, **не отменяет функциональную приёмку** |
| Отложенные кандидаты | [H4](architecture.md#h4--unified-audiocpp-optional), [H6](architecture.md#h6--deferred-semantic-activation-turn-detection-and-fine-tuning), [model research](research/minicpm5-2b.md) | Неуспех не допускает конкретный эксперимент в продукт, но не блокирует первые три этапа |

## Этап 1 — голосовой разговор

**Результат пользователю:** включить разговор кнопкой, сказать несколько русских реплик и услышать ответы; перебивание действительно останавливает старый звук.

**Вход / ранняя работа:** в самом начале подтвердить предпосылки LangSmith и проверить H1/H2/H7 на минимальном сквозном пути, до наращивания зависимых функций. На момент VOCA-2 лицензия, доступ к images и совместное размещение LangSmith не доказаны: рекомендации 16 vCPU/64 GB выше бюджета хоста около 32 GB RAM. Документированный Minikube/Kind dev путь не является обещанием достаточности ресурсов или native/systemd установки.

**Реализуется:** один Python/FastAPI backend с runtime-владельцем turn/context/event queue; browser UI (переключатель без удержания, transcript/assistant text, connection/conversation/error), FastRTC WebRTC audio, control WS, VAD и запрос AEC; локальный Whisper server → LiteLLM → llama.cpp/LFM2.5 → audio.cpp через TTSProvider. SQLite фиксирует sessions/turns/messages и базовый instruction snapshot. Self-hosted LangSmith на той же машине, явный локальный endpoint в приложении и LiteLLM, redaction и ограниченная export queue. Cancellation/flush/late-generation fencing реализуются здесь, не в polish.

**Не входит:** произвольные инструменты и делегация, развитый UI, управляемое редактирование/reload инструкций, semantic/always-on, замена LFM и unified audio.cpp STT.

**Приёмка:**

- Кнопка → permission/connected/listening → несколько реплик → transcript и слышимый ответ → следующая реплика без удержания.
- Permission denied оставляет `off` с ошибкой. Echo на динамиках проверен, включённый AEC flag не заменяет проверку.
- Barge-in во время LLM/TTS streaming очищает backend/browser звук, не ждёт окончания старого inference, сохраняет новую речь; три последовательных перебивания без возобновления старого звука.
- Stop выключает capture/звук; disconnect инвалидирует generation; reconnect показывает snapshot/history и `off`, микрофон включается только кнопкой, старый звук не возвращается.
- Поочерёдный STT/LLM/TTS failure/timeout виден с компонентом; нет выдуманного transcript/cloud fallback; assistant text переживает TTS failure, новая реплика доступна после восстановления.
- Transcript, полная доступная trace и redaction проверены; tracing outage/overflow видны и не блокируют микрофон. H1/H2/H7 зелёные; H3 публикуется отдельно, без выдуманного достижения latency.

**Артефакт выхода:** walkthrough запись минимального UI/аудио, corpus/reference/transcripts, cancellation timeline с последним старым слышимым сэмплом, local LangSmith run IDs/span map, redaction/outage evidence и таблица cold/warm p50/p95/VRAM/RAM. До закрытия функциональных gates этап не принят.

## Этап 2 — контекст и инструкции

**Результат пользователю:** продолжать исходную сессию между подключениями и менять инструкции без изменения выполняющегося ответа.

**Вход:** принятый голосовой этап с H1/H2/H7; базовые persisted turns/snapshots уже существуют.

**Реализуется:** восстановление и использование истории в context, JSONL export из SQLite, управляемый instruction snapshot/reload в порядке SOUL → user → agent, last-valid retention и видимые reload errors. Не дублировать источник истины в JSONL. Отдельно хранить generated text, playback started и playback finished/unknown.

**Не входит:** вызов Pi, остальные агенты, arbitrary tooling и semantic activation.

**Приёмка:**

- Назвать факт, отключиться и вернуться: следующая реплика учитывает историю, resume сам не включает микрофон.
- Прервать длинный ответ и спросить о нём: история/context не выдают весь generated text за услышанный.
- Изменить инструкции во время turn: старый snapshot остаётся неизменным, новая инструкция действует со следующего turn.
- Ошибка reload видна, последняя валидная версия продолжает работать; отсутствие начального valid set блокирует start, а не подставляет пустой prompt.
- JSONL export соответствует SQLite messages/turns и playback status; голосовые сценарии этапа 1 не регрессируют.

**Артефакт выхода:** transcript и export с идентификаторами turns/snapshots, trace двух соседних turns до/после reload, reconnect/error walkthrough и сравнение persisted playback facts с UI.

## Этап 3 — Pi-делегация

**Результат пользователю:** поручить исследование выбранному Pi, продолжить говорить и получить поздний результат в исходной сессии.

**Вход:** этап 2, H5 на LFM и preflight выбранного Pi instance/cwd/config/trust/RPC version. При несовпадении конфигурации задача не запускается пустым Pi и не обходится новым approvals слоем.

**Реализуется:** `delegate(agent, task)` с единственным доступным `pi`, выбор экземпляра/каталога пользователем до поручения, отдельная новая Pi-сессия с его окружением/tools/MCP/ограничениями; JSONL RPC prompt/progress/`agent_settled`, durable tasks и task cards, async result delivery, дедупликация tool ID/terminal events, unknown recovery, trace связи voice → task → result turn.

Полномочия: **«Чтение, исследование и использование тулов/mcp подключенных к конкретно этому Pi инстансу»**. Текст задания не расширяет их и не переопределяет instance/cwd. Существующая беседа Pi не управляется.

**Не входит:** Hermes/OpenClaw, общий agent gateway, произвольное разрешение изменений, новый approval mechanism, автоматический retry неизвестного поручения.

**Приёмка:**

- Выбрать instance/cwd → попросить исследовать проект → task ID сразу виден, отдельная Pi session использует выбранную конфигурацию, prompt acceptance не принят за success.
- Продолжить голосом и перебить ответ: текущий voice generation отменяется, Pi task остаётся running; `agent_end` не завершает задачу до `agent_settled` и проверки terminal outcome.
- Поздний результат сохраняется и обновляет card, один раз входит в context, отдельный result turn звучит в паузе с приоритетом пользовательской речи. Сырой RPC/tool текст не читается TTS.
- Result при выключенном браузере переживает reconnect: card/history восстановлены, микрофон off; ещё не начатое озвучивание ждёт включения. Прерванный result turn не переозвучивается сам.
- Повтор tool/terminal event не запускает второй Pi и не создаёт второй результат. Restart после launch intent без известного outcome показывает `unknown`, не повторяет поручение.
- Доступные Pi/tool/MCP события и late result связаны trace/run/task IDs; физическое exactly-once прослушивание не обещается. H1/H2/H7 повторены на расширенном пути.

**Артефакт выхода:** redacted RPC event transcript, SQLite task/delivery/message evidence с IDs, task UI/reconnect/restart/duplicate walkthrough, linked local traces и audio cancellation timeline. Несовпадение полномочий, потеря result или дубли — блокер.

## Этап 4 — остальные возможности

**Результат пользователю:** дополнительные агенты и способы взаимодействия на уже принятом базовом разговоре и Pi, без их регрессии. Ни один подэтап не prerequisite первых трёх.

Последовательные подэтапы, каждый со своим отдельным артефактом приёмки:

| Порядок | Вход / что реализуется | Граница / не входит | Наблюдаемая приёмка и артефакт |
|---|---|---|---|
| 4.1 Hermes adapter | Принятый этап 3; сверить конкретную версию runs/events SSE/stop/approval, реализовать mapping к task lifecycle | Не общий protocol для всех агентов, не изменение Pi permissions | Поручение возвращает status/result без остановки микрофона; error/reconnect/duplicate не ломают delivery. Event transcript, task UI и trace |
| 4.2 OpenClaw adapter | После принятого 4.1; сверить WS agent/runId/agent.wait, независимый adapter | Не перенос Hermes SSE semantics по аналогии | Status и late result во время разговора, неизвестный outcome/дубли обработаны; event transcript и trace |
| 4.3 Развитый UI | Принятые adapters, тот же control contract; panels/controls/visuals с нуля | Не новая state machine и не перенос v2 | Все connection/conversation/turn/task состояния и ошибки видимы; walkthrough этапов 1–3 без регрессий, UI запись |
| 4.4 Semantic turn detection | H6 на принятом базовом VAD | Не always-on и не обещанная замена VAD | Нет потерь слов/смысла, не хуже baseline по cut/end errors, улучшение delay; размеченный корпус, comparison/trace; провал оставляет VAD |
| 4.5 Always-on semantic activation | H6, заранее согласованные false/missed rates и latency budget | Не обязательное включение без кнопки | Прямые обращения/фон/эхо/тишина: confusion matrix и p50/p95, сохранность начала речи; провал оставляет кнопку + VAD, 200–400 мс не считаются измеренными |
| 4.6 Модели и audio.cpp experiments | H2/H3/H4/H5, baseline артефакты; сравнение MiniCPM и optional unified STT/TTS по отдельности | Не замена по benchmark/URL, не обязательный fine-tuning | Русский/tool/streaming/cancel/resource checks проходят; сравнительные пробы/trace и решение по конкретному кандидату; провал сохраняет LFM/Whisper |

Fine-tuning допускается только после воспроизводимых провалов исходных сценариев и held-out проверки H6; не объявляется необходимым заранее. Каждый принятый эксперимент повторяет базовые voice/task/error сценарии. Неуспех оставляет предыдущую работающую конфигурацию и видимый результат исследования, а не меняет критерии приёмки.
