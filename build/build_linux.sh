#!/usr/bin/env bash
# Build script for Linux – produces RcloneManager-x86_64.AppImage
#
# Prerequisites:
#   - Python 3.10+  (python3 on PATH)
#   - pip
#   - appimagetool  (https://github.com/AppImage/AppImageKit/releases)
#   - appimagetool must be on PATH or set APPIMAGETOOL env variable

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

cd "$ROOT"

echo "[1/5] Installing Python dependencies..."
pip3 install -r requirements.txt

echo "[2/5] Generating icon..."
python3 assets/create_icon.py || true

echo "[3/5] Building executable with PyInstaller..."
pyinstaller build/rclone_manager.spec \
    --distpath dist/linux \
    --workpath build/work_linux \
    --clean

echo "[4/5] Preparing AppDir..."
APPDIR="build/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy PyInstaller output
cp -r dist/linux/RcloneManager "$APPDIR/usr/bin/RcloneManager"

# Copy icon
cp assets/icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/rclonemanager.png"
cp assets/icon.png "$APPDIR/rclonemanager.png"

# Desktop entry
cat > "$APPDIR/rclonemanager.desktop" <<EOF
[Desktop Entry]
Name=Rclone Manager
Exec=RcloneManager
Icon=rclonemanager
Type=Application
Categories=Network;FileManager;
Comment=Multiplatform cloud storage sync manager powered by rclone
EOF

# AppRun launcher
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/usr/bin/RcloneManager" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "[5/5] Packaging AppImage..."
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"
"$APPIMAGETOOL" "$APPDIR" "dist/linux/RcloneManager-x86_64.AppImage"

echo ""
echo "Build complete: dist/linux/RcloneManager-x86_64.AppImage"
