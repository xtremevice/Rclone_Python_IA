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
    (and occasionally on Intel) Macs.  There are two distinct failure modes
    that must both be solved:

    1. *Wrong extraction path* (the original crash):  In ``--onefile`` mode
       all libraries are extracted to a random temp directory at runtime.  Tk
       cannot find the Tcl script library files it needs to finish initialising
       (they are embedded at a path the Tcl interpreter does not know about)
       and calls ``Tcl_Panic`` → ``abort``.

    2. *Missing Tcl/Tk script trees* (the residual crash after switching to
       ``--onedir``):  ``collect_all("tkinter")`` only collects Python-level
       package files (``.py``, ``.so``).  The Tcl/Tk ``.tcl`` script
       library trees (``tcl8.6/``, ``tk8.6/``) live in ``sys.prefix/lib/``
       and are **not** included by that call.  ``TkpInit`` calls
       ``Tcl_Init()``, which searches for ``init.tcl`` / ``tk.tcl`` using
       the ``TCL_LIBRARY`` / ``TK_LIBRARY`` environment variables (or a
       compiled-in path that no longer exists in the bundle).  When neither
       is set and the path is wrong, ``Tcl_Init`` returns ``TCL_ERROR`` and
       ``TkpInit`` calls ``Tcl_Panic`` → ``abort``.

    Both problems are solved together:
    * The spec explicitly collects ``tcl8.6/`` and ``tk8.6/`` from
      ``sys.prefix/lib/`` into the ROOT of ``_MEIPASS`` (``Contents/MacOS/``
      inside the ``.app``).
    * The runtime hook (``build/hooks/rthook_macos_tkinter.py``) sets
      ``TCL_LIBRARY`` / ``TK_LIBRARY`` before any application code runs,
      searching the bundle root, the embedded Python.framework, and
      ``sys.prefix/lib`` as fallbacks.

Windows / Linux
    Produces a single-file executable (``--onefile`` style) which works fine
    on those platforms.
"""

import glob
import os
import sys
from pathlib import Path

block_cipher = None

# Root of the source tree (one level above build/)
ROOT = Path(SPECPATH).parent  # noqa: F821 – SPECPATH is injected by PyInstaller

# ---------------------------------------------------------------------------
# Collect Python-level tkinter files, binaries and data.
# NOTE: this does NOT include the Tcl/Tk .tcl script library trees; those are
# gathered explicitly below.
# ---------------------------------------------------------------------------
from PyInstaller.utils.hooks import collect_all  # noqa: E402

tk_datas, tk_binaries, tk_hiddenimports = collect_all("tkinter")

# ---------------------------------------------------------------------------
# Explicitly collect the Tcl/Tk script library trees.
#
# Tk's TkpInit calls Tcl_Init(), which searches for init.tcl using the
# TCL_LIBRARY environment variable (or a compiled-in path).  Inside a
# PyInstaller bundle that compiled-in path no longer exists, so we must
# ship the script trees and point Tcl/Tk to them via the runtime hook.
#
# The trees live at <sys.prefix>/lib/tcl8.x/ and <sys.prefix>/lib/tk8.x/.
# We collect them to the *root* of _MEIPASS (destination = "tcl8.x" /
# "tk8.x") so that the runtime hook can find them with a simple glob.
# ---------------------------------------------------------------------------
_tcl_tk_extra_datas: list = []
_lib_dir = os.path.join(sys.prefix, "lib")
for _src in glob.glob(os.path.join(_lib_dir, "tcl8*")) + glob.glob(
    os.path.join(_lib_dir, "tk8*")
):
    if os.path.isdir(_src):
        _tcl_tk_extra_datas.append((_src, os.path.basename(_src)))

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=tk_binaries,
    datas=[
        # Include the assets directory
        (str(ROOT / "assets"), "assets"),
        # Python-level tkinter data files
        *tk_datas,
        # Tcl/Tk .tcl script library trees – required by TkpInit / Tcl_Init
        *_tcl_tk_extra_datas,
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
