#!/usr/bin/env bash
# Verify pip-cache has wheels for the current host arch before docker build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ARCH="$(uname -m)"
DEST="pip-cache/${ARCH}"
MIN_WHEELS="${MIN_PIP_WHEELS:-3}"

if [ ! -d "$DEST" ]; then
  echo "ERROR: missing ${DEST}" >&2
  echo "Run: make pip-cache" >&2
  exit 1
fi

count="$(find "$DEST" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$count" -lt "$MIN_WHEELS" ]; then
  echo "ERROR: ${DEST} has only ${count} wheel(s); need at least ${MIN_WHEELS}" >&2
  echo "Run: make pip-cache" >&2
  exit 1
fi

echo "pip-cache OK: ${DEST} (${count} wheels)"
