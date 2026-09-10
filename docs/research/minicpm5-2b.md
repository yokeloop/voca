# MiniCPM5-2B — кандидат сравнения для VOCA

> Исходная заметка сохранена 2026-09-07 по [Reddit](https://www.reddit.com/r/LocalLLaMA/comments/1w9skjz/minicpm52b_release_day/), [OpenBMB](https://github.com/OpenBMB/MiniCPM) и model card. Проверка источников уточнена 2026-09-10 в VOCA-2. Модели не скачивались, inference не запускался; измерений VOCA нет.

## Evidence and limits

| Источник / revision | Подтверждено документально | Не подтверждено |
|---|---|---|
| [Официальная model card](https://huggingface.co/openbmb/MiniCPM5-2B/blob/abe115e887989b14f05e64a3b260648329324c3f/README.md), revision `abe115e887989b14f05e64a3b260648329324c3f`, проверена 2026-09-10 | OpenBMB MiniCPM5-2B, dense Transformer / `LlamaForCausalLM`, Apache-2.0, заявленные English/Chinese, 2,516,756,480 parameters (1,981,982,720 non-embedding), context 131,072 | Качество русского, рабочий размер context/VRAM и latency на RTX 4070 |
| [Официальный GGUF](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF/tree/c451f4d674096e6e6f1cc5d0abb6794bda638304), revision `c451f4d674096e6e6f1cc5d0abb6794bda638304`, проверен 2026-09-10 | Репозиторий издателя содержит F16, Q4_K_M и Q8_0; основная card прямо ссылается на него как GGUF для llama.cpp/Ollama/LM Studio | Наличие файла не равно проверенной streaming/tool совместимости конкретного server/template, тем более качеству VOCA |
| [llama.cpp chat routing](https://github.com/ggml-org/llama.cpp/blob/c32d1dabe819002ca8aa3a885aca4057f5968e3d/common/chat.cpp#L1186-L1191), проверен 2026-09-10 | MiniCPM5 template распознаётся и направляется в [XML parser](https://github.com/ggml-org/llama.cpp/blob/c32d1dabe819002ca8aa3a885aca4057f5968e3d/common/parsers/minicpm5.cpp); reasoning и content имеют отдельные границы | End-to-end нормализованные `tool_calls` через LiteLLM ещё не проверены |

Нельзя делать вывод о совместимости лишь из `LlamaForCausalLM` или смены URL. Официальные serving recipes (vLLM/SGLang/Transformers/DSpark) — альтернативы из model card, **не выбранный VOCA stack**. Старт остаётся локальный llama.cpp с LFM2.5 через LiteLLM; новый модельный парсер в loop не вводится.

## Manufacturer benchmarks are not VOCA measurements

В исходной заметке были сохранены числа производителя: Average 53.9 против LFM2.5 33.2; BFCL v4 66.6 против 61.1; SWE-bench Verified 46.4 против 6.0; IFEval 86.7 против 93.4. Это заявления OpenBMB о своём сравнении, не воспроизведение VOCA и не независимая проверка. Они не отвечают на вопросы русского голоса, streaming/cancellation, реальной делегации и первого слышимого ответа. Reddit-оценка Artificial Analysis также не является артефактом VOCA.

Training recipe (UltraData → SFT → RL/OPD) и открытые dataset listings описывают происхождение модели, но не доказывают нужду в fine-tuning VOCA. Решение о замене не принимается по рекламному среднему benchmark.

## Future comparison protocol

Нормативный протокол: [H5 — модели и tool calling](../architecture.md#h5--lfmminicpm-and-tool-calling), вместе с [H2 — русский и память](../architecture.md#h2--russian-voice-and-shared-memory) и [H3 — latency](../architecture.md#h3--latency).

1. Зафиксировать host/driver, llama.cpp/LiteLLM/adapters versions, model revisions/hash/quantization, context size, sampling/thinking/template настройки. Начать с сопоставимой Q4_K_M и одинаковых limits, отличия templates/thinking перечислить, не скрывать.
2. Подать обеим моделям один сохранённый русский корпус, одинаковые SOUL → user → agent snapshots, историю и `delegate(agent, task)` schema. Обычный вопрос, явное поручение исследования, промежуточная реплика + tool, неизвестный агент, incomplete/malformed аргументы и duplicate tool ID — обязательны; каждый сценарий повторить трижды cold/warm.
3. Сохранить redacted provider deltas и normalized adapter output: `id`, `function.name`, полную JSON `function.arguments`, finish reason, раздельные assistant content/reasoning. Валидировать tool name/args **до** dispatch. Не исполнять partial calls; повтор ID не запускает второе поручение. На этапе сравнения фиксировать ожидаемое действие; реальный Pi запускается только в приёмке его этапа.
4. Проверить русский ответ и инструкции, speakable промежуточную реплику. В TTS не должны попадать reasoning, arguments/results, XML/Pythonic markers или RPC events. Tokens/sec и первый reasoning token не заменяют first audible.
5. На неизменных STT/TTS и локальном LangSmith измерить first speakable text, first TTS chunk, first audible p50/p95, cold/warm и peak VRAM/RAM/OOM при совместной резидентности. Артефакт — пробы, audio/transcripts, normalized calls, trace IDs и сравнительная таблица, не только итоговый рейтинг.

**Критерий:** все обязательные действия/отказы верны, malformed/duplicate не dispatch-ятся, русский разборчив и сохраняет смысл/имена/числа, служебный текст изолирован, нет OOM. Latency публикуется независимо от функциональной приёмки; около 500 мс — цель, не гарантия.

**Решение по результату:** LFM остаётся baseline. Успешный MiniCPM допускается к отдельному выбору по русскому/tool/resource/latency артефакту, но не заменяет LFM автоматически. Провал MiniCPM оставляет кандидата в research. Провал обязательных LFM tool-сценариев блокирует Pi-этап до исправления совместимости/качества, а не ведёт к новому парсеру в loop. Само сравнение MiniCPM не prerequisite первых трёх [этапов roadmap](../roadmap.md).
