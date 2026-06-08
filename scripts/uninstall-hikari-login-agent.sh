#!/usr/bin/env bash
# Remove HIKARI Launch Agent (macOS).
set -euo pipefail

PLIST_DST="$HOME/Library/LaunchAgents/com.hikari.assistant.plist"
LABEL="com.hikari.assistant"
OLD_LABELS=("com.hikari.wake" "com.hikari.alwayson")
OLD_PLISTS=("$HOME/Library/LaunchAgents/com.hikari.wake.plist" "$HOME/Library/LaunchAgents/com.hikari.alwayson.plist")

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS only." >&2
  exit 1
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true

for l in "${OLD_LABELS[@]}"; do
  launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
done
for p in "${OLD_PLISTS[@]}"; do
  launchctl unload "$p" 2>/dev/null || true
done

if [[ -f "$PLIST_DST" ]]; then
  rm -f "$PLIST_DST"
  echo "Removed $PLIST_DST"
else
  echo "No plist at $PLIST_DST"
fi

for p in "${OLD_PLISTS[@]}"; do
  if [[ -f "$p" ]]; then
    rm -f "$p"
    echo "Removed $p"
  fi
done
