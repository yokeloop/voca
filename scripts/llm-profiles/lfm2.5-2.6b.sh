# An alternative since VOCA-36 (the agent's model in VOCA-9…VOCA-35); start it with VOCA_LLM_PROFILE=lfm2.5-2.6b. Sampling of the LiquidAI/LFM2.5-2.6B card (temperature 0.1, top_k 50, repetition_penalty 1.1); always thinks; no --cache-reuse (shifted prefixes are not reused, the prefix cache is); no per_layer override.
model="$VOCA_MODELS_DIR/LiquidAI/LFM2.5-2.6B-Q4_K_M.gguf"
args=(--temp 0.1 --top-k 50 --repeat-penalty 1.1)
