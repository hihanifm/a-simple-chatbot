#!/usr/bin/env bash
# Download wheels into pip-cache/<uname -m>/ for the current machine arch.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ARCH="$(uname -m)"
DEST="pip-cache/${ARCH}"
mkdir -p "$DEST"

case "$ARCH" in
  aarch64|arm64)
    PLAT=(--platform manylinux_2_17_aarch64 --platform linux_aarch64)
    ;;
  x86_64|amd64)
    PLAT=(--platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform linux_x86_64)
    ;;
  *)
    echo "Unknown arch: $ARCH (downloading without --platform filter)"
    PLAT=()
    ;;
esac

TRUSTED=(
  --trusted-host pypi.org
  --trusted-host pypi.python.org
  --trusted-host files.pythonhosted.org
)

INDEX=()
if [ -n "${PIP_INDEX_URL:-}" ]; then
  INDEX=(-i "$PIP_INDEX_URL")
fi

COMMON=(
  "${INDEX[@]}"
  "${TRUSTED[@]}"
  --python-version 3.11
  --implementation cp
  --abi cp311
  -r requirements.txt
  -d "$DEST"
)

echo "pip-cache: arch=$ARCH dest=$DEST"

if [ ${#PLAT[@]} -gt 0 ]; then
  pip download "${PLAT[@]}" "${COMMON[@]}" --only-binary=:all: \
    || pip download "${PLAT[@]}" "${COMMON[@]}"
else
  pip download "${COMMON[@]}" --only-binary=:all: \
    || pip download "${COMMON[@]}"
fi

echo "Done. Wheels in $DEST"
