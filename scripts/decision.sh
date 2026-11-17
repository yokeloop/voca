#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

fail() {
  echo "$*" >&2
  exit 1
}

[ -x "$VOCA_LLAMA_BIN" ] || fail "VOCA_LLAMA_BIN=$VOCA_LLAMA_BIN не найден: scripts/fetch.sh"
[ -f "$VOCA_DECISION_MODEL" ] || fail "модель $VOCA_DECISION_MODEL не найдена: scripts/fetch.sh"
if [ -n "$(ss -Hltn "sport = :$VOCA_DECISION_PORT")" ]; then
  fail "порт $VOCA_DECISION_PORT занят — порт модели решений"
fi
if [ "${1:-}" = "--check" ]; then
  exit 0
fi

CUDA_VISIBLE_DEVICES= exec "$VOCA_LLAMA_BIN" -m "$VOCA_DECISION_MODEL" --alias d1-2B \
  --host 127.0.0.1 --port "$VOCA_DECISION_PORT" \
  -c 2048 --parallel 1 -t "$VOCA_DECISION_THREADS" -ngl 0 --cache-ram 256 "$@"
