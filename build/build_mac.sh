#!/usr/bin/env bash
# build/build_mac.sh
#
# Build a single-file macOS application bundle for RclonePyIA.
#
# Requirements:
#   • macOS 11+
#   • Python 3.9+ with pip  (preferably from python.org, not Homebrew)
#   • Xcode Command Line Tools
#
# Usage:
#   chmod +x build/build_mac.sh
#   ./build/build_mac.sh
#
# Output:
#   dist/RclonePyIA.app   (macOS app bundle)
#   dist/RclonePyIA       (flat single-file binary, for convenience)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_ROOT/dist"

cd "$PROJECT_ROOT"

echo "── Installing Python dependencies ──────────────────────────────"
pip install --quiet -r requirements.txt

echo "── Running PyInstaller ─────────────────────────────────────────"
pyinstaller --distpath "$DIST_DIR" --workpath "$PROJECT_ROOT/build_tmp/work" \
    "$SCRIPT_DIR/rclonepyia.spec"

echo ""
if [[ -d "$DIST_DIR/RclonePyIA.app" ]]; then
    echo "✅  macOS app bundle: $DIST_DIR/RclonePyIA.app"
else
    echo "✅  macOS binary:      $DIST_DIR/RclonePyIA"
fi
