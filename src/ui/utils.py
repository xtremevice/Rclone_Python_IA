"""
utils.py
--------
Shared UI utilities: window sizing, theme styling, helper widgets.
"""

import platform
import tkinter as tk
from tkinter import ttk


# ------------------------------------------------------------------
# Color palette (light theme)
# ------------------------------------------------------------------
COLORS = {
    "bg": "#F5F6FA",
    "surface": "#FFFFFF",
    "primary": "#2563EB",          # Blue accent
    "primary_hover": "#1D4ED8",
    "secondary": "#64748B",
    "text": "#1E293B",
    "text_light": "#64748B",
    "border": "#E2E8F0",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#EF4444",
    "tab_bg": "#EFF6FF",
    "tab_active": "#2563EB",
    "tab_text": "#1E293B",
    "tab_text_active": "#FFFFFF",
    "button_bg": "#2563EB",
    "button_fg": "#FFFFFF",
    "button_secondary_bg": "#F1F5F9",
    "button_secondary_fg": "#1E293B",
    "list_odd": "#F8FAFC",
    "list_even": "#FFFFFF",
    "sidebar_bg": "#1E293B",
    "sidebar_fg": "#F8FAFC",
    "sidebar_selected": "#2563EB",
}


def get_screen_size(root: tk.Tk):
    """Return (screen_width, screen_height) of the primary monitor."""
    return root.winfo_screenwidth(), root.winfo_screenheight()


def center_window(window: tk.Toplevel | tk.Tk, width: int, height: int):
    """Center a window on screen given explicit pixel dimensions."""
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    window.geometry(f"{width}x{height}+{x}+{y}")


def set_window_size_percent(window: tk.Toplevel | tk.Tk, w_pct: float, h_pct: float):
    """
    Resize a window to a percentage of the screen and center it.

    Parameters
    ----------
    window : tk window or toplevel
    w_pct  : Width as a fraction of screen width (e.g. 0.60 for 60%)
    h_pct  : Height as a fraction of screen height (e.g. 0.70 for 70%)
    """
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    w = int(sw * w_pct)
    h = int(sh * h_pct)
    center_window(window, w, h)
    return w, h


