#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

config="$VOCA_ROOT/.local/server.json"

fail() {
  echo "$*" >&2
  exit 1
}

mkdir -p "$VOCA_ROOT/.local"
envsubst '${VOCA_MODELS_DIR}' <"$VOCA_ROOT/scripts/server.json" >"$config"

[ -x "$VOCA_AUDIOCPP_BIN" ] || fail "VOCA_AUDIOCPP_BIN=$VOCA_AUDIOCPP_BIN не найден: scripts/fetch.sh"
while read -r model; do
  [ -f "$model" ] || fail "модель $model не найдена: scripts/fetch.sh"
done < <(jq -r '.models[].path' "$config")
port="$(jq -r .port "$config")"
if [ -n "$(ss -Hltn "sport = :$port")" ]; then
  fail "порт $port занят — порт audiocpp_server из scripts/server.json"
fi
if [ "${1:-}" = "--check" ]; then
  exit 0
fi

exec "$VOCA_AUDIOCPP_BIN" --config "$config" --no-ui
