# MiniCPM5-2B — модель для теста в VOCA

> Source: [r/LocalLLaMA post](https://www.reddit.com/r/LocalLLaMA/comments/1w9skjz/minicpm52b_release_day/) · [HuggingFace](https://huggingface.co/openbmb/MiniCPM5-2B) · [GitHub](https://github.com/OpenBMB/MiniCPM)
> Saved: 2026-09-07

## Что это

**MiniCPM5-2B** (OpenBMB) — dense 2B Transformer, второй в серии MiniCPM5 (после MiniCPM5-1B). Для on-device / local / resource-constrained deployment. License: **Apache-2.0**. Архитектура: `LlamaForCausalLM` (native llama.cpp совместимость вероятна).

Reddit: 15 баллов на Artificial Analysis Intelligence Index v4.2 — **высший результат среди open-weight моделей до 4B**.

## Почему кандидата стоит протестировать для VOCA

Позиционируется как кандидат на замену/сравнение с **LFM2.5-2.6B** — прямым конкурентом в 2B-классе. По заявленным бенчмаркам OpenBMB (воспроизведённые + Artificial Analysis):

- **Average: 53.9** vs LFM2.5-2.6B: 33.2, Qwen3.5-2B: 28.0 — обгоняет весь 2B-класс и все 4B-модели в наборе (макс 51.1 у Qwen3.5-4B)
- **Tool use** (важно для delegation): BFCL v4 **66.6** vs LFM2.5 61.1; τ³-Bench Banking **20.8** vs 7.2; τ²-Bench Telecom **97.1** vs 90.4
- **Agentic**: Claw-Gym **59.2** vs LFM2.5 19.3; WildClaw **23.9** vs 10.2; QwenClaw **42.9** vs 19.3
- **Coding Agent**: SWE-bench Verified **46.4** vs LFM2.5 6.0 (!)
- **Math**: AIME 2025 **86.5** vs 41.9
- **Long context**: AA-LCR **59.0** vs 5.3; NoLiMa **68.1** vs 0.7
- Instruction Following: IFBench 66.3 vs 59.0; IFEval 86.7 vs **93.4** (LFM чуть лучше)

⚠️ Заявленные числа — от OpenBMB, воспроизведение независимое не подтверждено. Поэтому и тест.

## Training recipe

- UltraData tiered data management: base → mid → post (SFT 400B tokens deep-thinking → RL → OPD)
- RL+OPD: +10.96 avg reasoning, +6.96 agentic. OPD мержит 16 RL-экспертов (5 агентных) через full-vocab reverse KL
- Открытые датасеты: UltraX, UltraData-Code (L0-L3), UltraData-SFT-Agent-2609 (500K agent samples), UltraData-RL-2609 (80K+ RL samples)

## Запуск

- **vLLM**: `vllm serve openbmb/MiniCPM5-2B --port 8000` (OpenAI-compatible API)
- **SGLang**: `python -m sglang.launch_server --model-path openbmb/MiniCPM5-2B --port 30000`
- **Speculative**: MiniCPM5-2B-DSpark draft model (SGLang, DSPARK, block-size 7)
- Transformers >= 5.6, chat template с `enable_thinking=True`

Через LiteLLM подключается как любой OpenAI-compatible backend — замена модели в VOCA без изменения loop.

## Что мерить в тесте (для VOCA)

1. Tool calling качество — правильный формат вызовов, особенно `delegate(agent, task)`
2. Русский язык — качество разговорного русского (бенчи в основном англ)
3. Latency на RTX 4070 — tokens/sec streaming vs LFM2.5-2.6B
4. Поведение в loop — "говорить или действовать", промежуточные реплики
5. Instruction following — SOUL/agent.md правила соблюдение