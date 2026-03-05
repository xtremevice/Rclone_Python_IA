#!/usr/bin/env bash
# build_mac.sh
# -------------
# Builds the Rclone Manager macOS single-file application.
#
# Prerequisites:
#   pip install pyinstaller
#
# Usage:
#   chmod +x build_scripts/build_mac.sh
#   ./build_scripts/build_mac.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$ROOT_DIR/dist"
APP_NAME="RcloneManager"

echo "=== Rclone Manager – macOS Builder ==="
echo "Project root: $ROOT_DIR"

# ---- Step 1: Generate icon ----
echo ""
echo "[1/3] Generating application icon..."
cd "$ROOT_DIR"
python3 assets/generate_icon.py

# ---- Step 2: Build macOS .app bundle with PyInstaller ----
echo ""
echo "[2/3] Building .app bundle with PyInstaller..."
pyinstaller \
    --onefile \
    --windowed \
    --name "$APP_NAME" \
    --add-data "assets:assets" \
    --hidden-import "pystray._darwin" \
    --hidden-import "PIL._imaging" \
    --hidden-import "PIL.ImageFont" \
    --hidden-import "PIL.ImageDraw" \
    --hidden-import "psutil" \
    --hidden-import "tkinter" \
    --hidden-import "tkinter.ttk" \
    --hidden-import "tkinter.filedialog" \
    --hidden-import "tkinter.messagebox" \
    --osx-bundle-identifier "com.rclonemanager.app" \
    "$ROOT_DIR/main.py"

echo "[2/3] Build complete."

# ---- Step 3: Create a distributable DMG (optional) ----
echo ""
echo "[3/3] Packaging..."
if command -v hdiutil &>/dev/null; then
    DMG_PATH="$DIST_DIR/${APP_NAME}.dmg"
    hdiutil create \
        -volname "Rclone Manager" \
        -srcfolder "$DIST_DIR/${APP_NAME}.app" \
        -ov \
        -format UDZO \
        "$DMG_PATH"
    echo "DMG created: $DMG_PATH"
else
    echo "hdiutil not found – skipping DMG creation."
fi

echo ""
echo "=== Build complete ==="
echo "Single executable: $DIST_DIR/$APP_NAME"
if [ -f "$DIST_DIR/${APP_NAME}.app/Contents/MacOS/$APP_NAME" ]; then
    echo "App bundle:        $DIST_DIR/${APP_NAME}.app"
fi
