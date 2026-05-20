#!/usr/bin/env sh
# Install requirements from pip-cache only (no PyPI during docker build).
set -eu

CACHE_ROOT="${1:-/tmp/pip-cache}"
REQ="${2:-/app/requirements.txt}"

has_wheels() {
  dir="$1"
  [ -d "$dir" ] && find "$dir" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) -print -quit | grep -q .
}

pick_cache_dir() {
  uarch="$(uname -m)"
  for dir in \
    "$CACHE_ROOT/$uarch" \
    "$CACHE_ROOT/${TARGETARCH:-}" \
    "$CACHE_ROOT" \
    ; do
    if has_wheels "$dir"; then
      echo "$dir"
      return 0
    fi
  done
  case "${TARGETARCH:-}" in
    amd64)
      for dir in "$CACHE_ROOT/x86_64" "$CACHE_ROOT/amd64"; do
        if has_wheels "$dir"; then echo "$dir"; return 0; fi
      done
      ;;
    arm64)
      for dir in "$CACHE_ROOT/aarch64" "$CACHE_ROOT/arm64"; do
        if has_wheels "$dir"; then echo "$dir"; return 0; fi
      done
      ;;
  esac
  return 1
}

if ! CACHE_DIR="$(pick_cache_dir)"; then
  echo "pip: ERROR no wheels under ${CACHE_ROOT}" >&2
  echo "pip: expected pip-cache/$(uname -m)/ on the build host" >&2
  ls -la "$CACHE_ROOT" 2>/dev/null || true
  echo "pip: run 'make pip-cache' on this machine, then 'make build'" >&2
  exit 1
fi

count="$(find "$CACHE_DIR" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) | wc -l | tr -d ' ')"
echo "pip: OFFLINE install from ${CACHE_DIR} (${count} artifacts)"
echo "pip: uname -m=$(uname -m) TARGETARCH=${TARGETARCH:-unknown}"

# Offline only: do not use proxy or PyPI for this step.
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
pip install --no-index --find-links "$CACHE_DIR" -r "$REQ"
