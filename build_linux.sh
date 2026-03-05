#!/usr/bin/env bash
# build_linux.sh — Script para construir el AppImage de Linux
#
# Uso: bash build_linux.sh
#
# Requisitos previos:
#   - Python 3.8+, pip, PyQt5
#   - pyinstaller: pip install pyinstaller
#   - appimagetool disponible o descargado automáticamente

set -e

APP_NAME="RclonePythonIA"
APP_VERSION="1.0.0"
DIST_DIR="dist"
APPDIR="${DIST_DIR}/${APP_NAME}.AppDir"
APPIMAGE_TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"

echo "==============================="
echo " Rclone Python IA - Linux Build"
echo "==============================="

# 1. Instalar dependencias Python
echo "[1/5] Instalando dependencias Python..."
pip install -r requirements.txt --quiet

# 2. Construir el ejecutable con PyInstaller (directorio, no single-file para AppImage)
echo "[2/5] Construyendo ejecutable con PyInstaller..."
pyinstaller \
    --name "${APP_NAME}" \
    --windowed \
    --noconfirm \
    --distpath "${DIST_DIR}" \
    --add-data "src/assets:assets" \
    --hidden-import PyQt5 \
    --hidden-import PyQt5.QtWidgets \
    --hidden-import PyQt5.QtCore \
    --hidden-import PyQt5.QtGui \
    --hidden-import windows.wizard \
    --hidden-import windows.main_window \
    --hidden-import windows.settings_window \
    --hidden-import config \
    --hidden-import rclone_manager \
    --paths src \
    src/main.py

# 3. Preparar la estructura del AppDir
echo "[3/5] Preparando estructura AppDir..."
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

# Copiar los archivos del ejecutable al AppDir
cp -r "${DIST_DIR}/${APP_NAME}/." "${APPDIR}/usr/bin/"

# Crear el archivo .desktop para el AppImage
cat > "${APPDIR}/${APP_NAME}.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Rclone Python IA
Exec=${APP_NAME}
Icon=${APP_NAME}
Comment=Sincronizador de archivos multiplataforma con rclone
Categories=Utility;FileManager;
Terminal=false
EOF

# Copiar el mismo desktop a la ubicación estándar
cp "${APPDIR}/${APP_NAME}.desktop" "${APPDIR}/usr/share/applications/"

# Crear AppRun (punto de entrada del AppImage)
cat > "${APPDIR}/AppRun" << 'EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
exec "${HERE}/usr/bin/RclonePythonIA" "$@"
EOF
chmod +x "${APPDIR}/AppRun"

# Usar un icono placeholder si no hay uno real
if [ -f "src/assets/icon.png" ]; then
    cp "src/assets/icon.png" "${APPDIR}/${APP_NAME}.png"
    cp "src/assets/icon.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"
else
    # Crear un icono mínimo con Python si no existe
    python3 -c "
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPixmap, QColor
import sys
app = QApplication(sys.argv)
pix = QPixmap(256, 256)
pix.fill(QColor('#4CAF50'))
pix.save('${APPDIR}/${APP_NAME}.png')
pix.save('${APPDIR}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png')
"
fi

# 4. Descargar appimagetool si no está disponible
echo "[4/5] Verificando appimagetool..."
if ! command -v appimagetool &> /dev/null; then
    echo "  Descargando appimagetool..."
    wget -q -O /tmp/appimagetool "${APPIMAGE_TOOL_URL}"
    chmod +x /tmp/appimagetool
    APPIMAGETOOL="/tmp/appimagetool"
else
    APPIMAGETOOL="appimagetool"
fi

# 5. Construir el AppImage
echo "[5/5] Construyendo AppImage..."
ARCH=x86_64 "${APPIMAGETOOL}" "${APPDIR}" "${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"

echo ""
echo "✔ AppImage creado: ${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
