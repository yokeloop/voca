#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

if [ "$#" -eq 0 ]; then
  set -- livekit audio llm web
fi

run_dir="$VOCA_ROOT/.local/run"
log_dir="$VOCA_ROOT/.local/logs"

fail() {
  echo "$*" >&2
  exit 1
}

tcp_busy() {
  [ -n "$(ss -Hltn "sport = :$1")" ]
}

udp_busy() {
  [ -n "$(ss -Hlun "sport = :$1")" ]
}

for name in "$@"; do
  case "$name" in
    livekit | audio | llm | web) ;;
    *) fail "неизвестный сервис $name: livekit audio llm web" ;;
  esac
  pid_file="$run_dir/$name.pid"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    fail "$name уже запущен (pid $(cat "$pid_file"))"
  fi
  case "$name" in
    livekit)
      [ -x "$VOCA_LIVEKIT_BIN" ] || fail "VOCA_LIVEKIT_BIN=$VOCA_LIVEKIT_BIN не найден: scripts/fetch.sh"
      for port in 7880 7881; do
        if tcp_busy "$port"; then
          fail "порт $port занят — фиксированный порт livekit-server --dev"
        fi
      done
      if udp_busy 7882; then
        fail "порт 7882 занят — фиксированный порт livekit-server --dev"
      fi
      ;;
    web)
      [ -f "$VOCA_ROOT/web/dist/index.html" ] || fail "нет web/dist/index.html: pnpm --dir web build"
      [ -x "$VOCA_ROOT/agent/.venv/bin/voca" ] || fail "нет agent/.venv/bin/voca: (cd agent && uv sync --locked)"
      if tcp_busy "$VOCA_WEB_PORT"; then
        fail "порт $VOCA_WEB_PORT (VOCA_WEB_PORT) занят"
      fi
      ;;
    audio) "$VOCA_ROOT/scripts/audiocpp.sh" --check || exit 1 ;;
    llm) "$VOCA_ROOT/scripts/llama.sh" --check || exit 1 ;;
  esac
done

mkdir -p "$log_dir" "$run_dir"
started=()

for name in "$@"; do
  case "$name" in
    livekit)
      command=("$VOCA_LIVEKIT_BIN" --dev --bind 0.0.0.0 --node-ip "$VOCA_LIVEKIT_NODE_IP")
      ready="http://127.0.0.1:7880/"
      wait_s=20
      ;;
    web)
      command=("$VOCA_ROOT/agent/.venv/bin/voca" web)
      ready="http://$VOCA_WEB_HOST:$VOCA_WEB_PORT/healthz"
      wait_s=20
      ;;
    audio)
      command=("$VOCA_ROOT/scripts/audiocpp.sh")
      ready="http://127.0.0.1:8082/health"
      wait_s=120
      ;;
    llm)
      command=("$VOCA_ROOT/scripts/llama.sh")
      ready="http://127.0.0.1:8081/health"
      wait_s=120
      ;;
  esac
  log="$log_dir/$name.log"
  nohup "${command[@]}" >>"$log" 2>&1 </dev/null &
  pid=$!
  echo "$pid" >"$run_dir/$name.pid"
  started+=("$name")
  up=0
  for _ in $(seq $((wait_s * 2))); do
    if curl -fsS -o /dev/null "$ready" 2>/dev/null; then
      up=1
      break
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if [ "$up" = 0 ]; then
    echo "$name не ответил на $ready за $wait_s с; последние строки $log:" >&2
    tail -n 20 "$log" >&2
    "$VOCA_ROOT/scripts/down.sh" "${started[@]}" >&2
    exit 1
  fi
  echo "$name up (pid $pid) $ready"
done
