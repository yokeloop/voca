#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

if [ "$#" -eq 0 ]; then
  set -- agent web llm audio livekit
fi

run_dir="$VOCA_ROOT/.local/run"

for name in "$@"; do
  pid_file="$run_dir/$name.pid"
  if [ ! -f "$pid_file" ]; then
    echo "$name не запущен"
    continue
  fi
  pid="$(cat "$pid_file")"
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "$name не запущен"
    continue
  fi
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
  echo "$name stopped (pid $pid)"
done