def apply_theme(root: tk.Tk):
    """
    Apply a consistent visual theme to all ttk widgets.
    Uses the 'clam' base theme for cross-platform consistency.
    """
    style = ttk.Style(root)
    style.theme_use("clam")

    # General window background
    root.configure(bg=COLORS["bg"])

    # --- Frame ---
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Sidebar.TFrame", background=COLORS["sidebar_bg"])

    # --- Label ---
    style.configure(
        "TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
        font=("Segoe UI", 10) if platform.system() == "Windows" else ("Helvetica", 10),
    )
    style.configure("Title.TLabel", font=(
        "Segoe UI", 14, "bold") if platform.system() == "Windows" else ("Helvetica", 14, "bold"),
        foreground=COLORS["text"],
        background=COLORS["bg"],
    )
    style.configure("Subtitle.TLabel", font=(
        "Segoe UI", 10) if platform.system() == "Windows" else ("Helvetica", 10),
        foreground=COLORS["text_light"],
        background=COLORS["bg"],
    )
    style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
    style.configure("Sidebar.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["sidebar_fg"])
    style.configure(
        "Status.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text_light"],
        font=("Segoe UI", 9) if platform.system() == "Windows" else ("Helvetica", 9),
    )

    # --- Button ---
    style.configure(
        "Primary.TButton",
        background=COLORS["button_bg"],
        foreground=COLORS["button_fg"],
        borderwidth=0,
        focusthickness=3,
        focuscolor=COLORS["primary"],
        padding=(12, 6),
        font=("Segoe UI", 10) if platform.system() == "Windows" else ("Helvetica", 10),
    )
    style.map(
        "Primary.TButton",
        background=[("active", COLORS["primary_hover"]), ("pressed", COLORS["primary_hover"])],
    )
    style.configure(
        "Secondary.TButton",
        background=COLORS["button_secondary_bg"],
        foreground=COLORS["button_secondary_fg"],
        borderwidth=1,
        padding=(12, 6),
        font=("Segoe UI", 10) if platform.system() == "Windows" else ("Helvetica", 10),
    )
    style.map("Secondary.TButton", background=[("active", COLORS["border"])])

    style.configure(
        "Danger.TButton",
        background=COLORS["error"],
        foreground=COLORS["button_fg"],
        borderwidth=0,
        padding=(12, 6),
    )
    style.map("Danger.TButton", background=[("active", "#DC2626")])

    # --- Notebook (tabs) ---
    style.configure(
        "TNotebook",
        background=COLORS["tab_bg"],
        borderwidth=0,
        tabmargins=[2, 5, 2, 0],
    )
    style.configure(
        "TNotebook.Tab",
        background=COLORS["tab_bg"],
        foreground=COLORS["tab_text"],
        padding=[12, 6],
        borderwidth=0,
        font=("Segoe UI", 10, "bold") if platform.system() == "Windows" else ("Helvetica", 10, "bold"),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["tab_active"])],
        foreground=[("selected", COLORS["tab_text_active"])],
    )

    # --- Treeview (file list, folder tree) ---
    style.configure(
        "TTreeview",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        fieldbackground=COLORS["surface"],
        rowheight=24,
        borderwidth=0,
        font=("Segoe UI", 9) if platform.system() == "Windows" else ("Helvetica", 9),
    )
    style.configure(
        "TTreeview.Heading",
        background=COLORS["border"],
        foreground=COLORS["text"],
        font=("Segoe UI", 9, "bold") if platform.system() == "Windows" else ("Helvetica", 9, "bold"),
        borderwidth=0,
    )
    style.map("TTreeview", background=[("selected", COLORS["primary"])],
              foreground=[("selected", "#FFFFFF")])

    # --- Entry ---
    style.configure(
        "TEntry",
        fieldbackground=COLORS["surface"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        insertcolor=COLORS["text"],
        padding=6,
    )

    # --- Combobox ---
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["surface"],
        foreground=COLORS["text"],
        padding=6,
    )

    # --- Scrollbar ---
    style.configure("TScrollbar", background=COLORS["border"], troughcolor=COLORS["bg"],
                    arrowcolor=COLORS["secondary"])

    # --- Separator ---
    style.configure("TSeparator", background=COLORS["border"])

    # --- Checkbutton ---
    style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])

    # --- Progressbar ---
    style.configure("TProgressbar", troughcolor=COLORS["border"],
                    background=COLORS["primary"], thickness=6)

    return style


def status_color(status: str) -> str:
    """Return a hex color string that represents the given sync status."""
    mapping = {
        "syncing": COLORS["primary"],
        "idle": COLORS["success"],
        "paused": COLORS["warning"],
        "error": COLORS["error"],
    }
    return mapping.get(status, COLORS["text_light"])


def status_label(status: str) -> str:
    """Return a human-readable string for the given sync status."""
    mapping = {
        "syncing": "⟳ Sincronizando...",
        "idle": "✔ Actualizado",
        "paused": "⏸ Pausado",
        "error": "✗ Error al sincronizar",
    }
    return mapping.get(status, status.capitalize())


def platform_display_name(platform_type: str) -> str:
    """Return a human-readable platform name for a given rclone type string."""
    from src.core.rclone_manager import SUPPORTED_PLATFORMS
    return SUPPORTED_PLATFORMS.get(platform_type, platform_type.capitalize())


def make_scrollable_frame(parent):
    """
    Create a scrollable frame inside parent.
    Returns (outer_frame, inner_frame) where widgets should be added to inner_frame.
    """
    outer = ttk.Frame(parent)
    canvas = tk.Canvas(outer, bg=COLORS["bg"], highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)

    inner.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # Enable mouse wheel scrolling
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    return outer, inner


def bind_mousewheel(widget, canvas):
    """Bind mouse wheel events on a widget to scroll a canvas."""
    def _scroll(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    widget.bind("<MouseWheel>", _scroll)
    widget.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
    widget.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
