# -*- mode: python ; coding: utf-8 -*-
# build/rclonepyia.spec
#
# PyInstaller spec file that produces:
#   • Windows: single-file EXE (--onefile)
#   • Linux:   single binary used inside an AppImage
#   • macOS:   single-file app bundle
#
# Usage:
#   pyinstaller build/rclonepyia.spec
#
# The output lands in dist/ relative to the project root.

import sys
import os

block_cipher = None

# Resolve the project root (one level up from this spec file)
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # Bundle any assets (icons, etc.) found in the assets/ directory
        (os.path.join(ROOT, 'assets'), 'assets'),
    ],
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.sip',
        'src',
        'src.app',
        'src.core',
        'src.core.config',
        'src.core.rclone',
        'src.core.service_manager',
        'src.windows',
        'src.windows.setup_wizard',
        'src.windows.main_window',
        'src.windows.config_window',
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

# ── Single-file executable ──────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RclonePyIA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # GUI app – no terminal window on Windows/macOS
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows-specific: embed a version resource and icon
    version=None,
    icon=os.path.join(ROOT, 'assets', 'icon.ico') if sys.platform == 'win32' else None,
    onefile=True,
)

# macOS: wrap the EXE inside a .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='RclonePyIA.app',
        icon=os.path.join(ROOT, 'assets', 'icon.icns'),
        bundle_identifier='dev.xtremevice.rclonepyia',
    )
