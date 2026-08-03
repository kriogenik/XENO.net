#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "ERROR: python3 required" >&2
  exit 1
fi
# On Git Bash + Windows Python, prefer Windows path
if command -v cygpath >/dev/null 2>&1; then
  exec "$PY" "$(cygpath -w "$ROOT_DIR/scripts/dry_run.py")"
fi
exec "$PY" "$ROOT_DIR/scripts/dry_run.py"
