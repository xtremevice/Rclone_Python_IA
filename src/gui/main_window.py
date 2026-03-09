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

import os
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from src.config.config_manager import PLATFORM_LABELS, ConfigManager
from src.gui.tray_icon import TrayIcon
from src.gui.elementary_indicator import ElementaryIndicator, is_elementary_os
from src.gui.error_logger import ErrorLogger
from src.rclone.rclone_manager import RcloneManager

# Status string emitted by RcloneManager when no sync is running
_STATUS_STOPPED = "Detenido"


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

        # Root Tk window
        self._root = tk.Tk()
        self._root.title("Rclone Manager")
        self._root.resizable(False, False)

        # Remove maximize button on supported platforms
        _remove_maximize_button(self._root)

        _center_window(self._root, height_pct=0.60, width_pct=0.20)

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

        # Row 1: Storage quota info (fetched asynchronously via rclone about)
        storage_var = tk.StringVar(value="💾 Total: 0  |  Usado: 0  |  Libre: 0")
        self._storage_vars[name] = storage_var
        tk.Label(
            header,
            textvariable=storage_var,
            bg="#f0f4fa",
            fg="#555555",
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))

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

        # ── File change list (60 % of window height) ──────────────────
        list_frame = tk.Frame(tab_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        sb = tk.Scrollbar(list_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            list_frame,
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
            on_saved=self._refresh_tabs,
            on_deleted=self._on_service_deleted,
            error_logger=self._error_logger,
        )

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
            on_saved=lambda: self._on_config_fixed_start_sync(service_name),
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
        """Insert a new file entry into the service's Listbox (max 50 items)."""
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

    # ------------------------------------------------------------------
    # Tab refresh helpers
    # ------------------------------------------------------------------

    def _refresh_tabs(self) -> None:
        """Rebuild all tabs after a config change."""
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
        self._build_ui()

    def _on_service_added(self, service_name: str) -> None:
        """Called after a new service is successfully added via the wizard."""
        self._rclone.start_service(service_name)
        self._refresh_tabs()

    def _on_service_deleted(self, service_name: str) -> None:
        """Called after a service is deleted from the config window."""
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
