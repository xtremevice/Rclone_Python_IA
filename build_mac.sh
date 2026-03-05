#!/usr/bin/env bash
# build_mac.sh — Script para construir el ejecutable único para macOS
#
# Uso: bash build_mac.sh
#
# Requisitos previos:
#   - macOS con Python 3.8+, pip
#   - PyQt5: pip install PyQt5
#   - pyinstaller: pip install pyinstaller

set -e

APP_NAME="RclonePythonIA"
APP_VERSION="1.0.0"
DIST_DIR="dist"

echo "=============================="
echo " Rclone Python IA - macOS Build"
echo "=============================="

# 1. Instalar dependencias Python
echo "[1/3] Instalando dependencias Python..."
pip install -r requirements.txt --quiet

# 2. Construir el ejecutable de un solo archivo con PyInstaller
echo "[2/3] Construyendo ejecutable macOS con PyInstaller..."
pyinstaller \
    --name "${APP_NAME}" \
    --windowed \
    --onefile \
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
    # --icon src/assets/icon.icns   # Descomentar si se tiene un icono .icns

echo "[3/3] Ejecutable creado."
echo ""
echo "✔ Ejecutable macOS: ${DIST_DIR}/${APP_NAME}"
echo ""
echo "Para distribuir como .app bundle, ejecuta con --windowed sin --onefile"
echo "y empaqueta la carpeta ${APP_NAME}.app resultante."
