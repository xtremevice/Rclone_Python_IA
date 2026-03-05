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
pip3 install -r requirements.txt

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
