#!/usr/bin/env bash
# Build script for macOS – produces RcloneManager (single executable)
#
# Prerequisites:
#   - Python 3.10+  (python3 on PATH)
#   - pip
#   - Xcode Command Line Tools (for codesigning, optional)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

cd "$ROOT"

echo "[1/3] Installing Python dependencies..."
pip3 install -r requirements.txt

echo "[2/3] Generating icon..."
python3 assets/create_icon.py || true

echo "[3/3] Building executable with PyInstaller..."
pyinstaller build/rclone_manager.spec \
    --distpath dist/mac \
    --workpath build/work_mac \
    --clean

echo ""
echo "Build complete:"
echo "  App bundle : dist/mac/RcloneManager.app"
echo "  Executable : dist/mac/RcloneManager"
