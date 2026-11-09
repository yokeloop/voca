#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

check=0
case "${1:-}" in
  "") ;;
  --check) check=1 ;;
  *)
    echo "usage: scripts/fetch.sh [--check]" >&2
    exit 2
    ;;
esac

sums="$VOCA_ROOT/scripts/SHA256SUMS"
failed=0
number=0

digest() {
  sha256sum "$1" | cut -d' ' -f1
}

while read -r sum path source rest || [ -n "${sum:-}" ]; do
  number=$((number + 1))
  case "${sum:-}" in "" | "#"*) continue ;; esac
  if [ -z "${path:-}" ] || [ -z "${source:-}" ] || [ -n "${rest:-}" ]; then
    echo "SHA256SUMS:$number: ожидается «<sha256> <path> <source>»" >&2
    exit 2
  fi
  case "$path" in
    /*) echo "SHA256SUMS:$number: абсолютный путь $path" >&2; exit 2 ;;
  esac
  case "/$path/" in
    */../*) echo "SHA256SUMS:$number: путь с .. $path" >&2; exit 2 ;;
  esac
  case "${path%%/*}" in
    apps) base="$VOCA_APPS_DIR" ;;
    models) base="$VOCA_MODELS_DIR" ;;
    *) echo "SHA256SUMS:$number: неизвестный корень ${path%%/*} (apps|models)" >&2; exit 2 ;;
  esac
  dest="$base/${path#*/}"
  dir="$(dirname "$dest")"

  if [ "$check" = 1 ]; then
    if [ ! -f "$dest" ]; then
      echo "MISSING $dest"
      failed=1
    elif [ "$(digest "$dest")" != "$sum" ]; then
      echo "MISMATCH $dest"
      failed=1
    else
      echo "OK $dest"
    fi
    continue
  fi

  if [ -f "$dest" ]; then
    if [ "$(digest "$dest")" != "$sum" ]; then
      echo "SHA256SUMS:$number: sha256 $dest не совпадает: удалите файл и повторите" >&2
      exit 1
    fi
    echo "ok $dest"
    continue
  fi

  mkdir -p "$dir"
  case "$source" in
    https://*)
      curl -fL --retry 3 -o "$dest.part" "$source"
      if [ "$(digest "$dest.part")" != "$sum" ]; then
        rm -f "$dest.part"
        echo "SHA256SUMS:$number: sha256 загрузки $source не совпадает" >&2
        exit 1
      fi
      mv "$dest.part" "$dest"
      ;;
    tar:*)
      tar -xzf "$dir/${source#tar:}" -C "$dir" "$(basename "$dest")"
      if [ "$(digest "$dest")" != "$sum" ]; then
        echo "SHA256SUMS:$number: sha256 $dest из ${source#tar:} не совпадает: удалите файл и повторите" >&2
        exit 1
      fi
      ;;
    *)
      echo "SHA256SUMS:$number: неизвестный источник $source (https://…|tar:<archive>)" >&2
      exit 2
      ;;
  esac
  echo "fetched $dest"
done <"$sums"

exit "$failed"
