#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

fail() {
  echo "$*" >&2
  exit 1
}

profile="$VOCA_ROOT/scripts/llm-profiles/$VOCA_LLM_PROFILE.sh"
[ -f "$profile" ] || fail "профиль VOCA_LLM_PROFILE=$VOCA_LLM_PROFILE не найден: scripts/llm-profiles/<name>.sh"
. "$profile"

[ -x "$VOCA_LLAMA_BIN" ] || fail "VOCA_LLAMA_BIN=$VOCA_LLAMA_BIN не найден: scripts/fetch.sh"
[ -f "$model" ] || fail "модель $model не найдена: scripts/fetch.sh"
if [ -n "$(ss -Hltn "sport = :8081")" ]; then
  fail "порт 8081 занят — порт llama-server"
fi
if [ "${1:-}" = "--check" ]; then
  exit 0
fi

exec "$VOCA_LLAMA_BIN" -m "$model" --alias "$VOCA_LLM_PROFILE" \
  --jinja --host 127.0.0.1 --port 8081 \
  -c 16384 -fa on -ctk q8_0 -ctv q8_0 \
  --parallel 1 -t 2 -ngl 99 \
  "${args[@]}" \
  "$@"
