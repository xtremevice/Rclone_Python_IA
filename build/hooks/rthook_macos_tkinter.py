"""
macOS runtime hook – fix Tcl/Tk library paths in PyInstaller bundles.

When frozen in a .app bundle, Tkinter can fail to initialise because the
Tcl/Tk library directories are not in the locations the framework expects.
``TkpInit`` calls ``Tcl_Init()``, which searches for ``init.tcl`` using the
``TCL_LIBRARY`` environment variable (falling back to a compiled-in path that
no longer exists inside the bundle).  When it cannot find ``init.tcl`` it
calls ``Tcl_Panic`` → ``abort``, crashing the app immediately on launch.

This hook runs *before* any application code and explicitly sets
``TCL_LIBRARY`` / ``TK_LIBRARY`` to the bundled copies so that ``TkpInit``
finds them without panicking.  It is registered via ``runtime_hooks`` in
``rclone_manager.spec`` and only takes effect on macOS.

Search order for each library tree (first match wins):
1. Root of ``sys._MEIPASS`` (where the spec's explicit collect places them)
2. Inside any ``Python3.framework`` or ``Python.framework`` embedded by
   PyInstaller under ``Contents/Frameworks/``
3. ``sys.prefix/lib`` (development / non-frozen mode fallback)
"""
import glob as _glob
import os
import sys

if sys.platform == "darwin":
    # _MEIPASS is the directory holding all collected files:
    #   onefile → a per-run temp dir  (~/.../MEI-xxxxxx/)
    #   onedir  → Contents/MacOS/ inside the .app bundle  (fixed path)
    _meipass = getattr(sys, "_MEIPASS", None)
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))

    # Build a prioritised list of directories to search.
    _search_dirs: list = []

    # 1. Bundle root (_MEIPASS) – the explicit collect in the spec puts the
    #    tcl8.6/ and tk8.6/ trees here.
    if _meipass:
        _search_dirs.append(_meipass)

    # 2. Any Python.framework embedded under Contents/Frameworks/.
    #    PyInstaller copies the Python.framework here; for python.org builds
    #    the Tcl/Tk script trees live inside it at Versions/<v>/lib/.
    for _fw_name in ("Python3.framework", "Python.framework"):
        _fw_lib_glob = os.path.join(
            _exe_dir, "..", "Frameworks", _fw_name, "Versions", "*", "lib"
        )
        _search_dirs.extend(_glob.glob(_fw_lib_glob))

    # 3. sys.prefix/lib – works in development mode and as a last resort.
    _prefix_lib = os.path.join(sys.prefix, "lib")
    if os.path.isdir(_prefix_lib):
        _search_dirs.append(_prefix_lib)

    # Set TCL_LIBRARY and TK_LIBRARY from the first directory that contains
    # the matching tree, skipping variables the user has already set.
    for _pattern, _envvar in (("tcl8*", "TCL_LIBRARY"), ("tk8*", "TK_LIBRARY")):
        if _envvar not in os.environ:
            for _sdir in _search_dirs:
                _hits = sorted(_glob.glob(os.path.join(_sdir, _pattern)))
                if _hits:
                    os.environ[_envvar] = _hits[0]
                    break
