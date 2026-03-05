"""
Setup wizard for adding a new cloud service.

Presents three sequential windows:
  Step 1 – Choose the local directory for the new service.
  Step 2 – Choose the cloud platform.
  Step 3 – Authenticate and confirm.

Window size: 70 % of screen height × 60 % of screen width.
"""

import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from src.config.config_manager import PLATFORM_LABELS, SUPPORTED_PLATFORMS, ConfigManager
from src.rclone.rclone_manager import RcloneManager


class SetupWizard:
    """
    Three-step wizard that guides the user through adding a new service.

    After the third step completes successfully, `on_complete` is called
    with the name of the newly created service.
    """

    def __init__(
        self,
        parent: Optional[tk.Tk],
        config_manager: ConfigManager,
        rclone_manager: RcloneManager,
        on_complete: Optional[Callable[[str], None]] = None,
    ) -> None:
        # Shared state collected across the three steps
        self._config = config_manager
        self._rclone = rclone_manager
        self._on_complete = on_complete

        # Data gathered from the user during the wizard
        self._local_path: str = ""
        self._platform: str = ""
        self._service_name: str = ""

        # Create the wizard top-level window
        if parent:
            self._root = tk.Toplevel(parent)
        else:
            self._root = tk.Tk()

        self._root.title("Agregar nuevo servicio")
        self._root.resizable(False, False)
        _center_window(self._root, height_pct=0.70, width_pct=0.60)

        # Container that holds the content of each step
        self._frame = tk.Frame(self._root)
        self._frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        self._show_step1()

    # ------------------------------------------------------------------
    # Step 1 – Choose local directory
    # ------------------------------------------------------------------

    def _show_step1(self) -> None:
        """Render step 1: ask the user to choose a local sync folder."""
        self._clear_frame()

        tk.Label(
            self._frame,
            text="Paso 1 de 3 – Carpeta de sincronización",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            self._frame,
            text=(
                "Elige la carpeta local donde se sincronizarán los archivos "
                "de este servicio."
            ),
            wraplength=550,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        # Row with path entry and Browse button
        path_frame = tk.Frame(self._frame)
        path_frame.pack(fill=tk.X, pady=5)

        self._path_var = tk.StringVar(value=os.path.expanduser("~"))
        path_entry = tk.Entry(path_frame, textvariable=self._path_var, width=55)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Button(
            path_frame,
            text="Examinar…",
            command=self._browse_folder,
        ).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(
            self._frame,
            text="Nombre del servicio:",
            anchor="w",
        ).pack(anchor="w", pady=(15, 3))

        self._name_var = tk.StringVar()
        tk.Entry(self._frame, textvariable=self._name_var, width=40).pack(anchor="w")

        # Navigation buttons at bottom
        nav = tk.Frame(self._frame)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))

        tk.Button(
            nav,
            text="Cancelar",
            command=self._root.destroy,
        ).pack(side=tk.LEFT)

        tk.Button(
            nav,
            text="Siguiente →",
            command=self._validate_step1,
        ).pack(side=tk.RIGHT)

    def _browse_folder(self) -> None:
        """Open a folder picker dialog and update the path entry."""
        folder = filedialog.askdirectory(
            title="Seleccionar carpeta de sincronización",
            initialdir=self._path_var.get(),
        )
        if folder:
            self._path_var.set(folder)

    def _validate_step1(self) -> None:
        """Validate step 1 inputs and advance to step 2."""
        path = self._path_var.get().strip()
        name = self._name_var.get().strip()

        if not path:
            messagebox.showwarning("Campo requerido", "Por favor selecciona una carpeta.", parent=self._root)
            return
        if not name:
            messagebox.showwarning("Campo requerido", "Por favor escribe un nombre para el servicio.", parent=self._root)
            return
        # Check for duplicate service names
        if self._config.get_service(name) is not None:
            messagebox.showwarning(
                "Nombre duplicado",
                f"Ya existe un servicio con el nombre '{name}'.\nElige un nombre diferente.",
                parent=self._root,
            )
            return

        self._local_path = path
        self._service_name = name
        self._show_step2()

    # ------------------------------------------------------------------
    # Step 2 – Choose cloud platform
    # ------------------------------------------------------------------

    def _show_step2(self) -> None:
        """Render step 2: ask the user to pick the cloud platform."""
        self._clear_frame()

        tk.Label(
            self._frame,
            text="Paso 2 de 3 – Plataforma de almacenamiento",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            self._frame,
            text="Selecciona la plataforma de la que deseas sincronizar los datos:",
            wraplength=550,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        # Listbox with all supported platforms
        list_frame = tk.Frame(self._frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._platform_listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            font=("Segoe UI", 11),
            selectmode=tk.SINGLE,
            activestyle="none",
        )
        self._platform_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._platform_listbox.yview)

        for key in SUPPORTED_PLATFORMS:
            label = PLATFORM_LABELS.get(key, key)
            self._platform_listbox.insert(tk.END, f"  {label}")

        # Pre-select first item
        self._platform_listbox.selection_set(0)

        nav = tk.Frame(self._frame)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))

        tk.Button(nav, text="← Atrás", command=self._show_step1).pack(side=tk.LEFT)
        tk.Button(nav, text="Siguiente →", command=self._validate_step2).pack(side=tk.RIGHT)

    def _validate_step2(self) -> None:
        """Validate step 2 input and advance to step 3."""
        selection = self._platform_listbox.curselection()
        if not selection:
            messagebox.showwarning("Selección requerida", "Por favor selecciona una plataforma.", parent=self._root)
            return
        self._platform = SUPPORTED_PLATFORMS[selection[0]]
        self._show_step3()

    # ------------------------------------------------------------------
    # Step 3 – Authenticate and confirm
    # ------------------------------------------------------------------

    def _show_step3(self) -> None:
        """Render step 3: launch OAuth browser session and wait for token."""
        self._clear_frame()

        platform_label = PLATFORM_LABELS.get(self._platform, self._platform)

        tk.Label(
            self._frame,
            text="Paso 3 de 3 – Autenticación",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            self._frame,
            text=(
                f"Haz clic en '🔑 Sincronizar sesión' para abrir el navegador "
                f"e iniciar sesión en {platform_label}.\n\n"
                "Espera a que la autenticación se complete antes de continuar."
            ),
            wraplength=550,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        # Summary box
        summary_frame = tk.LabelFrame(self._frame, text="Resumen de configuración", padx=10, pady=10)
        summary_frame.pack(fill=tk.X, pady=10)

        tk.Label(summary_frame, text=f"Nombre: {self._service_name}", anchor="w").pack(anchor="w")
        tk.Label(summary_frame, text=f"Plataforma: {platform_label}", anchor="w").pack(anchor="w")
        tk.Label(summary_frame, text=f"Carpeta local: {self._local_path}", anchor="w", wraplength=500).pack(anchor="w")

        # Status label updated during auth
        self._auth_status_var = tk.StringVar(value="Estado: esperando autenticación…")
        tk.Label(
            self._frame,
            textvariable=self._auth_status_var,
            fg="gray",
            font=("Segoe UI", 10, "italic"),
        ).pack(anchor="w", pady=(10, 0))

        nav = tk.Frame(self._frame)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))

        tk.Button(nav, text="← Atrás", command=self._show_step2).pack(side=tk.LEFT)

        self._sync_btn = tk.Button(
            nav,
            text="🔑 Sincronizar sesión",
            command=self._start_auth,
            bg="#0078d4",
            fg="white",
            font=("Segoe UI", 10, "bold"),
        )
        self._sync_btn.pack(side=tk.RIGHT)

    def _start_auth(self) -> None:
        """Launch rclone auth in a background thread, opening the browser."""
        self._sync_btn.configure(state=tk.DISABLED, text="Autenticando…")
        self._auth_status_var.set("Estado: abriendo el navegador para autenticación…")

        remote_name = self._service_name.lower().replace(" ", "_")

        def run_auth() -> None:
            # Run rclone config create which opens the browser for OAuth
            proc = self._rclone.open_browser_auth(remote_name, self._platform)
            proc.wait()
            if proc.returncode == 0:
                # Schedule success handling on the main thread
                self._root.after(0, self._auth_success, remote_name)
            else:
                self._root.after(0, self._auth_failed)

        threading.Thread(target=run_auth, daemon=True).start()

    def _auth_success(self, remote_name: str) -> None:
        """Called on the main thread after successful authentication."""
        self._auth_status_var.set("✅ Autenticación completada correctamente.")
        # Briefly show the confirmation message before finishing
        self._root.after(1500, lambda: self._finish(remote_name))

    def _auth_failed(self) -> None:
        """Called on the main thread if authentication failed."""
        self._auth_status_var.set("❌ La autenticación falló. Intenta de nuevo.")
        self._sync_btn.configure(state=tk.NORMAL, text="🔑 Sincronizar sesión")

    def _finish(self, remote_name: str) -> None:
        """Save the new service and close the wizard."""
        # Add the service to the config
        svc = self._config.add_service(
            name=self._service_name,
            platform=self._platform,
            local_path=self._local_path,
        )
        # Update the remote name to what was used for rclone config
        self._config.update_service(self._service_name, {"remote_name": remote_name})

        self._root.destroy()

        if self._on_complete:
            self._on_complete(self._service_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_frame(self) -> None:
        """Remove all widgets from the main content frame."""
        for widget in self._frame.winfo_children():
            widget.destroy()

    def show(self) -> None:
        """Start the Tkinter event loop (used when wizard is the root window)."""
        self._root.mainloop()


# ------------------------------------------------------------------
# Window helpers
# ------------------------------------------------------------------

def _center_window(window: tk.Wm, height_pct: float, width_pct: float) -> None:
    """
    Resize `window` to a percentage of the screen and center it.

    Args:
        window: The Tk or Toplevel window to resize.
        height_pct: Fraction of screen height (0.0 – 1.0).
        width_pct: Fraction of screen width (0.0 – 1.0).
    """
    window.update_idletasks()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()

    win_w = int(screen_w * width_pct)
    win_h = int(screen_h * height_pct)

    x = (screen_w - win_w) // 2
    y = (screen_h - win_h) // 2

    window.geometry(f"{win_w}x{win_h}+{x}+{y}")
