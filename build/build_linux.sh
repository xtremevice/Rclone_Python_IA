#!/usr/bin/env bash
# build/build_linux.sh
#
# Build a portable Linux AppImage for RclonePyIA.
#
# Requirements:
#   • Python 3.9+ with pip
#   • appimagetool (https://github.com/AppImage/AppImageKit/releases)
#     – or downloaded automatically by this script if not in PATH
#   • The project dependencies (see requirements.txt)
#
# Usage:
#   chmod +x build/build_linux.sh
#   ./build/build_linux.sh
#
# Output:
#   dist/RclonePyIA-x86_64.AppImage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_ROOT/dist"
BUILD_DIR="$PROJECT_ROOT/build_tmp"
APPDIR="$BUILD_DIR/AppDir"

cd "$PROJECT_ROOT"

echo "── Installing Python dependencies ──────────────────────────────"
pip install --quiet -r requirements.txt

echo "── Running PyInstaller ─────────────────────────────────────────"
pyinstaller --distpath "$DIST_DIR" --workpath "$BUILD_DIR/work" \
    "$SCRIPT_DIR/rclonepyia.spec"

BINARY="$DIST_DIR/RclonePyIA"
if [[ ! -f "$BINARY" ]]; then
    echo "ERROR: PyInstaller did not produce $BINARY" >&2
    exit 1
fi

echo "── Preparing AppDir structure ──────────────────────────────────"
mkdir -p "$APPDIR/usr/bin"
cp "$BINARY" "$APPDIR/usr/bin/RclonePyIA"
chmod +x "$APPDIR/usr/bin/RclonePyIA"

# Desktop entry
cat > "$APPDIR/RclonePyIA.desktop" <<EOF
[Desktop Entry]
Name=RclonePyIA
Exec=RclonePyIA
Icon=RclonePyIA
Type=Application
Categories=Utility;FileManager;
EOF

# Icon (copy from assets or create a placeholder)
if [[ -f "$PROJECT_ROOT/assets/icon.png" ]]; then
    cp "$PROJECT_ROOT/assets/icon.png" "$APPDIR/RclonePyIA.png"
else
    # Generate a minimal 64×64 PNG icon using Python
    python3 - <<PYEOF
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPixmap, QPainter, QColor
import sys
app = QApplication(sys.argv)
pix = QPixmap(64, 64)
pix.fill(QColor(0, 0, 0, 0))
painter = QPainter(pix)
painter.setBrush(QColor("#4A90E2"))
painter.setPen(QColor(0, 0, 0, 0))
painter.drawEllipse(8, 24, 32, 28)
painter.drawEllipse(20, 18, 28, 28)
painter.drawEllipse(36, 22, 24, 24)
painter.drawRect(12, 36, 44, 12)
painter.end()
pix.save("$APPDIR/RclonePyIA.png")
PYEOF
fi

# AppRun entry point
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/usr/bin/RclonePyIA" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "── Downloading appimagetool if needed ──────────────────────────"
APPIMAGETOOL_BIN="$BUILD_DIR/appimagetool"
if ! command -v appimagetool &>/dev/null; then
    TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    curl -L --silent --show-error -o "$APPIMAGETOOL_BIN" "$TOOL_URL"
    chmod +x "$APPIMAGETOOL_BIN"
else
    APPIMAGETOOL_BIN="$(command -v appimagetool)"
fi

echo "── Building AppImage ───────────────────────────────────────────"
"$APPIMAGETOOL_BIN" "$APPDIR" "$DIST_DIR/RclonePyIA-x86_64.AppImage"

echo ""
echo "✅  AppImage created: $DIST_DIR/RclonePyIA-x86_64.AppImage"
