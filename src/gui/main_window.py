"""
Main application window.

Displays one tab per configured service.  Each tab shows:
  - Service name, sync status, interval, and platform (header row)
  - A scrollable list of the last 50 synced/changed files (60 % of height)
  - Three action buttons at the bottom (open folder / pause-resume / configure)

Additional behaviours:
  - Minimize → sent to system tray (window hidden).
  - No maximize button.
  - Close button exits the application.
  - Window size: 60 % screen height × 20 % screen width.
"""

import json
import os
import platform
import re
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from src.config.config_manager import (
    PLATFORM_LABELS,
    TREE_FILE_THRESHOLD,
    ConfigManager,
    get_config_dir,
)
from src.db.file_scan_db import FileScanDB
from src.gui.tray_icon import TrayIcon
from src.gui.elementary_indicator import ElementaryIndicator, is_elementary_os
from src.gui.error_logger import ErrorLogger
from src.rclone.rclone_manager import RcloneManager, _build_mtime_comparison, _scan_local_mtimes

# Status string emitted by RcloneManager when no sync is running
_STATUS_STOPPED = "Detenido"

# ── Tree color scheme ────────────────────────────────────────────────────────
# Colors used for the sync-status file tree.  Items are colored by the side
# on which they exist, matching the legend shown in the status column:
#   🔵 local_only  – exists only on the local disk
#   🟠 remote_only – exists only on the remote (web)
#   🟢 synced/both – present on both sides
#   ⚠️ diff        – on both sides but contents differ
_TREE_COLOR_LOCAL_ONLY  = "#0078d4"   # blue
_TREE_COLOR_REMOTE_ONLY = "#cc6600"   # orange
_TREE_COLOR_SYNCED      = "#007700"   # green
_TREE_COLOR_DIFF        = "#cc6600"   # orange (same as remote_only — warning shade)
_TREE_COLOR_UNKNOWN     = "#888888"   # gray


def _center_window(window: tk.Wm, height_pct: float, width_pct: float) -> None:
    """Resize and center a Tk / Toplevel window on screen."""
    window.update_idletasks()
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    ww = int(sw * width_pct)
    wh = int(sh * height_pct)
    x = (sw - ww) // 2
    y = (sh - wh) // 2
    window.geometry(f"{ww}x{wh}+{x}+{y}")


