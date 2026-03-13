# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Rclone Manager.

Usage:
    pyinstaller build/rclone_manager.spec

Platform behaviour
------------------
macOS
    Produces a proper .app bundle using ``--onedir`` mode
    (``exclude_binaries=True`` + ``COLLECT`` + ``BUNDLE``).
    This is required to prevent the ``TkpInit`` crash seen on Apple Silicon
    (and occasionally on Intel) Macs.  In ``--onefile`` mode all libraries are
    extracted to a random temp directory at runtime; Tk cannot find the Tcl
    script library files it needs to finish initialising and calls
    ``Tcl_Panic`` → ``abort``.  With ``--onedir`` the Tcl/Tk trees sit in a
    fixed location inside Contents/MacOS/ and Tk can always find them.
    An additional runtime hook (build/hooks/rthook_macos_tkinter.py) sets
    ``TCL_LIBRARY`` / ``TK_LIBRARY`` explicitly as a belt-and-suspenders
    safety net.

Windows / Linux
    Produces a single-file executable (``--onefile`` style, the current
    default) which works fine on those platforms.
"""

import sys
from pathlib import Path

block_cipher = None

# Root of the source tree (one level above build/)
ROOT = Path(SPECPATH).parent  # noqa: F821 – SPECPATH is injected by PyInstaller

# ---------------------------------------------------------------------------
# Collect all of tkinter's Python files, data files (Tcl/Tk library trees,
# theme files, …) and binary extensions into the bundle.  PyInstaller ships a
# built-in tkinter hook, but being explicit here ensures the Tcl/Tk library
# directories (tcl8.6/, tk8.6/) are always present in the collected output,
# which is what the runtime hook and TkpInit both rely on.
# ---------------------------------------------------------------------------
from PyInstaller.utils.hooks import collect_all  # noqa: E402

tk_datas, tk_binaries, tk_hiddenimports = collect_all("tkinter")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=tk_binaries,
    datas=[
        # Include the assets directory
        (str(ROOT / "assets"), "assets"),
        *tk_datas,
    ],
    hiddenimports=[
        "pystray._xorg",    # Linux tray backend
        "pystray._win32",   # Windows tray backend
        "pystray._darwin",  # macOS tray backend
        "PIL._imaging",
        # cryptography uses C extensions that PyInstaller may not detect
        # automatically via static analysis.
        "cryptography",
        "cryptography.fernet",
        "cryptography.hazmat.primitives.ciphers",
        "cryptography.hazmat.backends.openssl",
        *tk_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    # The macOS Tcl/Tk path-fix hook must run before tkinter is imported.
    runtime_hooks=[
        str(ROOT / "build" / "hooks" / "rthook_macos_tkinter.py"),
    ],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

# ---------------------------------------------------------------------------
# macOS: --onedir style (exclude_binaries=True + COLLECT + BUNDLE)
#
# All shared libraries / Tcl-Tk trees are placed *next to* the executable
# inside Contents/MacOS/ at a stable path, not extracted to a temp dir.
# This is the only reliable way to make TkpInit work in a .app bundle.
# ---------------------------------------------------------------------------
if sys.platform == "darwin":
    import platform as _platform

    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],                  # binaries/datas go into COLLECT, not the exe
        exclude_binaries=True,
        name="RcloneManager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,           # UPX can break codesigning on macOS; skip it
        console=False,       # GUI app – no console window
        disable_windowed_traceback=False,
        # argv_emulation must be False for arm64 builds; it is only needed for
        # universal2 binaries running under Rosetta on Intel Macs.
        argv_emulation=False,
        # target_arch=None → native architecture of the build machine.
        # The CI runs separate jobs on macos-13 (x86_64) and macos-14 (arm64).
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "assets" / "icon.png"),
    )

    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="RcloneManager",
    )

    app = BUNDLE(  # noqa: F821
        coll,
        name="RcloneManager.app",
        icon=str(ROOT / "assets" / "icon.png"),
        bundle_identifier="com.xtremevice.rclonemanager",
        info_plist={
            # Declare that this is a native macOS app (not a UIKit port).
            # LSMinimumSystemVersion 11.0 = macOS Big Sur, the first release
            # with Apple Silicon support.
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
        },
    )

# ---------------------------------------------------------------------------
# Windows / Linux: --onefile style (all libraries packed into the executable)
# ---------------------------------------------------------------------------
else:
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
        console=False,       # GUI app – no console window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "assets" / "icon.png"),
    )
