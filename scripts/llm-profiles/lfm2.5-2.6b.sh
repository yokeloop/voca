# The default profile, the agent's model (VOCA-9). Sampling of the LiquidAI/LFM2.5-2.6B card (temperature 0.1, top_k 50, repetition_penalty 1.1); always thinks; no --cache-reuse (shifted prefixes are not reused, the prefix cache is); no per_layer override.
model="$VOCA_MODELS_DIR/LiquidAI/LFM2.5-2.6B-Q4_K_M.gguf"
args=(--temp 0.1 --top-k 50 --repeat-penalty 1.1)
