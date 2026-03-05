"""
Main application window.

Displays one tab per configured service showing sync status, interval,
platform, a scrollable list of the last 50 changed files, and three action
buttons.

Window properties:
- Size: 60 % of screen height × 20 % of screen width (portrait strip)
- No Maximise button
- Minimising sends the window to the notification tray instead of the taskbar
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from app.config import AVAILABLE_SERVICES, SYNC_INTERVALS, AppConfig
from app.sync_manager import SyncManager
from app.utils import center_window, open_folder


# Milliseconds between UI refresh cycles
_REFRESH_INTERVAL_MS = 3_000


class MainWindow(tk.Tk):
    """Root application window with per-service tabs."""

    def __init__(
        self,
        app_config: AppConfig,
        sync_manager: SyncManager,
        on_add_service: Optional[Callable[[], None]] = None,
        on_open_config: Optional[Callable[[str], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Build the main window.

        Args:
            app_config: Shared application configuration.
            sync_manager: Running sync manager.
            on_add_service: Callback invoked when user requests a new service.
            on_open_config: Callback(service_name) for configuration window.
            on_quit: Callback invoked when the window is fully closed.
        """
        super().__init__()
        self.app_config = app_config
        self.sync_manager = sync_manager
        self._on_add_service = on_add_service
        self._on_open_config = on_open_config
        self._on_quit = on_quit

        # Window chrome
        self.title("Rclone Manager")
        self.resizable(False, False)
        # Remove maximise button (platform-dependent workarounds)
        self._remove_maximize_button()

        # Size: 20 % wide × 60 % tall
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = int(sw * 0.20)
        h = int(sh * 0.60)
        center_window(self, w, h)
        self._win_w = w
        self._win_h = h

        # Intercept close (X) button → only truly quit
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._start_refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct all widgets."""
        # ---- Notebook (tabs for each service) ----
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        # Populate tabs
        self._tab_frames: Dict[str, "_ServiceTab"] = {}
        self._rebuild_tabs()

        # ---- Bottom button bar (5 % of window height) ----
        btn_h = max(30, int(self._win_h * 0.05))
        btn_bar = tk.Frame(self, height=btn_h, bg="#e0e0e0")
        btn_bar.pack(fill=tk.X, side=tk.BOTTOM)
        btn_bar.pack_propagate(False)

        btn_opts = dict(bd=0, relief=tk.FLAT, bg="#e0e0e0", fg="#333", padx=4)

        self._btn_open = tk.Button(
            btn_bar,
            text="📂 Abrir",
            command=self._open_folder,
            **btn_opts,
        )
        self._btn_open.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Frame(btn_bar, width=1, bg="#bbb").pack(side=tk.LEFT, fill=tk.Y)

        self._btn_toggle = tk.Button(
            btn_bar,
            text="⏸ Pausar",
            command=self._toggle_sync,
            **btn_opts,
        )
        self._btn_toggle.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Frame(btn_bar, width=1, bg="#bbb").pack(side=tk.LEFT, fill=tk.Y)

        self._btn_config = tk.Button(
            btn_bar,
            text="⚙ Config",
            command=self._open_config,
            **btn_opts,
        )
        self._btn_config.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # If there are no services, show an "Add Service" button
        if not self.app_config.services:
            self._show_empty_state()

    def _show_empty_state(self) -> None:
        """Replace the notebook with an 'Add your first service' prompt."""
        for widget in self._notebook.winfo_children():
            widget.destroy()
        empty_frame = tk.Frame(self._notebook, bg="#f5f5f5")
        self._notebook.add(empty_frame, text="Sin servicios")

        tk.Label(
            empty_frame,
            text="No hay servicios\nconfigurados.",
            bg="#f5f5f5",
            justify=tk.CENTER,
        ).pack(expand=True, pady=(20, 8))

        tk.Button(
            empty_frame,
            text="+ Agregar Servicio",
            command=self._on_add_service,
        ).pack(pady=4)

    def _rebuild_tabs(self) -> None:
        """Destroy and recreate all notebook tabs from current config."""
        # Remove stale tabs
        for tab_id in self._notebook.tabs():
            self._notebook.forget(tab_id)
        self._tab_frames.clear()

        for svc in self.app_config.services:
            tab = _ServiceTab(self._notebook, svc, self.sync_manager)
            self._notebook.add(tab, text=svc.get("display_name", svc["name"]))
            self._tab_frames[svc["name"]] = tab

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    def _start_refresh(self) -> None:
        """Begin the periodic UI refresh cycle."""
        self._refresh()

    def _refresh(self) -> None:
        """Update every visible tab and reschedule."""
        for name, tab in self._tab_frames.items():
            svc = self.app_config.get_service(name)
            if svc:
                tab.refresh(svc)
        self._update_toggle_label()
        self.after(_REFRESH_INTERVAL_MS, self._refresh)

    def _update_toggle_label(self) -> None:
        """Update the Pause/Resume button label based on current service."""
        name = self._current_service_name()
        if not name:
            return
        status = self.sync_manager.get_status(name)
        if status == "paused":
            self._btn_toggle.config(text="▶ Reanudar")
        else:
            self._btn_toggle.config(text="⏸ Pausar")

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _current_service_name(self) -> Optional[str]:
        """Return the name of the service shown in the active tab."""
        idx = self._notebook.index("current") if self._notebook.tabs() else None
        if idx is None:
            return None
        svc_list = self.app_config.services
        if idx < len(svc_list):
            return svc_list[idx]["name"]
        return None

    def _open_folder(self) -> None:
        """Open the active service's local sync folder."""
        name = self._current_service_name()
        if not name:
            return
        svc = self.app_config.get_service(name)
        if svc:
            open_folder(svc["local_path"])

    def _toggle_sync(self) -> None:
        """Pause or resume the active service's sync."""
        name = self._current_service_name()
        if not name:
            return
        svc = self.app_config.get_service(name)
        if not svc:
            return
        status = self.sync_manager.get_status(name)
        if status == "paused":
            self.sync_manager.resume_service(svc)
            self._btn_toggle.config(text="⏸ Pausar")
        else:
            self.sync_manager.stop_service(name)
            self._btn_toggle.config(text="▶ Reanudar")

    def _open_config(self) -> None:
        """Open the configuration window for the active service."""
        name = self._current_service_name()
        if not name and self.app_config.services:
            name = self.app_config.services[0]["name"]
        if name and self._on_open_config:
            self._on_open_config(name)

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _remove_maximize_button(self) -> None:
        """
        Hide the maximise button on supported platforms.

        On Windows this is done via the window attributes; on macOS via
        'zoomed' state; on Linux it varies by window manager.
        """
        import sys
        if sys.platform == "win32":
            self.resizable(False, False)
        elif sys.platform == "darwin":
            # Remove the zoom (maximise) button on macOS
            try:
                self.tk.call(
                    "::tk::unsupported::MacWindowStyle",
                    "style", self._w, "document", "closeBox collapseBox"
                )
            except tk.TclError:
                pass
        # On Linux resizable(False, False) is sufficient

    def minimize_to_tray(self) -> None:
        """Withdraw the window instead of iconifying it (sends to tray)."""
        self.withdraw()

    def restore_from_tray(self) -> None:
        """Bring the window back from the tray."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close(self) -> None:
        """Handle window close (X button) – fully quit the application."""
        if messagebox.askyesno(
            "Salir",
            "¿Desea cerrar Rclone Manager?\nLas sincronizaciones se detendrán.",
            parent=self,
        ):
            self.sync_manager.stop_all()
            self.destroy()
            if self._on_quit:
                self._on_quit()

    # ------------------------------------------------------------------
    # Public helpers called by the Application controller
    # ------------------------------------------------------------------

    def add_service_tab(self, service: Dict) -> None:
        """Add a tab for *service* and begin syncing it."""
        tab = _ServiceTab(self._notebook, service, self.sync_manager)
        self._notebook.add(tab, text=service.get("display_name", service["name"]))
        self._tab_frames[service["name"]] = tab
        # Remove the "no services" placeholder if present
        if len(self.app_config.services) == 1:
            self._rebuild_tabs()

    def refresh_service_tab(self, name: str) -> None:
        """Force a refresh for the tab of *name*."""
        svc = self.app_config.get_service(name)
        tab = self._tab_frames.get(name)
        if svc and tab:
            tab.refresh(svc)


# ---------------------------------------------------------------------------
# Per-service tab widget
# ---------------------------------------------------------------------------


class _ServiceTab(tk.Frame):
    """
    Content shown inside a single notebook tab for one service.

    Layout (top → bottom):
      - Info strip: name, status, interval, platform
      - File-change list (100 % wide, 60 % of available vertical space)
    """

    def __init__(
        self,
        parent: ttk.Notebook,
        service: Dict,
        sync_manager: SyncManager,
    ) -> None:
        """Build the tab frame for *service*."""
        super().__init__(parent, bg="#f9f9f9")
        self._svc_name = service["name"]
        self._sync_manager = sync_manager
        self._build(service)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self, service: Dict) -> None:
        """Create all widgets for this tab."""
        # ---- Info strip ----
        info = tk.Frame(self, bg="#e8f4fd", padx=8, pady=6)
        info.pack(fill=tk.X, side=tk.TOP)

        # Service name (bold)
        self._lbl_name = tk.Label(
            info,
            text=service.get("display_name", service["name"]),
            bg="#e8f4fd",
            font=("TkDefaultFont", 10, "bold"),
        )
        self._lbl_name.grid(row=0, column=0, columnspan=2, sticky=tk.W)

        # Status
        tk.Label(info, text="Estado:", bg="#e8f4fd", font=("TkDefaultFont", 8)).grid(
            row=1, column=0, sticky=tk.W
        )
        self._lbl_status = tk.Label(
            info, text="—", bg="#e8f4fd", fg="#007bff", font=("TkDefaultFont", 8)
        )
        self._lbl_status.grid(row=1, column=1, sticky=tk.W, padx=(4, 12))

        # Interval
        tk.Label(info, text="Intervalo:", bg="#e8f4fd", font=("TkDefaultFont", 8)).grid(
            row=2, column=0, sticky=tk.W
        )
        self._lbl_interval = tk.Label(
            info, text="—", bg="#e8f4fd", font=("TkDefaultFont", 8)
        )
        self._lbl_interval.grid(row=2, column=1, sticky=tk.W, padx=(4, 12))

        # Platform
        tk.Label(info, text="Plataforma:", bg="#e8f4fd", font=("TkDefaultFont", 8)).grid(
            row=3, column=0, sticky=tk.W
        )
        self._lbl_platform = tk.Label(
            info, text="—", bg="#e8f4fd", font=("TkDefaultFont", 8)
        )
        self._lbl_platform.grid(row=3, column=1, sticky=tk.W, padx=(4, 12))

        # ---- File-change list (60 % vertical, 100 % horizontal) ----
        list_frame = tk.Frame(self, bg="#f9f9f9")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        tk.Label(
            list_frame,
            text="Últimos 50 archivos modificados",
            bg="#f9f9f9",
            font=("TkDefaultFont", 8, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X)

        # Treeview with two columns: filename and timestamp
        cols = ("archivo", "modificado")
        self._tree = ttk.Treeview(
            list_frame,
            columns=cols,
            show="headings",
            selectmode="browse",
        )
        self._tree.heading("archivo", text="Archivo")
        self._tree.heading("modificado", text="Modificado")
        self._tree.column("archivo", stretch=True, width=180)
        self._tree.column("modificado", width=110, stretch=False)

        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Initial population
        self.refresh(service)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self, service: Dict) -> None:
        """Update all dynamic labels and the file-change list."""
        # Status
        status = self._sync_manager.get_status(service["name"])
        status_text, color = _status_display(status)
        self._lbl_status.config(text=status_text, fg=color)

        # Sync interval
        minutes = service.get("sync_interval", 15)
        label = _minutes_to_label(minutes)
        self._lbl_interval.config(text=label)

        # Platform
        stype = service.get("service_type", "")
        platform = AVAILABLE_SERVICES.get(stype, stype)
        self._lbl_platform.config(text=platform)

        # File-change list
        changes = self._sync_manager.get_recent_changes(service["name"])
        # Rebuild tree rows
        self._tree.delete(*self._tree.get_children())
        for fname, ts in changes:
            self._tree.insert("", tk.END, values=(fname, ts))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_display(status: str):
    """Return (display_text, colour) for a status string."""
    mapping = {
        "idle": ("Actualizado ✓", "#28a745"),
        "syncing": ("Sincronizando…", "#007bff"),
        "error": ("Error ✗", "#dc3545"),
        "paused": ("Pausado ⏸", "#6c757d"),
    }
    return mapping.get(status, (status, "#333"))


def _minutes_to_label(minutes: int) -> str:
    """Convert an integer number of minutes to a human-readable label."""
    for label, mins in SYNC_INTERVALS.items():
        if mins == minutes:
            return label
    if minutes < 60:
        return f"Cada {minutes} minutos"
    return f"Cada {minutes // 60} hora(s)"
