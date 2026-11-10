# Sampling of the NVIDIA Nemotron-3.5-Bolt-E4B card; profile B of README 0.3 (per_layer tensors in RAM).
model="$VOCA_MODELS_DIR/unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf"
args=(--cache-reuse 256 --override-tensor 'per_layer.*=CPU' --temp 1.0 --top-p 0.95)
