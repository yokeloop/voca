# General preset of the LiquidAI/LFM3-1.7B card; no --cache-reuse (unsupported by this context, checkpoints are used); no per_layer override.
model="$VOCA_MODELS_DIR/LiquidAI/LFM3-1.7B-Q4_K_M.gguf"
args=(--temp 0.1 --top-k 50 --repeat-penalty 1.1)
