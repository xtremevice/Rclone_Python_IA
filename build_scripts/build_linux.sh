#!/usr/bin/env bash
# Script de construcción para Linux
# Genera un archivo AppImage de la aplicación
#
# Requisitos:
#   - Python 3.8+
#   - pip
#   - appimagetool (descargado automáticamente si no está instalado)
#   - fuse (necesario para AppImage): sudo apt install fuse libfuse2

set -e

echo "================================================"
echo "  Rclone Python IA - Build para Linux (AppImage)"
echo "================================================"

# Verificar Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 no encontrado. Instale Python 3.8+"
    exit 1
fi

python3 --version

# Instalar dependencias de Python
echo "Instalando dependencias de Python..."
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

# Construir con PyInstaller primero (genera el directorio dist/)
echo "Construyendo con PyInstaller..."
pyinstaller \
    --onedir \
    --windowed \
    --name "RclonePythonIA" \
    --add-data "resources:resources" \
    --hidden-import "PyQt5.sip" \
    --hidden-import "PyQt5.QtCore" \
    --hidden-import "PyQt5.QtWidgets" \
    --hidden-import "PyQt5.QtGui" \
    main.py

echo "Preparando estructura de AppImage..."

# Crear estructura de directorios del AppImage
APPDIR="AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copiar binarios de PyInstaller al AppDir
cp -r dist/RclonePythonIA/* "$APPDIR/usr/bin/"

# Crear archivo .desktop
cat > "$APPDIR/usr/share/applications/rclone-python-ia.desktop" << EOF
[Desktop Entry]
Name=Rclone Python IA
Comment=Sincronizador de archivos multiplataforma
Exec=RclonePythonIA
Icon=rclone-python-ia
Type=Application
Categories=Utility;FileTools;
Terminal=false
EOF

# Copiar ícono (usar SVG o crear uno básico si no existe)
if [ -f "resources/icon.png" ]; then
    cp resources/icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/rclone-python-ia.png"
else
    echo "Advertencia: No se encontró resources/icon.png, el AppImage no tendrá ícono."
fi

# AppRun script
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/usr/bin/RclonePythonIA" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# Descargar appimagetool si no está instalado
# appimagetool is itself an AppImage; set APPIMAGE_EXTRACT_AND_RUN=1 so it
# runs without FUSE (required in most CI environments such as GitHub Actions).
export APPIMAGE_EXTRACT_AND_RUN=1

if ! command -v appimagetool &> /dev/null; then
    echo "Descargando appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
        -O /tmp/appimagetool
    chmod +x /tmp/appimagetool
    APPIMAGETOOL="/tmp/appimagetool"
else
    APPIMAGETOOL="appimagetool"
fi

# Generar el AppImage
echo "Generando AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "RclonePythonIA-x86_64.AppImage"

echo ""
echo "================================================"
echo "  Build exitoso: RclonePythonIA-x86_64.AppImage"
echo "================================================"
