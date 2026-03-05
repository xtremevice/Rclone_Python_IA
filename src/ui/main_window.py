"""
main_window.py
--------------
Primary application window.

Layout (top → bottom):
  • Notebook tabs – one per service, labelled with service name.
  • Info strip – shows service name, sync status, interval, platform.
  • Change log Treeview – last 50 synced files (100% wide, 60% of window height).
  • Bottom button bar – Open Folder | Pause/Resume | Settings (5% of window height).

Window behaviour:
  • Size: 60% screen height × 20% screen width (sidebar-style).
  • No Maximize button.
  • Minimize → system tray.
  • System tray icon → click to restore.
"""

import os
import platform as _platform
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from src.core.service_manager import ServiceManager, SyncStatus
from src.ui.utils import (
    COLORS,
    apply_theme,
    center_window,
    platform_display_name,
    set_window_size_percent,
    status_color,
    status_label,
)


def _open_folder(path: str):
    """Open the given folder path in the native file manager."""
    system = _platform.system()
    try:
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{exc}")


class MainWindow(tk.Tk):
    """
    Root application window.
    Manages notebook tabs for each service, live status labels, change log,
    and the system tray icon.
    """

    def __init__(self, service_manager: ServiceManager):
        """
        Initialise the main window.

        Parameters
        ----------
        service_manager : Shared ServiceManager instance
        """
        super().__init__()
        self.service_manager = service_manager
        # Currently viewed service id (follows active notebook tab)
        self._active_service_id: Optional[str] = None
        # Mapping: notebook tab index → service id
        self._tab_service_map: dict[int, str] = {}
        # Per-service StringVars for status labels
        self._status_vars: dict[str, tk.StringVar] = {}
        self._status_color_vars: dict[str, str] = {}
        # System tray icon object (created lazily)
        self._tray_icon = None
        self._tray_thread = None

        self._setup_window()
        apply_theme(self)
        self._build_ui()
        self._populate_tabs()
        # Register for status-change callbacks from the service manager
        self.service_manager.register_status_callback(self._on_service_status_changed)
        # Start the UI refresh loop (every 2 seconds)
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        """Configure window title, size, and icon; remove Maximize button."""
        self.title("Rclone Manager")
        # 60% height × 20% width
        set_window_size_percent(self, w_pct=0.20, h_pct=0.60)
        self.resizable(False, False)
        # Override close button to minimise to tray instead of quitting
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        # Remove maximise button (platform-specific)
        system = _platform.system()
        if system == "Windows":
            # Disable the maximize box on Windows
            self.attributes("-toolwindow", False)
            self.resizable(False, False)
        # Set application icon
        self._set_app_icon()

    def _set_app_icon(self):
        """Load and apply the application icon from assets, or generate one."""
        try:
            from pathlib import Path
            import importlib.resources
            # Try loading from assets directory
            icon_path = Path(__file__).parent.parent.parent / "assets" / "icon.png"
            if icon_path.exists():
                img = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, img)
                self._icon_image = img   # Keep reference to prevent GC
        except Exception:
            pass  # Silently ignore icon errors

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Construct the main window layout."""
        # ---- Header bar ----
        header = ttk.Frame(self, style="Surface.TFrame")
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Rclone Manager",
            style="Title.TLabel",
            background=COLORS["surface"],
        ).pack(side="left", padx=12, pady=8)
        # Add service button
        ttk.Button(
            header,
            text="＋",
            style="Primary.TButton",
            command=self._open_add_service_wizard,
            width=3,
        ).pack(side="right", padx=8, pady=6)

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ---- Notebook (tabs per service) ----
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="x")
        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- Info strip (below tabs) ----
        self._info_frame = ttk.Frame(self, style="Surface.TFrame")
        self._info_frame.pack(fill="x", padx=0)

        self._lbl_name = ttk.Label(
            self._info_frame,
            text="",
            background=COLORS["surface"],
            font=("Segoe UI", 10, "bold") if _platform.system() == "Windows" else ("Helvetica", 10, "bold"),
            foreground=COLORS["text"],
        )
        self._lbl_name.pack(anchor="w", padx=10, pady=(8, 2))

        self._lbl_status = ttk.Label(
            self._info_frame,
            text="",
            background=COLORS["surface"],
            foreground=COLORS["text_light"],
        )
        self._lbl_status.pack(anchor="w", padx=10, pady=1)

        self._lbl_interval = ttk.Label(
            self._info_frame,
            text="",
            background=COLORS["surface"],
            foreground=COLORS["text_light"],
            style="Status.TLabel",
        )
        self._lbl_interval.pack(anchor="w", padx=10, pady=1)

        self._lbl_platform = ttk.Label(
            self._info_frame,
            text="",
            background=COLORS["surface"],
            foreground=COLORS["text_light"],
            style="Status.TLabel",
        )
        self._lbl_platform.pack(anchor="w", padx=10, pady=(1, 8))

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ---- Change log Treeview ----
        # Fills 100% width and ~60% of the window height
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True)

        # Column headers
        self._log_tree = ttk.Treeview(
            log_frame,
            columns=("file", "status"),
            show="headings",
            selectmode="browse",
        )
        self._log_tree.heading("file", text="Archivo")
        self._log_tree.heading("status", text="Estado")
        self._log_tree.column("file", width=220, anchor="w", stretch=True)
        self._log_tree.column("status", width=90, anchor="center", stretch=False)

        # Alternating row colours
        self._log_tree.tag_configure("odd", background=COLORS["list_odd"])
        self._log_tree.tag_configure("even", background=COLORS["list_even"])

        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._log_tree.yview)
        self._log_tree.configure(yscrollcommand=log_scroll.set)
        self._log_tree.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ---- Bottom button bar ----
        btn_bar = ttk.Frame(self, style="Surface.TFrame")
        btn_bar.pack(fill="x", side="bottom")

        # Button 1: Open folder
        self._btn_open = ttk.Button(
            btn_bar,
            text="📂",
            style="Secondary.TButton",
            command=self._on_open_folder,
        )
        self._btn_open.pack(side="left", expand=True, fill="x", padx=(4, 2), pady=4)

        # Button 2: Pause / Resume
        self._btn_pause = ttk.Button(
            btn_bar,
            text="⏸",
            style="Secondary.TButton",
            command=self._on_pause_resume,
        )
        self._btn_pause.pack(side="left", expand=True, fill="x", padx=2, pady=4)

        # Button 3: Settings
        self._btn_settings = ttk.Button(
            btn_bar,
            text="⚙",
            style="Secondary.TButton",
            command=self._on_open_settings,
        )
        self._btn_settings.pack(side="left", expand=True, fill="x", padx=(2, 4), pady=4)

    # ------------------------------------------------------------------
    # Tab population & management
    # ------------------------------------------------------------------

    def _populate_tabs(self):
        """Create one notebook tab for every configured service."""
        # Remove existing tabs
        for tab in self._notebook.tabs():
            self._notebook.forget(tab)
        self._tab_service_map.clear()

        services = self.service_manager.get_services()
        for idx, svc in enumerate(services):
            # Each tab is an empty frame; content is shown in the info strip below
            tab_frame = ttk.Frame(self._notebook)
            self._notebook.add(tab_frame, text=f"  {svc['name']}  ")
            self._tab_service_map[idx] = svc["id"]

        if services:
            self._active_service_id = self._tab_service_map.get(0)
            self._refresh_info_panel()
        else:
            self._clear_info_panel()

    def _on_tab_changed(self, event):
        """Update active service when the user clicks a different tab."""
        idx = self._notebook.index("current")
        self._active_service_id = self._tab_service_map.get(idx)
        self._refresh_info_panel()

    # ------------------------------------------------------------------
    # Info panel helpers
    # ------------------------------------------------------------------

    def _refresh_info_panel(self):
        """Update status labels and change log for the active service."""
        if not self._active_service_id:
            self._clear_info_panel()
            return
        svc = self.service_manager.config.get_service(self._active_service_id)
        if not svc:
            self._clear_info_panel()
            return

        status = self.service_manager.get_status(self._active_service_id)
        last_sync = self.service_manager.get_last_sync_time(self._active_service_id)

        # Service name
        self._lbl_name.config(text=svc.get("name", "Sin nombre"))
        # Sync status with colour
        s_text = status_label(status)
        s_color = status_color(status)
        self._lbl_status.config(text=s_text, foreground=s_color)
        # Sync interval
        interval = svc.get("sync_interval", 15)
        self._lbl_interval.config(text=f"🔄 Cada {interval} min")
        # Platform
        self._lbl_platform.config(text=f"☁  {platform_display_name(svc.get('platform', ''))}")

        # Update pause/resume button label
        if status == SyncStatus.PAUSED or svc.get("sync_paused", False):
            self._btn_pause.config(text="▶")
        else:
            self._btn_pause.config(text="⏸")

        # Populate the change log Treeview
        self._refresh_change_log()

    def _clear_info_panel(self):
        """Reset all info labels when no service is selected."""
        self._lbl_name.config(text="Sin servicios configurados")
        self._lbl_status.config(text="", foreground=COLORS["text_light"])
        self._lbl_interval.config(text="")
        self._lbl_platform.config(text="")
        self._log_tree.delete(*self._log_tree.get_children())

    def _refresh_change_log(self):
        """Populate the Treeview with the last 50 changed files."""
        if not self._active_service_id:
            return
        files = self.service_manager.get_changed_files(self._active_service_id)
        # Clear existing rows
        self._log_tree.delete(*self._log_tree.get_children())
        for i, f in enumerate(files):
            tag = "odd" if i % 2 else "even"
            self._log_tree.insert(
                "",
                "end",
                values=(f.get("path", ""), f.get("status", "")),
                tags=(tag,),
            )

    # ------------------------------------------------------------------
    # Status change callback (from service manager background thread)
    # ------------------------------------------------------------------

    def _on_service_status_changed(self, service_id: str):
        """
        Called by the service manager when a service's status changes.
        Schedules a UI refresh on the main thread.
        """
        if service_id == self._active_service_id:
            self.after(0, self._refresh_info_panel)

    # ------------------------------------------------------------------
    # Periodic refresh loop
    # ------------------------------------------------------------------

    def _schedule_refresh(self):
        """Refresh the info panel every 2 seconds to keep status up to date."""
        self._refresh_info_panel()
        self.after(2000, self._schedule_refresh)

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def _on_open_folder(self):
        """Open the local sync folder for the active service in the file manager."""
        if not self._active_service_id:
            return
        svc = self.service_manager.config.get_service(self._active_service_id)
        if svc:
            _open_folder(svc.get("local_path", str(os.path.expanduser("~"))))

    def _on_pause_resume(self):
        """Toggle pause/resume for the active service."""
        if not self._active_service_id:
            return
        status = self.service_manager.get_status(self._active_service_id)
        svc = self.service_manager.config.get_service(self._active_service_id)
        if not svc:
            return
        if svc.get("sync_paused", False) or status == SyncStatus.PAUSED:
            self.service_manager.resume_service(self._active_service_id)
        else:
            self.service_manager.pause_service(self._active_service_id)
        self._refresh_info_panel()

    def _on_open_settings(self):
        """Open the configuration window for the active service."""
        if not self._active_service_id:
            messagebox.showinfo(
                "Sin servicio",
                "Agrega un servicio primero antes de abrir la configuración.",
                parent=self,
            )
            return
        from src.ui.config_window import ConfigWindow
        ConfigWindow(self, self.service_manager, self._active_service_id)

    def _open_add_service_wizard(self):
        """Open the new-service wizard and, on completion, add the service."""
        from src.ui.setup_wizard import SetupWizard

        def _on_wizard_complete(name, plat, local_path, remote_name, token):
            """Callback invoked by the wizard when the user finishes setup."""
            self.service_manager.add_service(name, plat, local_path, remote_name)
            self._populate_tabs()
            # Select the newly added tab (last one)
            count = len(self._notebook.tabs())
            if count > 0:
                self._notebook.select(count - 1)

        SetupWizard(self, on_complete=_on_wizard_complete)

    # ------------------------------------------------------------------
    # Window minimise / system tray
    # ------------------------------------------------------------------

    def _on_window_close(self):
        """
        Override the window close (X) button to minimise to tray.
        The application only truly quits when the tray icon menu is used.
        """
        self.withdraw()
        self._ensure_tray_icon()

    def _on_iconify(self, event=None):
        """Catch the minimise event and redirect to tray."""
        if self.state() == "iconic":
            self.withdraw()
            self._ensure_tray_icon()

    def _ensure_tray_icon(self):
        """Create the system tray icon if it does not exist yet."""
        if self._tray_icon is not None:
            return
        try:
            import pystray
            from PIL import Image, ImageDraw

            # Create a simple coloured circle icon (used when no icon file exists)
            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([4, 4, size - 4, size - 4], fill="#2563EB")
            draw.text((size // 2 - 5, size // 2 - 8), "R", fill="white")

            # Try loading the actual icon file
            from pathlib import Path
            icon_path = Path(__file__).parent.parent.parent / "assets" / "icon.png"
            if icon_path.exists():
                img = Image.open(icon_path).convert("RGBA").resize((64, 64))

            menu = pystray.Menu(
                pystray.MenuItem("Abrir Rclone Manager", self._restore_from_tray, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self._quit_from_tray),
            )
            self._tray_icon = pystray.Icon(
                "rclone_manager",
                img,
                "Rclone Manager",
                menu,
            )
            # Run tray icon in its own daemon thread
            self._tray_thread = threading.Thread(
                target=self._tray_icon.run, daemon=True
            )
            self._tray_thread.start()
        except ImportError:
            # pystray/Pillow not installed – just minimise normally
            self.iconify()
        except Exception:
            self.iconify()

    def _restore_from_tray(self, icon=None, item=None):
        """Restore the main window from the system tray (runs on main thread via after)."""
        self.after(0, self._do_restore)

    def _do_restore(self):
        """Deiconify and bring the window to the foreground."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self, icon=None, item=None):
        """Quit the application from the system tray menu."""
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.quit)

    # ------------------------------------------------------------------
    # Public refresh API (called after config changes)
    # ------------------------------------------------------------------

    def refresh_tabs(self):
        """Re-populate all tabs (e.g. after a service is added or removed)."""
        self._populate_tabs()
