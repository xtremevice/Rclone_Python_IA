# rclone_manager_windows.spec
# ----------------------------
# PyInstaller spec file for building the Windows executable.
# Run with:  pyinstaller build_scripts/rclone_manager_windows.spec
# Output:    dist/RcloneManager.exe  (single-file executable)

import os
import sys
from pathlib import Path

# Project root is one level up from this build_scripts/ directory
ROOT = Path(os.path.abspath(SPECPATH)).parent
ASSETS = str(ROOT / "assets")

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Bundle the assets folder so the app can find icons at runtime
        (ASSETS, "assets"),
    ],
    hiddenimports=[
        "pystray._win32",
        "PIL._imaging",
        "PIL.ImageFont",
        "PIL.ImageDraw",
        "psutil",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RcloneManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                         # No console window (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),  # Windows taskbar icon
    onefile=True,
)
