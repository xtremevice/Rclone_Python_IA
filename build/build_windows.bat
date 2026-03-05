@echo off
REM build/build_windows.bat
REM
REM Build a single-file Windows EXE for RclonePyIA.
REM
REM Requirements:
REM   • Python 3.9+ (from python.org) with pip in PATH
REM   • Windows 10/11
REM
REM Usage:
REM   build\build_windows.bat
REM
REM Output:
REM   dist\RclonePyIA.exe

cd /d "%~dp0.."

echo -- Installing Python dependencies --------------------------------
pip install --quiet -r requirements.txt
if errorlevel 1 ( echo ERROR: pip install failed & exit /b 1 )

echo -- Running PyInstaller -------------------------------------------
pyinstaller --distpath dist --workpath build_tmp\work build\rclonepyia.spec
if errorlevel 1 ( echo ERROR: PyInstaller failed & exit /b 1 )

echo.
echo [OK]  Windows EXE: dist\RclonePyIA.exe
