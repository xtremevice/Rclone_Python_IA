#!/usr/bin/env bash
# build_linux.sh
# ---------------
# Builds the Rclone Manager Linux AppImage.
#
# Prerequisites (install once):
#   pip install pyinstaller
#   wget -O appimagetool "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
#   chmod +x appimagetool && sudo mv appimagetool /usr/local/bin/appimagetool
#
# Usage:
#   chmod +x build_scripts/build_linux.sh
#   ./build_scripts/build_linux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$ROOT_DIR/dist"
APP_NAME="RcloneManager"

echo "=== Rclone Manager – Linux AppImage Builder ==="
echo "Project root: $ROOT_DIR"

# ---- Step 1: Generate icon ----
echo ""
echo "[1/4] Generating application icon..."
cd "$ROOT_DIR"
python3 assets/generate_icon.py

# ---- Step 2: Build binary with PyInstaller ----
echo ""
echo "[2/4] Building executable with PyInstaller..."
pyinstaller \
    --onefile \
    --noconsole \
    --name "$APP_NAME" \
    --add-data "assets:assets" \
    --hidden-import "pystray._xorg" \
    --hidden-import "PIL._imaging" \
    --hidden-import "PIL.ImageFont" \
    --hidden-import "PIL.ImageDraw" \
    --hidden-import "psutil" \
    --hidden-import "tkinter" \
    --hidden-import "tkinter.ttk" \
    --hidden-import "tkinter.filedialog" \
    --hidden-import "tkinter.messagebox" \
    "$ROOT_DIR/main.py"

echo "[2/4] PyInstaller build complete."

# ---- Step 3: Create AppDir structure ----
echo ""
echo "[3/4] Creating AppDir structure..."
APPDIR="$DIST_DIR/${APP_NAME}.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy the PyInstaller binary
cp "$DIST_DIR/$APP_NAME" "$APPDIR/usr/bin/$APP_NAME"
chmod +x "$APPDIR/usr/bin/$APP_NAME"

# Copy icon
cp "$ROOT_DIR/assets/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/rclone-manager.png"
cp "$ROOT_DIR/assets/icon.png" "$APPDIR/rclone-manager.png"

# Desktop entry file
cat > "$APPDIR/usr/share/applications/rclone-manager.desktop" <<EOF
[Desktop Entry]
Name=Rclone Manager
Comment=Multi-platform rclone sync manager
Exec=RcloneManager
Icon=rclone-manager
Type=Application
Categories=Utility;Network;FileTransfer;
Terminal=false
EOF

# AppStream metadata symlinks required by AppImageKit
cp "$APPDIR/usr/share/applications/rclone-manager.desktop" "$APPDIR/rclone-manager.desktop"

# AppRun script
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
SELF="$(readlink -f "$0")"
HERE="$(dirname "$SELF")"
exec "$HERE/usr/bin/RcloneManager" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# ---- Step 4: Package as AppImage ----
echo ""
echo "[4/4] Packaging as AppImage..."
if command -v appimagetool &>/dev/null; then
    ARCH=x86_64 appimagetool "$APPDIR" "$DIST_DIR/${APP_NAME}-x86_64.AppImage"
    echo ""
    echo "=== Build complete ==="
    echo "AppImage: $DIST_DIR/${APP_NAME}-x86_64.AppImage"
else
    echo "WARNING: appimagetool not found – AppDir created at $APPDIR"
    echo "Install appimagetool and run: appimagetool $APPDIR $DIST_DIR/${APP_NAME}-x86_64.AppImage"
fi
