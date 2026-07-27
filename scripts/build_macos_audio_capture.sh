#!/usr/bin/env bash
# Build hikari-macos-audio-capture for local development.
# Requires Swift 5.9+ / Xcode CLT. Does not download dependencies.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/native/macos_audio_capture"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
if [[ -z "${SDKROOT:-}" && -d /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk ]]; then
  export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk
fi
export CLANG_MODULE_CACHE_PATH="${CLANG_MODULE_CACHE_PATH:-$PKG/.build/clang-module-cache}"
export SWIFTPM_MODULECACHE_OVERRIDE="${SWIFTPM_MODULECACHE_OVERRIDE:-$PKG/.build/swift-module-cache}"
cd "$PKG"
swift build -c release
BIN="$PKG/.build/release/hikari-macos-audio-capture"
echo "built: $BIN"
"$PKG/.build/release/hikari-macos-audio-capture-tests"
