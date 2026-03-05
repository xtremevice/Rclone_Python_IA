@echo off
REM Build script for Windows – produces RcloneManager.exe
REM Prerequisites: Python 3.10+, pip install -r requirements.txt

cd /d "%~dp0.."

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    exit /b 1
)

echo [2/3] Generating icon...
python assets\create_icon.py

echo [3/3] Building executable with PyInstaller...
pyinstaller build\rclone_manager.spec --distpath dist\windows --workpath build\work_windows --clean
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)

echo.
echo Build complete: dist\windows\RcloneManager.exe
