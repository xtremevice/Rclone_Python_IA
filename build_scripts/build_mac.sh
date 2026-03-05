#!/usr/bin/env bash
# Script de construcción para macOS
# Genera un archivo ejecutable de un solo archivo para macOS
#
# Requisitos:
#   - Python 3.8+
#   - pip
#   - Xcode Command Line Tools (para codesign)

set -e

echo "================================================"
echo "  Rclone Python IA - Build para macOS"
echo "================================================"

# Verificar Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 no encontrado. Instale Python 3.8+"
    exit 1
fi

python3 --version

# Instalar dependencias
echo "Instalando dependencias..."
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

# Construir con PyInstaller - un solo archivo ejecutable para macOS
echo "Construyendo ejecutable para macOS..."
pyinstaller \
    --onefile \
    --windowed \
    --name "RclonePythonIA" \
    --osx-bundle-identifier "com.rclone-python-ia.app" \
    --add-data "resources:resources" \
    --hidden-import "PyQt5.sip" \
    --hidden-import "PyQt5.QtCore" \
    --hidden-import "PyQt5.QtWidgets" \
    --hidden-import "PyQt5.QtGui" \
    main.py

echo ""
echo "================================================"
echo "  Build exitoso: dist/RclonePythonIA"
echo "================================================"
echo ""
echo "Para crear un .app bundle, use:"
echo "  pyinstaller --windowed --onedir --name RclonePythonIA main.py"
echo ""
echo "El ejecutable se encuentra en: dist/RclonePythonIA"
