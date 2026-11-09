#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

model="$VOCA_MODELS_DIR/unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf"

fail() {
  echo "$*" >&2
  exit 1
}

[ -x "$VOCA_LLAMA_BIN" ] || fail "VOCA_LLAMA_BIN=$VOCA_LLAMA_BIN не найден: scripts/fetch.sh"
[ -f "$model" ] || fail "модель $model не найдена: scripts/fetch.sh"
if [ -n "$(ss -Hltn "sport = :8081")" ]; then
  fail "порт 8081 занят — порт llama-server"
fi
if [ "${1:-}" = "--check" ]; then
  exit 0
fi

exec "$VOCA_LLAMA_BIN" -m "$model" --alias bolt-e4b \
  --jinja --host 127.0.0.1 --port 8081 \
  -c 16384 -fa on -ctk q8_0 -ctv q8_0 \
  --cache-reuse 256 --parallel 1 -t 2 \
  -ngl 99 --override-tensor 'per_layer.*=CPU' \
  --temp 1.0 --top-p 0.95 \
  "$@"
