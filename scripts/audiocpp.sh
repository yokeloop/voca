#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

mkdir -p "$VOCA_ROOT/.local"
envsubst '${VOCA_MODELS_DIR}' <"$VOCA_ROOT/scripts/server.json" >"$VOCA_ROOT/.local/server.json"
echo "запуск audiocpp_server добавляет VOCA-5 (0.2)" >&2
exit 1
