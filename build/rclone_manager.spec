# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Rclone Manager.

Produces a single-file executable containing all Python dependencies.
Usage:
    pyinstaller build/rclone_manager.spec

On macOS this creates a proper .app bundle.  When running on an Apple Silicon
(arm64) machine the resulting bundle will be a native arm64 executable; when
running on an Intel (x86_64) machine it will be a native x86_64 executable.
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Root of the source tree (one level above build/)
ROOT = Path(SPECPATH).parent  # noqa: F821 – SPECPATH is injected by PyInstaller

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include the assets directory
        (str(ROOT / "assets"), "assets"),
    ],
    hiddenimports=[
        "pystray._xorg",    # Linux tray backend
        "pystray._win32",   # Windows tray backend
        "pystray._darwin",  # macOS tray backend
        "PIL._imaging",
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
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
    console=False,           # GUI app – no console window
    disable_windowed_traceback=False,
    # argv_emulation must be False for arm64 and arm64-only builds; it is only
    # needed for universal2 builds running under Rosetta on Intel Macs.
    argv_emulation=False,
    # target_arch=None means "native architecture of the build machine".
    # The CI workflow runs separate jobs on macos-13 (x86_64) and macos-14
    # (arm64) so each produces a native binary for its target platform.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.png"),
)

# On macOS, also build an .app bundle
if sys.platform == "darwin":
    import platform as _platform
    _arch = _platform.machine()  # "x86_64" or "arm64"
    app = BUNDLE(  # noqa: F821
        exe,
        name="RcloneManager.app",
        icon=str(ROOT / "assets" / "icon.png"),
        bundle_identifier="com.xtremevice.rclonemanager",
        info_plist={
            # Declare that this is a native macOS app (not a UIKit port).
            # This key prevents macOS from treating the bundle as needing
            # Rosetta translation when it is already a native arm64 binary.
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
        },
    )