class MainWindow:
    """
    The primary UI window that shows all services as tabs.

    Minimizing the window hides it and starts the system-tray icon if
    available; clicking the tray icon restores it.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        rclone_manager: RcloneManager,
    ) -> None:
        self._config = config_manager
        self._rclone = rclone_manager

        # Application-wide error logger (loads previous session from disk)
        self._error_logger = ErrorLogger()

        # Encrypted SQLite database for per-service file scan metadata.
        # Created once and shared across all service tree-check threads.
        self._db = FileScanDB()
        # Log the DB path on startup so developers and testers can find it.
        self._error_logger.log(
            "Sistema",
            f"📂 Base de datos: {self._db.db_path}",
        )
        self._error_logger.log(
            "Sistema",
            f"🔑 Clave de cifrado: {self._db.key_path}",
        )

        # Root Tk window
        self._root = tk.Tk()
        self._root.title("Rclone Manager")
        self._root.resizable(True, True)

        # Remove maximize button on supported platforms
        _remove_maximize_button(self._root)

        _center_window(self._root, height_pct=0.60, width_pct=0.55)

        # On Elementary OS, use a Wingpanel indicator (AppIndicator3) that is
        # always visible while the app is running.  For all other systems, fall
        # back to the pystray-based tray icon that appears only on minimise.
        if is_elementary_os():
            self._elementary = ElementaryIndicator(
                on_show=self._restore_window,
                on_quit=self._quit,
            )
            # Start immediately so the icon appears in Wingpanel right away.
            if self._elementary.is_available():
                self._elementary.start()
        else:
            self._elementary = None

        # pystray tray icon — used on non-Elementary OS systems only.
        self._tray = TrayIcon(on_show=self._restore_window, on_quit=self._quit)

        # Intercept window close (×) to quit the app entirely
        self._root.protocol("WM_DELETE_WINDOW", self._quit)

        # Intercept minimize to send to tray
        self._root.bind("<Unmap>", self._on_minimize)

        # Register rclone callbacks
        self._rclone.on_status_change = self._on_status_change
        self._rclone.on_file_synced = self._on_file_synced
        self._rclone.on_error = self._on_rclone_error
        self._rclone.on_drive_id_error = self._on_drive_id_error
        self._rclone.on_api_call = self._on_native_api_call

        # Per-service Listbox widgets: service_name → tk.Listbox
        self._file_lists: Dict[str, tk.Listbox] = {}
        # Per-service status StringVars
        self._status_vars: Dict[str, tk.StringVar] = {}
        # Per-service toggle-button StringVars (Detener / Sincronizar)
        self._toggle_vars: Dict[str, tk.StringVar] = {}
        # Per-service storage info StringVars (from rclone about)
        self._storage_vars: Dict[str, tk.StringVar] = {}
        # Per-service drive_id error banner frames (shown when bisync detects
        # a missing drive_id/drive_type in rclone.conf)
        self._drive_id_banners: Dict[str, tk.Frame] = {}
        # Per-service Treeview widgets for the sync-file tree (right panel)
        self._file_trees: Dict[str, ttk.Treeview] = {}
        # Per-service tkinter after() IDs for the scheduled sync-tree auto-refresh
        self._tree_refresh_ids: Dict[str, Optional[str]] = {}
        # Generation counter per service used to cancel stale background checks.
        # Each call to _start_tree_check() increments the counter; background
        # threads discard their results when the counter has moved on.
        self._tree_check_generations: Dict[str, int] = {}
        # Per-service StringVars showing when the last file-tree scan started.
        self._tree_scan_started_vars: Dict[str, tk.StringVar] = {}
        # Per-service StringVars showing when the next scheduled scan will run.
        self._tree_scan_next_vars: Dict[str, tk.StringVar] = {}
        # Per-service (local, remote, merger) scan-thread status StringVars.
        self._tree_thread_status_vars: Dict[str, Tuple[tk.StringVar, tk.StringVar, tk.StringVar]] = {}
        # Per-service StringVar showing total scan elapsed time.
        self._tree_scan_elapsed_vars: Dict[str, tk.StringVar] = {}
        # Per-service monotonic start time used to compute elapsed scan time.
        self._tree_scan_wall_times: Dict[str, float] = {}
        # Whether the pystray tray icon has been started (non-Elementary only)
        self._tray_started = False

        self._notebook: Optional[ttk.Notebook] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the notebook and populate one tab per service."""
        services = self._config.get_services()

        if not services:
            self._show_empty_state()
            return

        # Notebook (tabs at top)
        self._notebook = ttk.Notebook(self._root)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        for svc in services:
            self._add_service_tab(svc)

    def _show_empty_state(self) -> None:
        """Display a message when no services are configured."""
        frame = tk.Frame(self._root)
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        tk.Label(
            frame,
            text="No hay servicios configurados.",
            font=("Segoe UI", 12),
        ).pack(expand=True)

        btn_row = tk.Frame(frame)
        btn_row.pack(pady=(6, 0))

        tk.Button(
            btn_row,
            text="➕ Agregar primer servicio",
            command=self._open_wizard,
            bg="#0078d4",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=6,
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_row,
            text="📥 Importar configuración",
            command=self._open_import_dialog,
            bg="#5c2d91",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=6,
        ).pack(side=tk.LEFT)

    def _add_service_tab(self, svc: Dict) -> None:
        """Build and add a tab for the given service dictionary."""
        name = svc.get("name", "Sin nombre")
        platform_key = svc.get("platform", "")
        platform_label = PLATFORM_LABELS.get(platform_key, platform_key)
        interval_secs = svc.get("sync_interval", 900)
        interval_label = _seconds_to_label(interval_secs)

        tab_frame = tk.Frame(self._notebook)
        self._notebook.add(tab_frame, text=f"  {name}  ")

        # ── Header row ────────────────────────────────────────────────
        header = tk.Frame(tab_frame, bg="#f0f4fa", pady=8, padx=10)
        header.pack(fill=tk.X)
        # Allow the status/name column to stretch when the window is resized
        # while keeping the timestamp columns right-anchored without growing.
        header.columnconfigure(0, weight=1)

        # Row 0: Service name | Platform | Sync status | Interval | Add button
        tk.Label(header, text=name, font=("Segoe UI", 11, "bold"), bg="#f0f4fa").grid(row=0, column=0, sticky="w", padx=(0, 20))

        tk.Label(header, text=f"Plataforma: {platform_label}", bg="#f0f4fa").grid(row=0, column=1, sticky="w", padx=(0, 20))

        # Sync status (dynamic)
        status_var = tk.StringVar(value=self._rclone.get_status(name))
        self._status_vars[name] = status_var
        tk.Label(header, textvariable=status_var, bg="#f0f4fa", fg="#0078d4", font=("Segoe UI", 9, "italic")).grid(row=0, column=2, sticky="w", padx=(0, 20))

        # Sync interval
        tk.Label(header, text=f"Sincroniza cada: {interval_label}", bg="#f0f4fa").grid(row=0, column=3, sticky="w")

        # "Add new service" shortcut button (next to the interval label)
        tk.Button(
            header,
            text="➕",
            command=self._open_wizard,
            relief=tk.FLAT,
            bg="#f0f4fa",
            font=("Segoe UI", 9),
            cursor="hand2",
        ).grid(row=0, column=4, sticky="w", padx=(8, 0))

        # "Import rclone config" shortcut button (next to "➕")
        tk.Button(
            header,
            text="📥 Importar configuración",
            command=self._open_import_dialog,
            relief=tk.FLAT,
            bg="#f0f4fa",
            font=("Segoe UI", 9),
            cursor="hand2",
        ).grid(row=0, column=5, sticky="w", padx=(4, 0))

        # "Refresh sync tree" shortcut button (next to import button)
        tk.Button(
            header,
            text="🔄 Actualizar",
            command=lambda n=name: self._refresh_sync_tree(n),
            relief=tk.FLAT,
            bg="#f0f4fa",
            font=("Segoe UI", 9),
            cursor="hand2",
        ).grid(row=0, column=6, sticky="w", padx=(4, 0))

        # Row 1: Storage quota info (fetched asynchronously via rclone about)
        storage_var = tk.StringVar(value="💾 Total: 0  |  Usado: 0  |  Libre: 0")
        self._storage_vars[name] = storage_var
        tk.Label(
            header,
            textvariable=storage_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))

        # "Last scan started" label
        scan_started_var = tk.StringVar(value=f"{_SCAN_STARTED_PREFIX}—")
        self._tree_scan_started_vars[name] = scan_started_var
        tk.Label(
            header,
            textvariable=scan_started_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=1, column=5, sticky="w", padx=(12, 0), pady=(4, 0))

        # "Next scan" label
        scan_next_var = tk.StringVar(value=f"{_SCAN_NEXT_PREFIX}—")
        self._tree_scan_next_vars[name] = scan_next_var
        tk.Label(
            header,
            textvariable=scan_next_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=1, column=6, sticky="w", padx=(12, 0), pady=(4, 0))

        # Row 2: per-thread scan status labels + total elapsed time
        local_status_var = tk.StringVar(value=f"{_SCAN_LOCAL_PREFIX}—")
        remote_status_var = tk.StringVar(value=f"{_SCAN_REMOTE_PREFIX}—")
        merger_status_var = tk.StringVar(value=f"{_SCAN_MERGER_PREFIX}—")
        elapsed_var = tk.StringVar(value=f"{_SCAN_ELAPSED_PREFIX}—")
        self._tree_thread_status_vars[name] = (local_status_var, remote_status_var, merger_status_var)
        self._tree_scan_elapsed_vars[name] = elapsed_var
        tk.Label(
            header,
            textvariable=local_status_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        tk.Label(
            header,
            textvariable=remote_status_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=(2, 0))
        tk.Label(
            header,
            textvariable=merger_status_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=2, column=4, columnspan=2, sticky="w", padx=(12, 0), pady=(2, 0))
        tk.Label(
            header,
            textvariable=elapsed_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=2, column=6, sticky="w", padx=(12, 0), pady=(2, 0))

        # Fetch storage quota in the background and update the label when ready
        self._fetch_storage_info_async(name, storage_var)

        # ── Drive-ID error banner (hidden until a drive_id error is detected) ──
        # Uses a yellow background to stand out and includes a direct button
        # to open the "Información del servicio" panel where the user can
        # run 'Reconectar' or 'Buscar drive_id' to fix the configuration.
        # Colours: #fff3cd background with #4d3800 text gives ~7.5:1 contrast
        # ratio (WCAG AA compliant for normal and large text).
        drive_id_banner = tk.Frame(tab_frame, bg="#fff3cd", bd=1, relief=tk.SOLID)
        # The banner is not packed initially — _show_drive_id_banner() will
        # pack it (before the file list) when needed.
        tk.Label(
            drive_id_banner,
            text=(
                "⚠️  Falta drive_id en la configuración del remoto.  "
                "La sincronización no puede continuar."
            ),
            bg="#fff3cd",
            fg="#4d3800",
            font=("Segoe UI", 9, "bold"),
            wraplength=400,
            justify="left",
        ).pack(side=tk.LEFT, padx=(8, 4), pady=6, fill=tk.X, expand=True)
        tk.Button(
            drive_id_banner,
            text="🔧 Reconfigurar ahora",
            command=lambda n=name: self._open_config_at_info(n),
            relief=tk.FLAT,
            bg="#e6a817",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=8,
            pady=4,
        ).pack(side=tk.RIGHT, padx=8, pady=6)
        self._drive_id_banners[name] = drive_id_banner

        # ── Content area: left=activity list | right=sync tree ────────
        content_pane = tk.PanedWindow(
            tab_frame,
            orient=tk.HORIZONTAL,
            sashrelief=tk.FLAT,
            sashwidth=4,
        )
        content_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ── Left panel: recent activity list ──────────────────────────
        left_frame = tk.Frame(content_pane)
        content_pane.add(left_frame, minsize=120)

        sb = tk.Scrollbar(left_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            left_frame,
            yscrollcommand=sb.set,
            font=("Courier", 9),
            selectmode=tk.BROWSE,
            activestyle="none",
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=listbox.yview)
        self._file_lists[name] = listbox

        # Populate with persisted history
        for entry in svc.get("sync_history", [])[:50]:
            icon = "✅" if entry.get("synced") else "⏳"
            ts = entry.get("timestamp", "")
            fp = entry.get("file", "")
            listbox.insert(tk.END, f"{icon} [{ts}]  {fp}")

        # ── Right panel: sync tree view ────────────────────────────────
        right_frame = tk.Frame(content_pane)
        content_pane.add(right_frame, minsize=150)

        tk.Label(
            right_frame,
            text="🗂 Archivos a sincronizar",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=4,
            pady=2,
        ).pack(fill=tk.X)

        tree_outer = tk.Frame(right_frame)
        tree_outer.pack(fill=tk.BOTH, expand=True)

        sb_tree_y = tk.Scrollbar(tree_outer, orient=tk.VERTICAL)
        sb_tree_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_tree_x = tk.Scrollbar(tree_outer, orient=tk.HORIZONTAL)
        sb_tree_x.pack(side=tk.BOTTOM, fill=tk.X)

        sync_tree = ttk.Treeview(
            tree_outer,
            columns=("status", "local_mtime", "remote_mtime"),
            displaycolumns=("status", "local_mtime", "remote_mtime"),
            yscrollcommand=sb_tree_y.set,
            xscrollcommand=sb_tree_x.set,
            selectmode="browse",
        )
        sync_tree.heading("#0", text="Archivo / Carpeta", anchor="w")
        sync_tree.heading("status", text="Estado", anchor="center")
        sync_tree.heading("local_mtime", text="Mod. local", anchor="center")
        sync_tree.heading("remote_mtime", text="Mod. remota", anchor="center")
        sync_tree.column("#0", stretch=True, minwidth=120)
        sync_tree.column("status", width=100, anchor="center", stretch=False)
        sync_tree.column("local_mtime", width=110, anchor="center", stretch=False)
        sync_tree.column("remote_mtime", width=110, anchor="center", stretch=False)
        sync_tree.tag_configure("synced",      foreground=_TREE_COLOR_SYNCED)
        sync_tree.tag_configure("pending",     foreground=_TREE_COLOR_DIFF)
        sync_tree.tag_configure("diff",        foreground=_TREE_COLOR_DIFF)
        # Color coding by file/folder origin:
        #   remote_only → orange  (exists only on the remote/web)
        #   local_only  → blue    (exists only on the local disk)
        #   synced/both → green   (exists on both sides)
        sync_tree.tag_configure("remote_only", foreground=_TREE_COLOR_REMOTE_ONLY)
        sync_tree.tag_configure("local_only",  foreground=_TREE_COLOR_LOCAL_ONLY)
        sync_tree.tag_configure("unknown",     foreground=_TREE_COLOR_UNKNOWN)
        sync_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_tree_y.config(command=sync_tree.yview)
        sb_tree_x.config(command=sync_tree.xview)

        self._file_trees[name] = sync_tree
        # Populate the tree asynchronously so the UI is not blocked
        self._populate_sync_tree_async(name, sync_tree, svc)

        # ── Bottom action buttons (5 % of window height) ──────────────
        btn_frame = tk.Frame(tab_frame, bg="#e0e0e0")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # Button 1: Open local folder
        tk.Button(
            btn_frame,
            text="📂 Abrir carpeta",
            command=lambda n=name: self._open_folder(n),
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=4)

        # Button 2: Stop / Start sync
        # Initialise from the 'sync_enabled' flag rather than is_running()
        # because services have not started yet when the tab is built
        # (run() calls start_all() after __init__() finishes).
        will_run = svc.get("sync_enabled", True)
        toggle_text = tk.StringVar(
            value="⏹ Detener" if will_run else "▶ Sincronizar"
        )
        self._toggle_vars[name] = toggle_text
        tk.Button(
            btn_frame,
            textvariable=toggle_text,
            command=lambda n=name, tv=toggle_text: self._toggle_sync(n, tv),
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=4)

        # Button 3: Open configuration window
        tk.Button(
            btn_frame,
            text="⚙️ Configuración",
            command=lambda n=name: self._open_config(n),
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=4)

    # ------------------------------------------------------------------
    # Storage info helpers
    # ------------------------------------------------------------------

    def _fetch_storage_info_async(self, service_name: str, var: tk.StringVar) -> None:
        """
        Fetch cloud storage quota for *service_name* in a background thread
        and update *var* on the main thread when the result is available.

        Uses ``rclone about remote:`` which is supported by OneDrive, Google
        Drive, Dropbox, Box, and pCloud.  For services that do not support
        ``about`` (e.g. S3, SFTP), the default "💾 Total: 0 | ..." text is
        left unchanged.
        """
        def _worker() -> None:
            info = self._rclone.get_storage_info(service_name)
            if info:
                self._root.after(0, lambda: var.set(f"💾 {info}"))

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"about-{service_name}",
        ).start()

    def _populate_sync_tree_async(
        self,
        service_name: str,
        tree: ttk.Treeview,
        svc: Dict,
    ) -> None:
        """Show a loading indicator and start a background status check for the tree.

        On the first call (tab creation) we immediately show any previously-saved
        snapshot so the user always has data visible.  A "🔄 actualizando…" notice
        row is then appended so the user knows a fresh scan is in progress.
        The background worker tries three strategies in order:

        1. **mtime comparison** (fast): ``rclone lsjson --recursive`` fetches
           remote file metadata and compares modification dates against local
           files via :func:`os.stat`.
        2. **rclone check** (accurate): ``rclone check --combined -`` verifies
           file content via checksums — slower but more thorough.
        3. **local filesystem scan** (offline): reads local files and uses
           the persisted ``sync_history`` to infer status without remote access.

        When the fresh scan completes the snapshot is saved to disk so the next
        startup (or refresh) can show it immediately again.

        After the tree is populated an automatic re-check is scheduled
        according to the service's configured ``tree_refresh_small_secs`` /
        ``tree_refresh_large_secs`` settings.
        """
        # Load any previously-saved snapshot and show it right away.
        # This ensures the user always sees data immediately, even before the
        # background rclone scan has finished.
        cached_items, saved_at = _load_tree_cache(service_name)
        try:
            tree.delete(*tree.get_children())
            if cached_items:
                _fill_sync_tree(tree, cached_items)
                # Append a subtle notice so the user knows a refresh is running
                notice = f"🔄 Actualizando… (datos del {saved_at})"
                try:
                    tree.insert("", "end", iid="__loading__",
                                text=notice, values=("", "", ""))
                except tk.TclError:
                    pass
            else:
                tree.insert("", "end", iid="__loading__",
                            text="🔄 Actualizando…", values=("", "", ""))
        except tk.TclError:
            pass
        self._start_tree_check(service_name)

    def _refresh_sync_tree(self, service_name: str) -> None:
        """Cancel any pending auto-refresh and run a new tree check immediately.

        The existing tree is **kept visible** while the background scan runs so
        the user always has data in front of them.  A ``__loading__`` notice row
        is added at the bottom to signal that a refresh is in progress.  When
        the scan completes, :meth:`_on_tree_check_done` replaces the whole tree
        with the fresh results (via :func:`_fill_sync_tree`).
        """
        # Cancel pending scheduled refresh
        after_id = self._tree_refresh_ids.pop(service_name, None)
        if after_id is not None:
            try:
                self._root.after_cancel(after_id)
            except tk.TclError:
                pass
        tree = self._file_trees.get(service_name)
        if tree is None:
            return
        # Keep the existing tree content — only add/replace the loading notice.
        # This ensures the user always sees the previous scan while waiting for
        # the next one to complete.
        try:
            if tree.exists("__loading__"):
                tree.delete("__loading__")
            tree.insert("", "end", iid="__loading__",
                        text="🔄 Actualizando…", values=("", "", ""))
        except tk.TclError:
            pass
        self._start_tree_check(service_name)

    def _start_tree_check(self, service_name: str) -> None:
        """Launch 3 daemon threads to scan local and remote, then merge results.

        **Thread model** (each service has its own independent set of threads):

        * **Thread 1 – local scan**: walks the local filesystem using
          ``os.stat`` (very fast).  Writes every file's path, size, and mtime
          to the per-service table in :attr:`_db` via
          :meth:`~src.db.file_scan_db.FileScanDB.upsert_local_batch`.
          Signals ``local_done`` when finished.

        * **Thread 2 – remote scan**: fetches remote file metadata via
          ``rclone lsjson`` (:meth:`RcloneManager.list_remote_metadata`).
          Writes path, size, and mtime to the DB via
          :meth:`~src.db.file_scan_db.FileScanDB.upsert_remote_batch`.
          Signals ``remote_done`` when finished (even on failure).

        * **Thread 3 – merger**: waits for Thread 1 first (fast), reads the
          DB and shows a partial tree immediately.  After Thread 2 completes
          it calls :meth:`~src.db.file_scan_db.FileScanDB.update_statuses`
          to persist per-file sync status, then reads the DB again to build
          the final tree.

        **Cancellation**: every call increments
        :attr:`_tree_check_generations` for the service.  All three threads
        capture the generation number at start and discard their results if
        the counter has changed (i.e. a newer refresh was requested while
        they were running).

        **Database isolation**: each service uses its own table, so Thread 1
        for service A and Thread 1 for service B write to different tables and
        never block each other.
        """
        gen = self._tree_check_generations.get(service_name, 0) + 1
        self._tree_check_generations[service_name] = gen

        # Record the time this scan started
        started_str = datetime.now(timezone.utc).astimezone().strftime(_SCAN_TIME_FMT)
        started_var = self._tree_scan_started_vars.get(service_name)
        if started_var is not None:
            started_var.set(f"{_SCAN_STARTED_PREFIX}{started_str}")

        # Reset thread status labels and start the wall-clock timer.
        self._tree_scan_wall_times[service_name] = time.monotonic()
        elapsed_var_init = self._tree_scan_elapsed_vars.get(service_name)
        if elapsed_var_init is not None:
            elapsed_var_init.set(f"{_SCAN_ELAPSED_PREFIX}—")
        status_vars_init = self._tree_thread_status_vars.get(service_name)
        if status_vars_init is not None:
            local_sv, remote_sv, merger_sv = status_vars_init
            local_sv.set(f"{_SCAN_LOCAL_PREFIX}{_THREAD_SCANNING}")
            remote_sv.set(f"{_SCAN_REMOTE_PREFIX}{_THREAD_SCANNING}")
            merger_sv.set(f"{_SCAN_MERGER_PREFIX}{_THREAD_SCANNING}")

        svc = self._config.get_service(service_name)
        if svc is None:
            return
        local_path = svc.get("local_path", "")

        # Ensure the DB table exists for this service.
        self._db.ensure_table(service_name)

        # Used to coordinate between the three threads.
        local_done = threading.Event()
        remote_done = threading.Event()
        # Thread 2 sets this to False when the remote scan fails (None/error).
        remote_available = threading.Event()

        # Capture current generation for use inside closures.
        scan_gen = gen
        db = self._db
        rclone = self._rclone

        # ── Thread 1: local filesystem scan ────────────────────────────────
        def _local_worker() -> None:
            scan_ts = datetime.now(timezone.utc).timestamp()
            local_files: Dict[str, Dict] = {}
            if local_path and os.path.isdir(local_path):
                base = Path(local_path)
                for dirpath, dirs, filenames in os.walk(local_path):
                    # Include directory entries so the DB tracks local directories
                    # and the tree can compare them against remote directories.
                    for d in dirs:
                        full = os.path.join(dirpath, d)
                        try:
                            rel = str(Path(full).relative_to(base)).replace("\\", "/")
                            st = os.stat(full)
                            local_files[rel] = {
                                "mtime": st.st_mtime,
                                "size": 0,
                                "is_dir": True,
                            }
                        except (OSError, ValueError):
                            pass
                    for name in filenames:
                        full = os.path.join(dirpath, name)
                        try:
                            rel = str(Path(full).relative_to(base)).replace("\\", "/")
                            st = os.stat(full)
                            local_files[rel] = {
                                "mtime": st.st_mtime,
                                "size": st.st_size,
                                "is_dir": False,
                            }
                        except (OSError, ValueError):
                            pass
            n_local_files = sum(1 for v in local_files.values() if not v.get("is_dir"))
            n_local_dirs  = sum(1 for v in local_files.values() if v.get("is_dir"))
            db.upsert_local_batch(service_name, scan_ts, local_files)
            local_done.set()
            local_detail = f"{n_local_files} arch., {n_local_dirs} dirs."
            self._root.after(
                0,
                lambda g=scan_gen, d=local_detail: self._update_thread_status(
                    service_name, g, "local", d
                ),
            )

        # ── Thread 2: remote metadata scan ─────────────────────────────────
        def _remote_worker() -> None:
            scan_ts = datetime.now(timezone.utc).timestamp()
            remote_files, remote_error = rclone.list_remote_metadata(service_name)
            if remote_files is not None:
                n_remote_files = sum(1 for v in remote_files.values() if not v.get("is_dir"))
                n_remote_dirs  = sum(1 for v in remote_files.values() if v.get("is_dir"))
                try:
                    db.upsert_remote_batch(service_name, scan_ts, remote_files)
                    remote_available.set()
                    remote_detail = f"{n_remote_files} arch., {n_remote_dirs} dirs."
                    self._error_logger.log(
                        service_name,
                        f"☁️ Escaneo remoto: {n_remote_files} archivos y "
                        f"{n_remote_dirs} directorios encontrados y guardados en BD",
                    )
                except Exception as exc:  # pragma: no cover
                    remote_detail = "Error al guardar"
                    self._error_logger.log(
                        service_name,
                        f"☁️ Error al guardar datos remotos en la base de datos: {exc}",
                    )
            else:
                err_msg = remote_error or "error desconocido"
                remote_detail = "Sin datos"
                self._error_logger.log(
                    service_name,
                    f"☁️ No se pudo obtener datos del remoto: {err_msg}",
                )
            remote_done.set()
            self._root.after(
                0,
                lambda g=scan_gen, d=remote_detail: self._update_thread_status(
                    service_name, g, "remote", d
                ),
            )

        # ── Thread 3: merger — reads DB, computes statuses, updates tree ────
        def _merger_worker() -> None:
            # Wait for the fast local scan first so we can show partial data.
            local_done.wait()
            if self._tree_check_generations.get(service_name) != scan_gen:
                return  # cancelled by a newer refresh

            # Build partial tree from local-only DB data and show immediately.
            records = db.get_all_records(service_name)
            # Convert DB records into the comparison format expected by
            # _merge_local_and_comparison (only local data available yet).
            partial_comparison = [
                {
                    "rel": rec["rel"],
                    "status": "local_only",
                    "local_mtime": rec["local_mtime"],
                    "remote_mtime": None,
                    "is_dir": rec.get("is_dir", False),
                }
                for rec in records if rec["rel"]
            ]
            partial_items = _merge_local_and_comparison(local_path, partial_comparison)
            self._root.after(
                0,
                lambda: self._on_tree_partial_update(service_name, scan_gen, partial_items),
            )

            # Now wait for the remote scan.
            remote_done.wait()
            if self._tree_check_generations.get(service_name) != scan_gen:
                return  # cancelled

            if remote_available.is_set():
                # Both sides are available: compute statuses and store in DB.
                db.update_statuses(service_name, scan_ts=0)
                records = db.get_all_records(service_name)
                full_comparison = [
                    {
                        "rel": rec["rel"],
                        "status": rec["status"],
                        "local_mtime": rec["local_mtime"],
                        "remote_mtime": rec["remote_mtime"],
                        "is_dir": rec.get("is_dir", False),
                    }
                    for rec in records if rec["rel"]
                ]
                final_items = _merge_local_and_comparison(local_path, full_comparison)
            else:
                # Remote unavailable: fall back to sync_history for status hints.
                sync_history = svc.get("sync_history", [])
                synced_set = {
                    e.get("file", "") for e in sync_history if e.get("synced") is True
                }
                pending_set = {
                    e.get("file", "") for e in sync_history if e.get("synced") is False
                }
                final_items = _scan_local_tree(local_path, synced_set, pending_set)

            # Log a status breakdown so the user can see what the tree contains.
            n_files = sum(1 for it in final_items if not it["is_dir"])
            n_dirs  = sum(1 for it in final_items if it["is_dir"])
            status_counts: Dict[str, int] = {}
            for it in final_items:
                if not it["is_dir"]:
                    s = it["status"]
                    status_counts[s] = status_counts.get(s, 0) + 1
            breakdown = ", ".join(
                f"{v} {k}" for k, v in sorted(status_counts.items()) if v
            )
            self._error_logger.log(
                service_name,
                f"🔄 Árbol generado: {n_files} archivos en {n_dirs} directorios"
                + (f" [{breakdown}]" if breakdown else ""),
            )

            self._root.after(
                0,
                lambda: self._on_tree_check_done(service_name, final_items),
            )
            self._root.after(
                0,
                lambda g=scan_gen: self._update_thread_status(service_name, g, "merger"),
            )

        threading.Thread(
            target=_local_worker, daemon=True, name=f"local-{service_name}"
        ).start()
        threading.Thread(
            target=_remote_worker, daemon=True, name=f"remote-{service_name}"
        ).start()
        threading.Thread(
            target=_merger_worker, daemon=True, name=f"merge-{service_name}"
        ).start()

    def _on_tree_partial_update(
        self, service_name: str, gen: int, items: List[Dict]
    ) -> None:
        """Show partial (local-only) scan results while the remote scan runs.

        Called on the main thread.  Fills the tree with local data immediately
        and replaces the generic loading notice with a more specific message
        indicating that the remote is still being scanned.
        """
        if self._tree_check_generations.get(service_name) != gen:
            return  # a newer refresh has started — discard
        tree = self._file_trees.get(service_name)
        if tree is None:
            return
        _fill_sync_tree(tree, items)
        # Replace the loading notice with a more descriptive one
        try:
            if tree.exists("__loading__"):
                tree.delete("__loading__")
            tree.insert(
                "", "end", iid="__loading__",
                text="🌐 Consultando versión remota…",
                values=("", "", ""),
            )
        except tk.TclError:
            pass

    def _build_tree_items_from_check(
        self, service_name: str, svc: Optional[Dict]
    ) -> List[Dict]:
        """Determine sync status for each file and return tree node dicts.

        Strategy:

        1. **Local filesystem scan** is always run first to build a complete
           baseline of every file present on disk.  This guarantees that every
           local file appears in the tree regardless of what the remote reports.

        2. **mtime comparison** – ``rclone lsjson --recursive`` fetches remote
           file metadata; local mtimes come from :func:`os.stat`.  When this
           succeeds the comparison statuses are overlaid onto the local baseline
           and any remote-only files are appended.

        3. **rclone check** – ``rclone check --combined -`` computes checksums.
           Used the same way as stage 2 when the mtime comparison is unavailable.

        4. **Offline fallback** – when no remote connection is available, the
           local-baseline scan is returned with ``sync_history`` used to infer
           "synced" / "pending" status for files that have been seen before.
        """
        if svc is None:
            return []
        local_path = svc.get("local_path", "")

        # Stage 2 & 3: try remote comparison (mtime first, then checksum)
        mtime_results = self._rclone.check_sync_status_mtime(service_name)
        if mtime_results is not None:
            return _merge_local_and_comparison(local_path, mtime_results)

        check_results = self._rclone.check_sync_status(service_name)
        if check_results is not None:
            return _merge_local_and_comparison(local_path, check_results)

        # Stage 4: local filesystem scan (offline fallback)
        sync_history = svc.get("sync_history", [])
        synced_set = {e.get("file", "") for e in sync_history if e.get("synced") is True}
        pending_set = {e.get("file", "") for e in sync_history if e.get("synced") is False}
        return _scan_local_tree(local_path, synced_set, pending_set)

    def _on_tree_check_done(self, service_name: str, items: List[Dict]) -> None:
        """Called on the main thread after a tree check completes.

        Fills the tree widget, persists the snapshot to disk (so future
        startups / refreshes can show it immediately), and schedules the next
        automatic refresh.
        """
        tree = self._file_trees.get(service_name)
        if tree is None:
            return
        _fill_sync_tree(tree, items)
        # Persist the fresh snapshot so the next startup shows data immediately.
        # _save_tree_cache is a no-op when items is empty, so a failed scan
        # never overwrites a good snapshot.
        _save_tree_cache(service_name, items)
        self._schedule_tree_refresh(service_name, len(items))

    def _update_thread_status(
        self, service_name: str, gen: int, thread_name: str, detail: str = ""
    ) -> None:
        """Update a scan-thread status label on the main thread.

        Called via ``_root.after(0, ...)`` from each worker thread once it
        finishes.  *thread_name* is one of ``"local"``, ``"remote"``, or
        ``"merger"``.  When the merger finishes the total elapsed scan time
        is computed and displayed.

        *detail* is an optional string (e.g. ``"42 arch., 5 dirs."`` for a
        successful remote scan, or ``"Sin datos remotos"`` on failure).  When
        provided it replaces the generic ``_THREAD_DONE`` text so the user can
        see at a glance how many entries were found.
        """
        if self._tree_check_generations.get(service_name) != gen:
            return  # a newer scan has started — discard stale update
        status_vars = self._tree_thread_status_vars.get(service_name)
        if status_vars is None:
            return
        local_sv, remote_sv, merger_sv = status_vars
        done_text = detail if detail else _THREAD_DONE
        if thread_name == "local":
            local_sv.set(f"{_SCAN_LOCAL_PREFIX}{done_text}")
        elif thread_name == "remote":
            remote_sv.set(f"{_SCAN_REMOTE_PREFIX}{done_text}")
        elif thread_name == "merger":
            merger_sv.set(f"{_SCAN_MERGER_PREFIX}{done_text}")
            start_t = self._tree_scan_wall_times.get(service_name)
            elapsed_var = self._tree_scan_elapsed_vars.get(service_name)
            if start_t is not None and elapsed_var is not None:
                elapsed_sec = time.monotonic() - start_t
                elapsed_var.set(f"{_SCAN_ELAPSED_PREFIX}{elapsed_sec:.1f}s")

    def _schedule_tree_refresh(self, service_name: str, item_count: int) -> None:
        """Cancel any existing scheduled refresh and queue the next one.

        The delay is taken from the service's ``tree_refresh_small_secs`` or
        ``tree_refresh_large_secs`` setting depending on whether *item_count*
        is below or at/above ``TREE_FILE_THRESHOLD``.

        Updates the "next refresh" label with the computed wall-clock time.
        """
        # Cancel existing scheduled refresh for this service
        after_id = self._tree_refresh_ids.pop(service_name, None)
        if after_id is not None:
            try:
                self._root.after_cancel(after_id)
            except tk.TclError:
                pass
        svc = self._config.get_service(service_name)
        if svc is None:
            return
        if item_count < TREE_FILE_THRESHOLD:
            interval_secs = svc.get("tree_refresh_small_secs", 60)
        else:
            interval_secs = svc.get("tree_refresh_large_secs", 600)
        effective_secs = max(_MIN_REFRESH_INTERVAL_SECS, interval_secs)
        interval_ms = effective_secs * 1000
        new_id = self._root.after(
            interval_ms,
            lambda n=service_name: self._refresh_sync_tree(n),
        )
        self._tree_refresh_ids[service_name] = new_id

        # Update the "next scan" label with the projected wall-clock time.
        next_var = self._tree_scan_next_vars.get(service_name)
        if next_var is not None:
            next_dt = datetime.now(timezone.utc).astimezone() + timedelta(seconds=effective_secs)
            next_str = next_dt.strftime(_SCAN_TIME_FMT)
            next_var.set(f"{_SCAN_NEXT_PREFIX}{next_str}")

    def _cancel_all_tree_refreshes(self) -> None:
        """Cancel all pending tree auto-refresh after() jobs."""
        for after_id in self._tree_refresh_ids.values():
            if after_id is not None:
                try:
                    self._root.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._tree_refresh_ids.clear()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _open_folder(self, service_name: str) -> None:
        """Open the service's local sync folder in the system file manager."""
        svc = self._config.get_service(service_name)
        if svc is None:
            return
        path = svc.get("local_path", "")
        if not path:
            messagebox.showwarning("Sin carpeta", "Este servicio no tiene carpeta local configurada.", parent=self._root)
            return
        # Offer to create the folder if it doesn't exist yet
        if not os.path.exists(path):
            if messagebox.askyesno(
                "Crear carpeta",
                f"La carpeta '{path}' no existe.\n¿Deseas crearla ahora?",
                parent=self._root,
            ):
                try:
                    os.makedirs(path, exist_ok=True)
                except OSError as exc:
                    messagebox.showerror("Error", f"No se pudo crear la carpeta:\n{exc}", parent=self._root)
                    return
            else:
                return
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{exc}", parent=self._root)

    def _toggle_sync(self, service_name: str, text_var: tk.StringVar) -> None:
        """Stop or start synchronization for the given service."""
        if self._rclone.is_running(service_name):
            self._rclone.stop_service(service_name)
            text_var.set("▶ Sincronizar")
            self._config.update_service(service_name, {"sync_enabled": False})
        else:
            # Clear any stale bisync lock files left by a previous interrupted
            # sync before restarting, so bisync does not fail with "prior lock
            # file found".
            self._rclone.clear_bisync_locks(service_name)
            self._config.update_service(service_name, {"sync_enabled": True})
            self._rclone.start_service(service_name)
            text_var.set("⏹ Detener")

    def _open_config(self, service_name: str) -> None:
        """Open the configuration window for the given service."""
        from src.gui.config_window import ConfigWindow

        ConfigWindow(
            parent=self._root,
            config_manager=self._config,
            rclone_manager=self._rclone,
            service_name=service_name,
            on_saved=lambda new_name=service_name: self._on_service_config_saved(
                service_name, new_name
            ),
            on_deleted=self._on_service_deleted,
            error_logger=self._error_logger,
        )

    def _on_service_config_saved(
        self, old_service_name: str, new_service_name: str
    ) -> None:
        """Called when the user saves the configuration for a single service.

        Restarts **only** the affected service (sync + tree scan), leaving every
        other service's threads and timers undisturbed.  If the service was
        renamed, the corresponding DB table is renamed before the UI rebuild.

        Parameters
        ----------
        old_service_name:
            The service name *before* the save (captured at ``_open_config``
            time via the closure).
        new_service_name:
            The service name *after* the save, passed by ``ConfigWindow`` via
            the ``on_saved`` callback signature.
        """
        # Rename DB table if the service name changed.
        if new_service_name != old_service_name:
            self._db.rename_table(old_service_name, new_service_name)

        # Determine effective name for post-rebuild restart.
        effective_name = new_service_name

        # Capture running state before making any changes.
        was_running = self._rclone.is_running(old_service_name)
        if was_running:
            self._rclone.stop_service(old_service_name)

        # Full UI rebuild (updates tab titles, labels, etc.).
        self._refresh_tabs()

        # Restart the bisync thread for the modified service so new settings
        # (e.g. sync_interval, local_path) take effect immediately.
        svc = self._config.get_service(effective_name)
        if svc is not None and (was_running or svc.get("sync_enabled")):
            self._rclone.clear_bisync_locks(effective_name)
            self._config.update_service(effective_name, {"sync_enabled": True})
            self._rclone.start_service(effective_name)

    def _open_config_at_info(self, service_name: str) -> None:
        """Open the configuration window at the 'Información del servicio' panel.

        Uses the ``INFO_PANEL_INDEX`` constant exported by ``config_window`` so
        that the panel index stays in sync with the sidebar menu definition.
        The info panel contains the 'Reconectar' and 'Buscar drive_id' buttons
        for fixing a missing drive_id/drive_type configuration error.

        When the user saves the configuration after fixing the error, the sync
        for the service is automatically started (so no manual click is needed),
        and the drive_id error banner is hidden once the first successful bisync
        cycle completes (status → "Actualizado").
        """
        from src.gui.config_window import ConfigWindow, INFO_PANEL_INDEX

        ConfigWindow(
            parent=self._root,
            config_manager=self._config,
            rclone_manager=self._rclone,
            service_name=service_name,
            on_saved=lambda new_name=service_name: self._on_config_fixed_start_sync(
                new_name
            ),
            on_deleted=self._on_service_deleted,
            error_logger=self._error_logger,
            initial_panel=INFO_PANEL_INDEX,
        )

    def _on_config_fixed_start_sync(self, service_name: str) -> None:
        """Called after the user saves config from the drive_id reconfigure flow.

        Rebuilds the tabs (picks up the newly written drive_id/drive_type values)
        and then immediately starts the sync for *service_name* so the user does
        not have to click the sync button manually.

        Ordering note: ``update_service`` writes synchronously to the in-memory
        config dict (and to disk) before ``start_service`` spawns the background
        thread, so the thread will always read the updated ``sync_enabled`` flag
        when ``_sync_loop`` calls ``get_service()``.  There is no race condition.
        The toggle button is kept in sync by the "Iniciando…" status callback
        that ``_sync_loop`` emits as its very first action, which calls
        ``_update_status`` → sets button to "⏹ Detener".
        """
        self._refresh_tabs()
        # Mark the service as enabled and start the background sync loop.
        # clear_bisync_locks removes any stale .lck / .lst-new files that may
        # have been left by the previous failed attempt.
        self._rclone.clear_bisync_locks(service_name)
        self._config.update_service(service_name, {"sync_enabled": True})
        self._rclone.start_service(service_name)

    def _open_wizard(self) -> None:
        """Launch the add-new-service wizard."""
        from src.gui.setup_wizard import SetupWizard

        SetupWizard(
            parent=self._root,
            config_manager=self._config,
            rclone_manager=self._rclone,
            on_complete=self._on_service_added,
        )

    def _open_import_dialog(self) -> None:
        """Launch the import-rclone-config dialog."""
        from src.gui.import_dialog import ImportConfigDialog

        ImportConfigDialog(
            parent=self._root,
            config_manager=self._config,
            rclone_manager=self._rclone,
            on_complete=self._on_service_added,
        )

    # ------------------------------------------------------------------
    # Tray / window management
    # ------------------------------------------------------------------

    def _on_minimize(self, event: tk.Event) -> None:
        """
        Called when the window is iconified (minimized).

        Hides the window.  On non-Elementary OS systems, also starts the
        pystray tray icon so the user can restore the window from it.
        On Elementary OS the Wingpanel indicator is already running and
        visible, so no additional tray icon is needed.
        """
        # Only respond to the root window's Unmap event
        if event.widget is not self._root:
            return
        # Withdraw (hide) the window
        self._root.withdraw()
        # On non-Elementary systems, start the pystray tray icon if not yet running
        if self._elementary is None or not self._elementary.is_running():
            if not self._tray_started and self._tray.is_available():
                self._tray.start()
                self._tray_started = True

    def _restore_window(self) -> None:
        """Restore the main window from the tray (runs on tray/indicator thread → schedule on main)."""
        self._root.after(0, self._do_restore)

    def _do_restore(self) -> None:
        """Re-show and lift the main window."""
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _quit(self) -> None:
        """Stop all sync threads, save error log, remove tray icon(s), and destroy the window."""
        self._cancel_all_tree_refreshes()
        self._rclone.stop_all()
        self._error_logger.save_to_file()
        self._tray.stop()
        if self._elementary is not None:
            self._elementary.stop()
        self._root.destroy()

    # ------------------------------------------------------------------
    # Callbacks from RcloneManager
    # ------------------------------------------------------------------

    def _on_status_change(self, service_name: str, status: str) -> None:
        """
        Invoked by RcloneManager when the sync status changes.
        Schedules a UI update on the main thread.
        """
        self._root.after(0, lambda: self._update_status(service_name, status))

    def _update_status(self, service_name: str, status: str) -> None:
        """Update the status label and toggle button for the given service."""
        var = self._status_vars.get(service_name)
        if var:
            var.set(status)
        # Keep the toggle button label accurate: "Detener" while active, "Sincronizar" when stopped
        toggle_var = self._toggle_vars.get(service_name)
        if toggle_var:
            if status == _STATUS_STOPPED:
                toggle_var.set("▶ Sincronizar")
            else:
                toggle_var.set("⏹ Detener")
        # A successful bisync cycle means the drive_id error (if it was shown)
        # is now resolved — hide the warning banner automatically.
        if status == "Actualizado":
            self._hide_drive_id_banner(service_name)
            # Trigger an immediate tree refresh so directory colours reflect
            # the completed sync.  A short delay lets the status label render
            # first; the refresh itself runs in a daemon background thread.
            self._root.after(500, lambda n=service_name: self._refresh_sync_tree(n))
        # Update tooltips in both tray implementations
        tooltip = f"Rclone Manager – {service_name}: {status}"
        self._tray.update_tooltip(tooltip)
        if self._elementary is not None:
            self._elementary.update_tooltip(tooltip)

    def _on_file_synced(self, service_name: str, file_path: str, synced: bool) -> None:
        """
        Invoked by RcloneManager when a file is transferred.
        Schedules a Listbox update on the main thread.
        """
        self._root.after(0, lambda: self._add_file_entry(service_name, file_path, synced))

    def _on_rclone_error(self, service_name: str, message: str) -> None:
        """
        Invoked by RcloneManager when an error occurs.
        Logs the error via ErrorLogger (thread-safe: no UI update needed).
        """
        self._error_logger.log(service_name, message)

    def _on_native_api_call(self, service_name: str, message: str) -> None:
        """
        Invoked by NativeSyncManager for every HTTP API call made on behalf of a
        service (both successful calls and errors).

        Logs the entry with a ``🔗 API`` prefix so it is clearly distinct from
        rclone errors in the Errores panel and can be filtered / searched easily.
        """
        self._error_logger.log(service_name, f"🔗 API | {message}")

    def _on_drive_id_error(self, service_name: str) -> None:
        """
        Invoked by RcloneManager when a drive_id/drive_type missing error is
        detected in bisync output.  Schedules showing a warning banner on the
        main thread so the user can fix the configuration immediately.
        """
        self._root.after(0, lambda: self._show_drive_id_banner(service_name))

    def _show_drive_id_banner(self, service_name: str) -> None:
        """Make the drive_id error banner visible in the service's tab.

        The banner was created (hidden) by ``_add_service_tab``; this method
        packs it so it appears between the header and the file list.  Calling
        it repeatedly is safe — the banner is only packed once.
        """
        banner = self._drive_id_banners.get(service_name)
        if banner is None:
            return
        try:
            if not banner.winfo_ismapped():
                banner.pack(fill=tk.X, padx=4, pady=(2, 0))
        except tk.TclError:
            pass

    def _hide_drive_id_banner(self, service_name: str) -> None:
        """Hide the drive_id error banner for the given service (if visible).

        Called automatically by ``_update_status`` when a bisync cycle
        completes successfully (status → "Actualizado"), confirming that the
        configuration problem has been resolved.
        """
        banner = self._drive_id_banners.get(service_name)
        if banner is None:
            return
        try:
            if banner.winfo_ismapped():
                banner.pack_forget()
        except tk.TclError:
            pass

    def _add_file_entry(self, service_name: str, file_path: str, synced: bool) -> None:
        """Insert a new file entry into the service's Listbox (max 50 items)
        and update the corresponding node in the sync tree if it exists."""
        import datetime

        listbox = self._file_lists.get(service_name)
        if listbox is None:
            return
        icon = "✅" if synced else "⏳"
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        listbox.insert(0, f"{icon} [{ts}]  {file_path}")
        # Enforce 50-item limit
        if listbox.size() > 50:
            listbox.delete(50, tk.END)
        # Reflect the new sync status in the right-side tree view
        tree = self._file_trees.get(service_name)
        if tree is not None:
            _update_tree_status(tree, file_path, "synced" if synced else "pending")
            # Propagate the new file status up to all ancestor directories so
            # their colours stay accurate during an active bisync cycle — without
            # waiting for the next scheduled full tree refresh.
            _update_tree_ancestors(tree, file_path)

    # ------------------------------------------------------------------
    # Tab refresh helpers
    # ------------------------------------------------------------------

    def _refresh_tabs(self) -> None:
        """Rebuild all tabs after a config change."""
        self._cancel_all_tree_refreshes()
        if self._notebook:
            self._notebook.destroy()
            self._notebook = None
        for w in self._root.winfo_children():
            w.destroy()
        self._file_lists.clear()
        self._status_vars.clear()
        self._toggle_vars.clear()
        self._storage_vars.clear()
        # Banner widgets are destroyed along with their parent frames above;
        # clear the dict so _add_service_tab can repopulate it with fresh widgets.
        self._drive_id_banners.clear()
        self._file_trees.clear()
        self._tree_scan_started_vars.clear()
        self._tree_scan_next_vars.clear()
        self._tree_thread_status_vars.clear()
        self._tree_scan_elapsed_vars.clear()
        self._tree_scan_wall_times.clear()
        self._build_ui()

    def _on_service_added(self, service_name: str) -> None:
        """Called after a new service is successfully added via the wizard."""
        # Create the DB table for the new service so Thread 1 / Thread 2 can
        # start writing immediately after _refresh_tabs triggers _start_tree_check.
        self._db.ensure_table(service_name)
        self._rclone.start_service(service_name)
        self._refresh_tabs()

    def _on_service_deleted(self, service_name: str) -> None:
        """Called after a service is deleted from the config window."""
        # Drop the DB table for the removed service to free disk space.
        self._db.drop_table(service_name)
        self._refresh_tabs()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start all sync threads and mount processes, then enter the Tkinter main loop."""
        self._rclone.start_all()
        self._rclone.start_all_mounts()
        self._root.mainloop()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Maximum number of *file* nodes rendered in the sync tree.  Directory nodes
# are always synthesised regardless of this limit so that the full folder
# structure is always visible.  This cap only limits how many file leaves are
# shown in order to keep the UI responsive on very large sync directories.
# Kept in sync with TREE_FILE_THRESHOLD so the refresh-interval logic and the
# tree display agree on what counts as a "large" service.
_MAX_TREE_FILES = TREE_FILE_THRESHOLD

# Maximum number of *directory* nodes rendered.  Set much higher than
# _MAX_TREE_FILES because directory nodes are cheap to render and the user
# must always be able to see all of their sync folders.
_MAX_TREE_DIRS = _MAX_TREE_FILES * 10

# Minimum auto-refresh interval in seconds (hard floor to avoid hammering
# the cloud API when a user sets an unreasonably low value).
_MIN_REFRESH_INTERVAL_SECS = 30

# strftime format used for "last scan started" and "next scan" labels.
_SCAN_TIME_FMT = "%H:%M:%S"

# Label prefixes for the tree-scan timing widgets.
_SCAN_STARTED_PREFIX = "🕐 Inicio: "
_SCAN_NEXT_PREFIX = "⏭ Próxima: "

# Label prefixes and status text for the per-thread scan progress row.
_THREAD_SCANNING = "Escaneando"
_THREAD_DONE = "Terminado"
_SCAN_LOCAL_PREFIX = "💾 Local: "
_SCAN_REMOTE_PREFIX = "☁️ Remoto: "
_SCAN_MERGER_PREFIX = "🔄 Árbol: "
_SCAN_ELAPSED_PREFIX = "⏱ Total: "

_TREE_STATUS_LABELS: Dict[str, str] = {
    "synced":       "🟢 Ambos",
    "diff":         "⚠️ Diferente",
    "remote_only":  "🟠 Solo remoto",
    "local_only":   "🔵 Solo local",
    "pending":      "⏳ Pendiente",
    "unknown":      "❓",
}
_TREE_STATUS_TAGS: Dict[str, tuple] = {
    "synced":       ("synced",),
    "diff":         ("diff",),
    "remote_only":  ("remote_only",),
    "local_only":   ("local_only",),
    "pending":      ("pending",),
    "unknown":      ("unknown",),
}


def _propagate_dir_status(items: List[Dict]) -> None:
    """Update each directory node's status based on the statuses of its descendant files.

    Directory nodes created by :func:`_build_check_tree` (and
    :func:`_scan_local_tree`) are initially assigned the ``"unknown"`` status
    because only file entries carry meaningful origin information.  This
    function performs a post-processing pass to colour directories using the
    same rules the user sees for individual files:

    * 🔵 ``"local_only"``  – every descendant file exists only on the local disk.
    * 🟠 ``"remote_only"`` – every descendant file exists only on the remote.
    * 🟢 ``"synced"``      – all descendant files are present on both sides
                             (whether identical or differing in mtime/content).
    * ⚠️ ``"diff"``        – at least one descendant file differs between sides.
    * ❓ ``"unknown"``     – the directory contains no files (empty or not scanned).

    The function modifies *items* **in-place** and returns ``None``.
    """
    # Map dir_rel → set of descendant FILE statuses (collected in first pass)
    dir_child_statuses: Dict[str, set] = {}

    for item in items:
        if item["is_dir"]:
            # Ensure every directory has an entry even if it has no files
            if item["rel"] not in dir_child_statuses:
                dir_child_statuses[item["rel"]] = set()
        else:
            # Propagate each file's status up to all of its ancestor directories
            parts = item["rel"].split("/")
            for depth in range(1, len(parts)):
                dir_rel = "/".join(parts[:depth])
                if dir_rel not in dir_child_statuses:
                    dir_child_statuses[dir_rel] = set()
                dir_child_statuses[dir_rel].add(item["status"])

    # Second pass: set each directory's status from accumulated child statuses
    for item in items:
        if not item["is_dir"]:
            continue
        statuses = dir_child_statuses.get(item["rel"], set())
        # Exclude "unknown" files — they carry no origin information and
        # must not inflate a directory's colour to "synced" just because
        # its files haven't been compared yet.  We derive status only from
        # files whose origin is known.
        known = statuses - {"unknown"}
        if not known:
            item["status"] = "unknown"
        elif known <= {"local_only"}:
            item["status"] = "local_only"
        elif known <= {"remote_only"}:
            item["status"] = "remote_only"
        elif "diff" in known:
            item["status"] = "diff"
        else:
            # This directory contains files from both sides (e.g. synced +
            # local_only, or synced + remote_only, or a mix of all known
            # statuses except "diff").  Mark it as "synced" — indicating
            # that at least some content is present on both sides.
            item["status"] = "synced"


def _merge_local_and_comparison(
    local_path: str,
    comparison_items: List[Dict],
) -> List[Dict]:
    """Build tree items using the local filesystem as the complete baseline,
    then overlay remote-comparison statuses on top.

    This is the core of the "local-first scan" strategy requested by the user:

    1. Walk *local_path* to discover **every** file on disk.  All files start
       with status ``"local_only"`` (present locally but not confirmed on the
       remote yet).
    2. Apply statuses from *comparison_items* (``synced``, ``diff``,
       ``remote_only``, …) to the matching local file nodes.  ``local_mtime``
       and ``remote_mtime`` fields are propagated from *comparison_items* when
       present so the Treeview mtime columns can be filled.
    3. Any ``remote_only`` files reported by the comparison that do not exist
       locally are appended (with their synthesised parent directories) so the
       tree also reflects content that lives only on the remote.
    4. Directory statuses are re-propagated from scratch so they reflect the
       final, merged child statuses.

    Parameters
    ----------
    local_path:
        Absolute path to the local sync folder.
    comparison_items:
        List of ``{"rel": str, "status": str, ...}`` dicts returned by
        :meth:`RcloneManager.check_sync_status_mtime` or
        :meth:`RcloneManager.check_sync_status`.  May optionally contain
        ``local_mtime`` and ``remote_mtime`` float fields.

    Returns
    -------
    List[Dict]
        Flat, depth-first-ordered list of node dicts ready for
        :func:`_fill_sync_tree`.  Parents always precede their children.
    """
    # Build comparison lookup: rel_path (normalised) → full item dict
    comp_map: Dict[str, Dict] = {}
    for item in comparison_items:
        rel = item.get("rel", "").strip("/").replace("\\", "/")
        if rel:
            comp_map[rel] = item

    # ── Stage 1: scan local filesystem (complete baseline) ──────────────────
    # _scan_local_tree walks the entire local directory regardless of network
    # availability.  We pass empty synced/pending sets here because we will
    # overlay the real statuses from comp_map in the next step.
    result = _scan_local_tree(local_path, set(), set())

    # ── Stage 2: overlay comparison statuses onto local file nodes ───────────
    # Track ALL local rels (both files and directories) to avoid re-adding
    # entries that already exist locally when we process remote-only items.
    local_all_rels: set = {item["rel"] for item in result}
    local_file_rels: set = set()
    for item in result:
        if item["is_dir"]:
            continue
        rel = item["rel"]
        local_file_rels.add(rel)
        comp_item = comp_map.get(rel)
        if comp_item is not None:
            # Comparison confirmed this file exists on one or both sides.
            cstatus = comp_item.get("status", "unknown")
            # If the DB returned "unknown" (both mtimes NULL — stale/corrupt
            # record) we still know this file IS present locally, so treat it
            # as "local_only" rather than showing it grey.
            item["status"] = cstatus if cstatus != "unknown" else "local_only"
            # Carry through mtime values so the columns can be populated
            if "local_mtime" in comp_item:
                item["local_mtime"] = comp_item["local_mtime"]
            if "remote_mtime" in comp_item:
                item["remote_mtime"] = comp_item["remote_mtime"]
        else:
            # File is present locally but the remote comparison did not mention
            # it → it only exists on the local disk.
            item["status"] = "local_only"

    # ── Stage 3: append remote-only entries (files AND directories) ─────────
    # Entries reported as "remote_only" by the comparison exist on the remote
    # but were NOT found by the local scan (genuinely absent from local folder).
    # This includes empty remote directories which have no files to synthesise
    # a parent node from.  Add them to the result so the tree shows the full
    # picture.
    remote_only_items = []
    for rel, ci in comp_map.items():
        if ci.get("status") == "remote_only" and rel not in local_all_rels:
            ro_item: Dict = {
                "rel": rel,
                "status": "remote_only",
                "is_dir": ci.get("is_dir", False),
            }
            if "remote_mtime" in ci:
                ro_item["remote_mtime"] = ci["remote_mtime"]
            remote_only_items.append(ro_item)

    if remote_only_items:
        # _build_check_tree synthesises parent directory nodes in depth-first
        # order, then caps at _MAX_TREE_FILES / _MAX_TREE_DIRS.
        remote_tree = _build_check_tree(remote_only_items)
        existing_rels: set = {item["rel"] for item in result}
        for item in remote_tree:
            if item["rel"] not in existing_rels:
                result.append(item)

    # ── Stage 4: re-propagate directory statuses ─────────────────────────────
    # Local scan already called _propagate_dir_status, but statuses changed in
    # stages 2 & 3, so we reset all directory nodes and re-run the propagation.
    for item in result:
        if item["is_dir"]:
            item["status"] = "unknown"
    _propagate_dir_status(result)

    # ── Stage 5: fix empty directories that are still "unknown" ──────────────
    # _propagate_dir_status leaves a directory as "unknown" when it has no
    # descendant files with a known status (e.g. the directory is truly empty
    # or contains only sub-directories that are themselves empty).  We know
    # whether each such directory is local or remote, so we can colour it
    # correctly instead of leaving it grey:
    #
    #   • In local_all_rels → directory exists on the local disk → "local_only"
    #   • Not in local_all_rels → came from Stage 3 (remote-only entry) → "remote_only"
    for item in result:
        if item["is_dir"] and item["status"] == "unknown":
            item["status"] = "local_only" if item["rel"] in local_all_rels else "remote_only"

    return result


def _build_check_tree(check_items: List[Dict]) -> List[Dict]:
    """Convert ``rclone check --combined`` output into Treeview node dicts.

    Each input item has ``rel`` (POSIX path) and ``status`` keys.  Items may
    optionally carry ``"is_dir": True`` to indicate that the entry itself is a
    directory (e.g. a remote-only empty directory).  Virtual directory nodes
    are synthesised from file paths so the tree has proper parent–child
    structure.

    Items are ordered depth-first (parent always before its children) so they
    can be inserted into a :class:`ttk.Treeview` in a single forward pass.

    **Cap behaviour**: directory nodes are capped at ``_MAX_TREE_DIRS`` and
    file nodes are capped at ``_MAX_TREE_FILES``.  The two caps are intentionally
    separate so that the full folder structure remains visible even when the file
    count exceeds the file cap.
    """
    result: List[Dict] = []
    seen_dirs: set = set()
    file_count = 0
    dir_count = 0

    # Sort so that a directory path always precedes its children when the file
    # paths themselves are processed in alphabetical order.
    for item in sorted(check_items, key=lambda x: x.get("rel", "").lower()):
        rel = item.get("rel", "").strip("/").replace("\\", "/")
        if not rel:
            continue

        parts = rel.split("/")
        is_item_dir = item.get("is_dir", False)

        # Synthesise parent directory nodes (always shown, up to _MAX_TREE_DIRS).
        # For both files and directories we synthesise all ANCESTOR directories
        # (i.e. all path components except the last one).
        for i in range(1, len(parts)):
            if dir_count >= _MAX_TREE_DIRS:
                break
            dir_rel = "/".join(parts[:i])
            if dir_rel not in seen_dirs:
                seen_dirs.add(dir_rel)
                parent_rel = "/".join(parts[: i - 1]) if i > 1 else ""
                result.append({
                    "rel": dir_rel,
                    "parent": parent_rel,
                    "name": parts[i - 1],
                    "is_dir": True,
                    "status": "unknown",
                })
                dir_count += 1

        if is_item_dir:
            # Directory entry: add it as a directory node (not a file node).
            # This handles explicit remote-only directories (including empty
            # ones) that would otherwise be invisible in the tree.
            if rel not in seen_dirs and dir_count < _MAX_TREE_DIRS:
                parent_rel = "/".join(parts[:-1])
                result.append({
                    "rel": rel,
                    "parent": parent_rel,
                    "name": parts[-1],
                    "is_dir": True,
                    "status": item.get("status", "unknown"),
                })
                seen_dirs.add(rel)
                dir_count += 1
        else:
            # File node (shown up to _MAX_TREE_FILES)
            if file_count < _MAX_TREE_FILES:
                parent_rel = "/".join(parts[:-1])
                node: Dict = {
                    "rel": rel,
                    "parent": parent_rel,
                    "name": parts[-1],
                    "is_dir": False,
                    "status": item.get("status", "unknown"),
                }
                # Carry through mtime fields when present
                if "local_mtime" in item:
                    node["local_mtime"] = item["local_mtime"]
                if "remote_mtime" in item:
                    node["remote_mtime"] = item["remote_mtime"]
                result.append(node)
                file_count += 1

    # Colour each synthesised directory node based on its descendant files
    _propagate_dir_status(result)
    return result


def _scan_local_tree(
    local_path: str,
    synced_set: set,
    pending_set: set,
    max_files: int = _MAX_TREE_FILES,
) -> List[Dict]:
    """Walk *local_path* and return a flat list of node dicts for the sync tree.

    Each dict contains:
        rel    – POSIX-style path relative to *local_path* (used as Treeview iid)
        parent – parent ``rel`` value ("" for root-level items)
        name   – file or directory base name
        is_dir – bool
        status – "synced" | "pending" | "unknown"

    Items are ordered depth-first (parent always before children) so they can
    be inserted into a :class:`ttk.Treeview` in a single forward pass.
    Directories and files with a ``hidden`` prefix (``'.'``) are still shown
    because rclone does not exclude them by default.

    **Cap behaviour**: only *file* nodes are counted against *max_files*.
    Directory nodes are always added (up to ``_MAX_TREE_DIRS``) so that the
    full folder structure remains visible even when the file count is large.
    """
    from pathlib import Path

    result: List[Dict] = []
    if not local_path or not os.path.isdir(local_path):
        return result

    base = Path(local_path)
    # Separate counters for files and directories so directories are never
    # silently omitted when the file cap fires.
    file_counter = [0]
    dir_counter = [0]

    def _walk(dir_path: Path, parent_rel: str) -> None:
        if dir_counter[0] >= _MAX_TREE_DIRS:
            return
        try:
            raw = list(dir_path.iterdir())
            # Pre-compute is_dir() once per entry to avoid redundant stat calls
            # during sort comparisons.
            entries_with_dir = [(p, p.is_dir()) for p in raw]
            entries_with_dir.sort(key=lambda t: (not t[1], t[0].name.lower()))
        except (PermissionError, OSError):
            return

        for entry, entry_is_dir in entries_with_dir:
            rel = entry.relative_to(base).as_posix()
            if entry_is_dir:
                if dir_counter[0] >= _MAX_TREE_DIRS:
                    break
                result.append({
                    "rel": rel,
                    "parent": parent_rel,
                    "name": entry.name,
                    "is_dir": True,
                    "status": "unknown",
                })
                dir_counter[0] += 1
                _walk(entry, rel)
            else:
                if file_counter[0] >= max_files:
                    # File cap reached: skip adding this file node but do NOT
                    # break or return — the loop must continue so that any
                    # remaining *sibling directories* (entries later in the
                    # sorted list) are still visited and added to the tree.
                    # This is the key fix: previously a shared counter caused
                    # sibling root-level folders to be silently omitted when the
                    # first folder consumed the entire budget.
                    continue
                if rel in synced_set:
                    status = "synced"
                elif rel in pending_set:
                    status = "pending"
                else:
                    status = "unknown"
                result.append({
                    "rel": rel,
                    "parent": parent_rel,
                    "name": entry.name,
                    "is_dir": False,
                    "status": status,
                })
                file_counter[0] += 1

    _walk(base, "")
    # Colour each directory node based on its descendant files
    _propagate_dir_status(result)
    # Any directory still "unknown" after propagation is empty on the local disk
    # (no files to inherit a colour from).  It IS present locally, so show it
    # as "local_only" (blue) rather than the uninformative grey "unknown".
    for item in result:
        if item["is_dir"] and item["status"] == "unknown":
            item["status"] = "local_only"
    return result


# ---------------------------------------------------------------------------
# Tree-snapshot persistence helpers
# ---------------------------------------------------------------------------

# Characters that are unsafe in filenames on Windows/Linux/macOS.
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _tree_cache_dir() -> Path:
    """Return (and create) the directory used to persist tree snapshots."""
    d = get_config_dir() / "tree_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tree_cache_path(service_name: str) -> Path:
    """Return the path of the JSON snapshot file for *service_name*."""
    safe = _UNSAFE_FILENAME_RE.sub("_", service_name) or "default"
    return _tree_cache_dir() / f"{safe}.json"


def _save_tree_cache(service_name: str, items: List[Dict]) -> None:
    """Persist *items* to disk as a JSON snapshot (atomic write).

    Adds a ``saved_at`` ISO-8601 timestamp so the UI can display when the
    data was last refreshed.  Only writes if *items* is non-empty so a failed
    scan never overwrites a good snapshot.
    """
    if not items:
        return
    path = _tree_cache_path(service_name)
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_tree_cache(service_name: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Load a previously-saved tree snapshot.

    Returns ``(items, saved_at_str)`` on success, or ``(None, None)`` if no
    snapshot exists or the file cannot be parsed.  *saved_at_str* is a
    human-readable local date/time string.
    """
    path = _tree_cache_path(service_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items: List[Dict] = payload["items"]
        # Convert the UTC ISO timestamp to local time for display
        saved_at_utc = datetime.fromisoformat(payload["saved_at"])
        saved_at_local = saved_at_utc.astimezone()
        saved_at_str = saved_at_local.strftime("%d/%m/%Y %H:%M")
        return items, saved_at_str
    except (OSError, KeyError, ValueError, TypeError):
        return None, None


def _format_mtime(ts: Optional[float]) -> str:
    """Format a UTC Unix timestamp as a local-time string ``"dd/mm HH:MM"``.

    Returns an empty string when *ts* is ``None`` or the conversion fails
    (e.g. out-of-range timestamps on some platforms).
    """
    if ts is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%d/%m %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _fill_sync_tree(tree: ttk.Treeview, items: List[Dict]) -> None:
    """Clear *tree* and insert *items*.

    Must be called on the Tkinter main thread.  *items* must be ordered so
    that every parent appears before its children (which ``_scan_local_tree``
    guarantees via its depth-first traversal).

    Each item dict may optionally carry ``local_mtime`` and ``remote_mtime``
    (UTC Unix timestamps as :class:`float`) which are formatted and shown in
    the "Mod. local" and "Mod. remota" columns respectively.
    """
    # Remove all existing nodes
    try:
        tree.delete(*tree.get_children())
    except tk.TclError:
        return

    for item in items:
        rel    = item["rel"]
        parent = item["parent"]
        name   = item["name"]
        is_dir = item["is_dir"]
        status = item["status"]

        icon  = "📁 " if is_dir else "📄 "
        label = _TREE_STATUS_LABELS.get(status, "❓")
        tags  = _TREE_STATUS_TAGS.get(status, ("unknown",))
        # All nodes start collapsed so users are not overwhelmed by an
        # automatically expanded tree and can open folders on demand.
        open_node = False

        # Mtime columns: only meaningful for files; directories show nothing
        local_str  = "" if is_dir else _format_mtime(item.get("local_mtime"))
        remote_str = "" if is_dir else _format_mtime(item.get("remote_mtime"))

        try:
            tree.insert(
                parent,
                "end",
                iid=rel,
                text=icon + name,
                values=(label, local_str, remote_str),
                tags=tags,
                open=open_node,
            )
        except tk.TclError:
            pass  # skip if the item already exists


def _update_tree_status(tree: ttk.Treeview, rel_path: str, status: str) -> None:
    """Update the status column of the tree node identified by *rel_path*.

    Safe to call even when the node does not exist (e.g. if the background
    scan has not finished yet) — the call is simply a no-op in that case.
    """
    label = _TREE_STATUS_LABELS.get(status, "❓")
    tags  = _TREE_STATUS_TAGS.get(status, ("unknown",))
    try:
        if tree.exists(rel_path):
            tree.set(rel_path, "status", label)
            tree.item(rel_path, tags=tags)
    except tk.TclError:
        pass


def _update_tree_ancestors(tree: ttk.Treeview, rel_path: str) -> None:
    """Re-derive and apply each ancestor directory's status after a child changes.

    Walks bottom-up from the file's immediate parent to the root.  For each
    ancestor directory the current tags of ALL its direct children are read
    from the Treeview widget and the directory's colour is recomputed using
    the same rules as :func:`_propagate_dir_status`.

    This ensures that folder colours stay accurate in real-time as individual
    files are marked ``"synced"`` or ``"pending"`` during a bisync cycle —
    without waiting for the next full background scan.

    Must be called on the Tkinter main thread (as all Treeview operations are).
    """
    parts = rel_path.split("/")
    # Walk from the immediate parent upward (leaf → root order so each
    # directory's tag is correct when its own parent is processed next).
    for depth in range(len(parts) - 1, 0, -1):
        dir_rel = "/".join(parts[:depth])
        if not dir_rel:
            continue
        try:
            if not tree.exists(dir_rel):
                continue
            children = tree.get_children(dir_rel)
        except tk.TclError:
            continue

        # Collect the first tag of each direct child — the tag encodes its status
        child_statuses: set = set()
        for child_iid in children:
            if child_iid == "__loading__":
                continue
            try:
                tags = tree.item(child_iid, "tags")
                if tags:
                    child_statuses.add(tags[0])
            except tk.TclError:
                pass

        # Derive the directory's status using the same logic as _propagate_dir_status
        known = child_statuses - {"unknown"}
        if not known:
            new_status = "unknown"
        elif known <= {"local_only"}:
            new_status = "local_only"
        elif known <= {"remote_only"}:
            new_status = "remote_only"
        elif "diff" in known:
            new_status = "diff"
        else:
            new_status = "synced"

        _update_tree_status(tree, dir_rel, new_status)



def _remove_maximize_button(root: tk.Tk) -> None:
    """
    Disable the maximize button on the given window.

    Implementation varies by operating system.
    """
    system = platform.system()
    if system == "Windows":
        # Use Windows API via ctypes to remove the maximize box
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
            style &= ~0x00010000  # Remove WS_MAXIMIZEBOX
            ctypes.windll.user32.SetWindowLongW(hwnd, -16, style)
        except Exception:
            pass
    elif system == "Darwin":
        # macOS – use the zoomed attribute via Tk
        root.resizable(False, False)
    else:
        # Linux/X11 – tell the window manager
        try:
            root.attributes("-type", "dialog")
        except tk.TclError:
            pass


def _seconds_to_label(seconds: int) -> str:
    """Convert a number of seconds to a human-readable interval string."""
    if seconds < 60:
        return f"{seconds} seg"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins} minuto{'s' if mins != 1 else ''}"
    else:
        hours = seconds // 3600
        return f"{hours} hora{'s' if hours != 1 else ''}"
