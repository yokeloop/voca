# The default profile, the agent's model (VOCA-36). Sampling of the NVIDIA Nemotron-3.5-Bolt-E4B card; profile B of README 0.3 (per_layer tensors in RAM). When the agent's thinking_budget_tokens runs out, llama-server forces the end of the thought channel; the message tells the model the thinking is over, otherwise it goes on reasoning in the answer and the reasoning is spoken.
model="$VOCA_MODELS_DIR/unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf"
args=(--cache-reuse 256 --override-tensor 'per_layer.*=CPU' --temp 1.0 --top-p 0.95
  --reasoning-budget-message $'\n\nTime is up. I stop thinking and give the user the final answer now, in Russian, without my reasoning.\n')
