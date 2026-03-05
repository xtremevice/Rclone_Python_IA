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

        # Root Tk window
        self._root = tk.Tk()
        self._root.title("Rclone Manager")
        self._root.resizable(False, False)

        # Remove maximize button on supported platforms
        _remove_maximize_button(self._root)

        _center_window(self._root, height_pct=0.60, width_pct=0.20)

        # System tray integration
        self._tray = TrayIcon(on_show=self._restore_window, on_quit=self._quit)

        # Intercept window close (×) to quit the app entirely
        self._root.protocol("WM_DELETE_WINDOW", self._quit)

        # Intercept minimize to send to tray
        self._root.bind("<Unmap>", self._on_minimize)

        # Register rclone callbacks
        self._rclone.on_status_change = self._on_status_change
        self._rclone.on_file_synced = self._on_file_synced

        # Per-service Listbox widgets: service_name → tk.Listbox
        self._file_lists: Dict[str, tk.Listbox] = {}
        # Per-service status StringVars
        self._status_vars: Dict[str, tk.StringVar] = {}
        # Per-service toggle-button StringVars (Detener / Sincronizar)
        self._toggle_vars: Dict[str, tk.StringVar] = {}
        # Whether the tray icon has been started
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

        tk.Button(
            frame,
            text="➕ Agregar primer servicio",
            command=self._open_wizard,
            bg="#0078d4",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=6,
        ).pack()

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

        # Service name
        tk.Label(header, text=name, font=("Segoe UI", 11, "bold"), bg="#f0f4fa").grid(row=0, column=0, sticky="w", padx=(0, 20))

        # Platform
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

        # Button 2: Stop / Start sync (label reflects current running state)
        toggle_text = tk.StringVar(
            value="⏹ Detener" if self._rclone.is_running(name) else "▶ Sincronizar"
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
        )

    def _open_wizard(self) -> None:
        """Launch the add-new-service wizard."""
        from src.gui.setup_wizard import SetupWizard

        SetupWizard(
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

        Hide the window and show the tray icon instead.
        """
        # Only respond to the root window's Unmap event
        if event.widget is not self._root:
            return
        # Withdraw (hide) the window
        self._root.withdraw()
        # Start the tray icon if not yet running
        if not self._tray_started and self._tray.is_available():
            self._tray.start()
            self._tray_started = True

    def _restore_window(self) -> None:
        """Restore the main window from the tray (runs on tray thread → schedule on main)."""
        self._root.after(0, self._do_restore)

    def _do_restore(self) -> None:
        """Re-show and lift the main window."""
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _quit(self) -> None:
        """Stop all sync threads, remove tray icon, and destroy the window."""
        self._rclone.stop_all()
        self._tray.stop()
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
        # Also update the tray tooltip with aggregated status
        self._tray.update_tooltip(f"Rclone Manager – {service_name}: {status}")

    def _on_file_synced(self, service_name: str, file_path: str, synced: bool) -> None:
        """
        Invoked by RcloneManager when a file is transferred.
        Schedules a Listbox update on the main thread.
        """
        self._root.after(0, lambda: self._add_file_entry(service_name, file_path, synced))

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
        """Start all sync threads and enter the Tkinter main loop."""
        self._rclone.start_all()
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
