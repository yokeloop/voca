#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

models="$VOCA_MODELS_DIR/ruaccent/accentuator-b78ae5e"
python="$VOCA_ROOT/agent/.venv/bin/python"

fail() {
  echo "$*" >&2
  exit 1
}

[ -x "$python" ] || fail "нет agent/.venv: (cd agent && uv sync --locked)"
while read -r sum path _; do
  case "${path:-}" in
    models/ruaccent/accentuator-b78ae5e/koziev/*) ;;
    *) continue ;;
  esac
  file="$VOCA_MODELS_DIR/${path#models/}"
  [ -f "$file" ] || fail "нет $file: scripts/fetch.sh"
  [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$sum" ] || fail "sha256 $file не совпадает: удалите файл и scripts/fetch.sh"
done <"$VOCA_ROOT/scripts/SHA256SUMS"

package="$("$python" -c 'import importlib.util, pathlib; print(pathlib.Path(importlib.util.find_spec("ruaccent").origin).parent)')"
link="$package/koziev"
if [ -L "$link" ] && [ "$(readlink "$link")" = "$models/koziev" ]; then
  echo "ok $link"
  exit 0
fi
[ -e "$link" ] || [ -L "$link" ] && fail "$link уже есть и не ведёт в $models/koziev: удалите его и повторите"
ln -s "$models/koziev" "$link"
echo "linked $link -> $models/koziev"
