#!/usr/bin/env bash
set -euo pipefail

TARGET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bin/Hikari"
if [[ -n "${HIKARI_CLI_DIR:-}" ]]; then
  CANDIDATE_DIRS=("$HIKARI_CLI_DIR")
else
  CANDIDATE_DIRS=("$HOME/.local/bin" "$HOME/bin")
fi

removed=0
for dir in "${CANDIDATE_DIRS[@]}"; do
  [[ -n "$dir" ]] || continue
  for name in hikari Hikari; do
    path="$dir/$name"
    if [[ -L "$path" ]] && [[ "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$path")" == "$TARGET" ]]; then
      rm "$path"
      echo "Removed $path"
      removed=1
    fi
  done
done

if [[ "$removed" -eq 0 ]]; then
  echo "No HIKARI CLI links found for this repo."
fi
