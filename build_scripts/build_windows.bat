@echo off
REM Script de construcción para Windows
REM Genera un archivo .exe de un solo archivo ejecutable

echo ================================================
echo   Rclone Python IA - Build para Windows
echo ================================================

REM Verificar Python
python --version
if errorlevel 1 (
    echo ERROR: Python no encontrado. Instale Python 3.8+
    exit /b 1
)

REM Instalar dependencias
echo Instalando dependencias...
pip install -r requirements.txt

REM Construir ejecutable con PyInstaller
echo Construyendo ejecutable...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "RclonePythonIA" ^
    --icon "resources/icon.ico" ^
    --add-data "resources;resources" ^
    --hidden-import "PyQt5.sip" ^
    --hidden-import "PyQt5.QtCore" ^
    --hidden-import "PyQt5.QtWidgets" ^
    --hidden-import "PyQt5.QtGui" ^
    main.py

if errorlevel 1 (
    echo ERROR: La construccion fallo. Revise los errores arriba.
    exit /b 1
)

echo.
echo ================================================
echo   Build exitoso: dist/RclonePythonIA.exe
echo ================================================
