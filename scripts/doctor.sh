#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="python"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

if [[ "${1:-}" == "--full" ]]; then
  exec "$PYTHON_BIN" hikari.py --doctor-full
fi

exec "$PYTHON_BIN" hikari.py --doctor
