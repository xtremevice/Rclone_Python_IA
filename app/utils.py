"""
Shared utility helpers for the Rclone Manager application.
"""

import sys
import tkinter as tk
from pathlib import Path
from typing import Tuple


def get_screen_geometry(root: tk.Tk) -> Tuple[int, int]:
    """Return (screen_width, screen_height) in pixels."""
    return root.winfo_screenwidth(), root.winfo_screenheight()


def center_window(
    window: tk.Toplevel | tk.Tk,
    width: int,
    height: int,
) -> None:
    """
    Place *window* at the centre of the primary screen with the given size.

    Args:
        window: The tkinter window to position.
        width: Desired window width in pixels.
        height: Desired window height in pixels.
    """
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    window.geometry(f"{width}x{height}+{x}+{y}")


def get_assets_dir() -> Path:
    """
    Return the path to the 'assets' directory whether running from source
    or from a frozen PyInstaller bundle.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller bundles assets next to the executable
        return Path(sys.executable).parent / "assets"
    return Path(__file__).parent.parent / "assets"


def open_folder(path: str) -> None:
    """Open *path* in the operating-system's default file manager."""
    import subprocess
    import os

    folder = Path(path)
    if not folder.is_dir():
        return
    if sys.platform == "win32":
        os.startfile(str(folder))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def apply_theme(root: tk.Tk) -> None:
    """Apply a consistent ttk theme to the application root window."""
    from tkinter import ttk

    style = ttk.Style(root)
    # 'clam' is available cross-platform and provides a clean flat look
    available = style.theme_names()
    if "clam" in available:
        style.theme_use("clam")

    # Common widget styling
    style.configure("TNotebook.Tab", padding=[12, 4])
    style.configure("TButton", padding=[8, 4])
    style.configure("Header.TLabel", font=("TkDefaultFont", 10, "bold"))
    style.configure(
        "Status.TLabel",
        foreground="#007bff",
        font=("TkDefaultFont", 9),
    )
    style.configure(
        "StatusOk.TLabel",
        foreground="#28a745",
        font=("TkDefaultFont", 9),
    )
    style.configure(
        "StatusErr.TLabel",
        foreground="#dc3545",
        font=("TkDefaultFont", 9),
    )
