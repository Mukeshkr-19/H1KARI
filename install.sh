#!/usr/bin/env bash
# HIKARI — portable setup (macOS / Linux). Run from the repo root after git clone.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "=== H1KARI setup ==="
echo "Repository: $REPO_ROOT"
echo "CLI commands remain: hikari / Hikari"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required." >&2
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $(python3 --version) (need 3.10+; 3.12 recommended)"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment in .venv ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip wheel setuptools
PY_VER="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" && "$PY_VER" == "3.12" ]]; then
  python -m pip install -r requirements-macos-arm64-py312.lock
else
  echo "No verified lock for $(uname -s)/$(uname -m)/Python $PY_VER; using direct requirements."
  python -m pip install -r requirements.txt
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo ""
  echo "Created .env from .env.example"
  echo "Edit .env and add at least one AI provider key (see README)."
fi

chmod +x bin/Hikari 2>/dev/null || true
chmod +x "$REPO_ROOT/scripts/install-hikari-login-agent.sh" \
  "$REPO_ROOT/scripts/uninstall-hikari-login-agent.sh" \
  "$REPO_ROOT/scripts/install-hikari-cli.sh" \
  "$REPO_ROOT/scripts/uninstall-hikari-cli.sh" 2>/dev/null || true

"$REPO_ROOT/scripts/install-hikari-cli.sh"

echo ""
echo "HIKARI voice wake mode requires a local owner voice enrollment."
if [[ -t 0 && -t 1 ]]; then
  read -r -p "Enroll your voice now? [y/N] " ENROLL_VOICE
  case "$ENROLL_VOICE" in
    y|Y|yes|YES|Yes)
      "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/hikari.py" --enroll-voice || {
        echo "Voice enrollment did not complete. Retry with: hikari --enroll-voice" >&2
      }
      ;;
    *)
      echo "Skipped. Wake-word mode stays locked until you run: hikari --enroll-voice"
      ;;
  esac
else
  echo "Non-interactive install: run 'hikari --enroll-voice' before wake-word mode."
fi

echo ""
echo "=== Done ==="
echo "Activate the environment:"
echo "  source .venv/bin/activate"
echo ""
echo "CLI from anywhere:"
echo "  hikari --doctor"
echo ""
