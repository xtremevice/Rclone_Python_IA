"""
macOS runtime hook – fix Tcl/Tk library paths in PyInstaller bundles.

When frozen in a .app bundle, Tkinter can fail to initialise because the
Tcl/Tk library directories are not in the locations the framework expects.
``TkpInit`` tries to source its own Tcl scripts, fails to find them and calls
``Tcl_Panic`` → ``abort``, crashing the app immediately on launch.

This hook runs *before* any application code and explicitly sets
``TCL_LIBRARY`` / ``TK_LIBRARY`` to the bundled copies so that ``TkpInit``
finds them without panicking.  It is registered via ``runtime_hooks`` in
``rclone_manager.spec`` and only takes effect on macOS.
"""
import glob as _glob
import os
import sys

if sys.platform == "darwin":
    # In a PyInstaller bundle, sys._MEIPASS points to the directory that
    # holds all collected files:
    #   onefile  → a per-run temp dir  (~/.../MEI-xxxxxx/)
    #   onedir   → Contents/MacOS/ inside the .app bundle  (fixed path)
    #
    # PyInstaller's own tkinter hook copies the Tcl/Tk library trees into
    # _MEIPASS under names like "tcl8.6" and "tk8.6".  We locate them with
    # a glob and set the env-vars so that Tcl_Init / Tk_Init can find them.
    _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))

    for _pattern, _envvar in (("tcl8*", "TCL_LIBRARY"), ("tk8*", "TK_LIBRARY")):
        if _envvar not in os.environ:
            _candidates = sorted(_glob.glob(os.path.join(_base, _pattern)))
            if _candidates:
                os.environ[_envvar] = _candidates[0]
