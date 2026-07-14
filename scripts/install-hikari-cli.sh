#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="$REPO_ROOT/bin/Hikari"

if [[ ! -x "$LAUNCHER" ]]; then
  chmod +x "$LAUNCHER"
fi

if [[ -n "${HIKARI_CLI_DIR:-}" ]]; then
  CLI_DIR="$HIKARI_CLI_DIR"
elif [[ ":$PATH:" == *":$HOME/.local/bin:"* ]]; then
  CLI_DIR="$HOME/.local/bin"
elif [[ ":$PATH:" == *":$HOME/bin:"* ]]; then
  CLI_DIR="$HOME/bin"
else
  CLI_DIR="$HOME/.local/bin"
fi

mkdir -p "$CLI_DIR"
WRAPPER="$CLI_DIR/hikari"
# Remove broken self-referential symlinks (macOS mv cannot replace symlink -> symlink loops).
rm -f "$WRAPPER" "$CLI_DIR/Hikari"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
export HIKARI_REPO_ROOT="$REPO_ROOT"
exec "$LAUNCHER" "\$@"
EOF
chmod +x "$WRAPPER"
# macOS default volumes are case-insensitive: "hikari" and "Hikari" are the same path.
# ln -sf hikari Hikari there replaces the script with a self-referential symlink (broken).
if [[ "$(uname -s)" != "Darwin" ]]; then
  ln -sf hikari "$CLI_DIR/Hikari"
else
  echo "  (macOS: hikari and Hikari are the same command — one wrapper only)"
fi

echo "Installed HIKARI CLI (repo folder: $(basename "$REPO_ROOT")):"
echo "  $CLI_DIR/hikari (HIKARI_REPO_ROOT=$REPO_ROOT)"
echo "  launcher: $LAUNCHER"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "  $CLI_DIR/Hikari -> hikari"
fi

if [[ ":$PATH:" != *":$CLI_DIR:"* ]]; then
  echo ""
  echo "Add this to your shell config, then restart Terminal:"
  echo "  export PATH=\"$CLI_DIR:\$PATH\""
fi

echo ""
echo "Try:"
echo "  hikari --doctor"
