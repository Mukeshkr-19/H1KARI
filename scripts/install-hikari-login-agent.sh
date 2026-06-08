#!/usr/bin/env bash
# Install a Launch Agent so HIKARI starts at login (macOS only).
#
# This installs ONE background listener to avoid multiple daemons fighting over the mic.
#
# Default: services/hikari_simple.py (STT-based wake phrase; most reliable setup on fresh Macs).
# If you want speaker-locked mode, run services/hikari_daemon.py manually or customize this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
DAEMON="$REPO_ROOT/services/hikari_simple.py"
PLIST_DST="$HOME/Library/LaunchAgents/com.hikari.assistant.plist"
LOG_DIR="$HOME/Library/Logs"
OUT_LOG="$LOG_DIR/hikari-assistant.stdout.log"
ERR_LOG="$LOG_DIR/hikari-assistant.stderr.log"
LABEL="com.hikari.assistant"
OLD_LABELS=("com.hikari.wake" "com.hikari.alwayson")
OLD_PLISTS=("$HOME/Library/LaunchAgents/com.hikari.wake.plist" "$HOME/Library/LaunchAgents/com.hikari.alwayson.plist")

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS only." >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  echo "Missing venv Python: $PY — run ./install.sh first." >&2
  exit 1
fi

if [[ ! -f "$DAEMON" ]]; then
  echo "Missing daemon: $DAEMON" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

# Stop any older/competing agents (ignore errors)
for l in "${OLD_LABELS[@]}"; do
  launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
done
for p in "${OLD_PLISTS[@]}"; do
  launchctl unload "$p" 2>/dev/null || true
done

# Unload previous job if present (ignore errors)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true

cat >"$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>HIKARI_HOME</key>
    <string>$REPO_ROOT</string>
    <key>HIKARI_QUIET</key>
    <string>1</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-E</string>
    <string>$DAEMON</string>
  </array>
  <key>StandardOutPath</key>
  <string>$OUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$ERR_LOG</string>
</dict>
</plist>
EOF

chmod 644 "$PLIST_DST"

# Load for current GUI session
if launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null; then
  :
else
  launchctl load -w "$PLIST_DST" || {
    echo "launchctl failed. Try: launchctl load -w $PLIST_DST" >&2
    exit 1
  }
fi

echo ""
echo "OK: HIKARI will start at login and stay running."
echo "   Repo:     $REPO_ROOT"
echo "   Logs:     $OUT_LOG"
echo "   Stop now:  launchctl bootout gui/$(id -u)/$LABEL"
echo "   Uninstall: $REPO_ROOT/scripts/uninstall-hikari-login-agent.sh"
echo ""
echo "Grant Microphone (once): System Settings → Privacy & Security → Microphone"
echo "  Enable for Terminal if you tested there; for background runs, you may need to"
echo "  allow the Python binary under .venv (macOS will prompt on first mic use)."
echo ""
