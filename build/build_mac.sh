#!/usr/bin/env bash
# Build script for macOS – produces RcloneManager.app bundle
#
# This script builds a native .app bundle for the architecture of the host
# machine.  Run it on an Intel (x86_64) Mac to get an Intel binary, or on an
# Apple Silicon (arm64) Mac to get a native arm64 binary.
#
# Prerequisites:
#   - Python 3.10+  (python3 on PATH, matching the target architecture)
#   - pip
#   - Xcode Command Line Tools (for codesigning, optional)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

cd "$ROOT"

echo "[1/3] Installing Python dependencies..."
# Create a virtual environment to avoid the PEP 668
# "externally-managed-environment" error on Python 3.12+ (Debian/Ubuntu).
if [ -z "${VIRTUAL_ENV:-}" ]; then
    python3 -m venv .venv || {
        echo "ERROR: Failed to create virtual environment."
        echo "  On Debian/Ubuntu, install the required package with:"
        echo "    sudo apt install python3-venv"
        exit 1
    }
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo "[2/3] Generating icon..."
python3 assets/create_icon.py || true

ARCH="$(uname -m)"
echo "[3/3] Building native ${ARCH} executable with PyInstaller..."
pyinstaller build/rclone_manager.spec \
    --distpath dist/mac \
    --workpath build/work_mac \
    --clean

echo ""
echo "Build complete (${ARCH}):"
echo "  App bundle : dist/mac/RcloneManager.app"
echo "  Executable : dist/mac/RcloneManager.app/Contents/MacOS/RcloneManager"
