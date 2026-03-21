"""
Setup wizard for adding a new cloud service.

Presents three (or four) sequential windows:
  Step 1 – Choose the local directory for the new service.
  Step 2 – Choose the cloud platform.
  Step 2.5 – Choose the sync provider (rclone vs. nativo) for OneDrive / Google Drive.
  Step 3 – Authenticate and confirm.

Window size: 70 % of screen height × 30 % of screen width.
"""

import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional

from src.config.config_manager import (
    PLATFORM_LABELS,
    SUPPORTED_PLATFORMS,
    NATIVE_SYNC_PLATFORMS,
    SYNC_PROVIDERS,
    ConfigManager,
)
from src.rclone.rclone_manager import RcloneManager
from src.native.native_sync_manager import NativeSyncManager

# Maximum seconds to wait for OAuth to complete (browser login + token write).
_OAUTH_TIMEOUT_SECONDS = 120


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
        # Chosen sync provider: "rclone" (default) or "nativo"
        self._sync_provider: str = "rclone"
        # NativeSyncManager used when provider is "nativo"
        self._native: NativeSyncManager = NativeSyncManager(config_manager)

        # Create the wizard top-level window
        if parent:
            self._root = tk.Toplevel(parent)
        else:
            self._root = tk.Tk()

        self._root.title("Agregar nuevo servicio")
        self._root.resizable(False, False)
        _center_window(self._root, height_pct=0.70, width_pct=0.30)

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

        # Row with path entry and Browse/New-folder buttons
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

        tk.Button(
            path_frame,
            text="📁 Nueva carpeta",
            command=self._create_subfolder,
        ).pack(side=tk.LEFT, padx=(4, 0))

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
            parent=self._root,
        )
        if folder:
            self._path_var.set(folder)

    def _create_subfolder(self) -> None:
        """Create a new subfolder inside the current path and update the entry."""
        parent_path = self._path_var.get().strip() or os.path.expanduser("~")
        name = simpledialog.askstring(
            "Nueva carpeta",
            "Nombre de la nueva carpeta:",
            parent=self._root,
        )
        if not name:
            return
        new_path = os.path.join(parent_path, name)
        try:
            os.makedirs(new_path, exist_ok=True)
            self._path_var.set(new_path)
        except OSError as exc:
            messagebox.showerror(
                "Error al crear carpeta",
                f"No se pudo crear la carpeta:\n{exc}",
                parent=self._root,
            )

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

        # Create the folder if it doesn't exist yet
        if not os.path.exists(path):
            if messagebox.askyesno(
                "Crear carpeta",
                f"La carpeta '{path}' no existe.\n¿Deseas crearla ahora?",
                parent=self._root,
            ):
                try:
                    os.makedirs(path, exist_ok=True)
                except OSError as exc:
                    messagebox.showerror(
                        "Error al crear carpeta",
                        f"No se pudo crear la carpeta:\n{exc}",
                        parent=self._root,
                    )
                    return
            else:
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
        """Validate step 2 input and advance to step 2.5 (provider) or step 3."""
        selection = self._platform_listbox.curselection()
        if not selection:
            messagebox.showwarning("Selección requerida", "Por favor selecciona una plataforma.", parent=self._root)
            return
        self._platform = SUPPORTED_PLATFORMS[selection[0]]
        # Show provider selection for platforms that support the native API
        if self._platform in NATIVE_SYNC_PLATFORMS:
            self._show_step2_5()
        else:
            self._sync_provider = "rclone"
            self._show_step3()

    # ------------------------------------------------------------------
    # Step 2.5 – Choose sync provider (only for OneDrive / Google Drive)
    # ------------------------------------------------------------------

    def _show_step2_5(self) -> None:
        """Render step 2.5: ask the user to choose a sync provider."""
        self._clear_frame()

        platform_label = PLATFORM_LABELS.get(self._platform, self._platform)

        tk.Label(
            self._frame,
            text="Paso 2.5 de 3 – Proveedor de sincronización",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            self._frame,
            text=(
                f"Elige cómo deseas sincronizar los datos con {platform_label}.\n\n"
                "• rclone (predeterminado): utiliza rclone bisync para una "
                "sincronización bidireccional robusta y probada.\n\n"
                "• Nativo (API directa): utiliza la API REST oficial de "
                f"{platform_label} sin necesidad de rclone."
            ),
            wraplength=530,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        self._provider_var = tk.StringVar(value="rclone")

        provider_frame = tk.Frame(self._frame)
        provider_frame.pack(fill=tk.X, pady=5)

        tk.Radiobutton(
            provider_frame,
            text="rclone (predeterminado)",
            variable=self._provider_var,
            value="rclone",
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=3)

        tk.Radiobutton(
            provider_frame,
            text="Nativo (API directa)",
            variable=self._provider_var,
            value="nativo",
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=3)

        nav = tk.Frame(self._frame)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))

        tk.Button(nav, text="← Atrás", command=self._show_step2).pack(side=tk.LEFT)
        tk.Button(nav, text="Siguiente →", command=self._validate_step2_5).pack(side=tk.RIGHT)

    def _validate_step2_5(self) -> None:
        """Save the provider choice and advance to step 3."""
        self._sync_provider = self._provider_var.get()
        self._show_step3()

    # ------------------------------------------------------------------
    # Step 3 – Authenticate and confirm
    # ------------------------------------------------------------------

    def _show_step3(self) -> None:
        """Render step 3: authenticate with the cloud provider.

        For Mega the authentication is credential-based (email + password).
        For native provider it uses the direct API OAuth flow.
        For every other platform it is OAuth via rclone / the system browser.
        """
        if self._platform == "mega":
            self._show_step3_mega()
        elif self._sync_provider == "nativo":
            self._show_step3_native()
        else:
            self._show_step3_oauth()

    def _show_step3_oauth(self) -> None:
        """Render step 3 for OAuth-based platforms (Google Drive, OneDrive, …)."""
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

    def _show_step3_native(self) -> None:
        """Render step 3 for the native (direct API) provider."""
        self._clear_frame()

        platform_label = PLATFORM_LABELS.get(self._platform, self._platform)

        tk.Label(
            self._frame,
            text="Paso 3 de 3 – Autenticación nativa",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            self._frame,
            text=(
                f"Haz clic en '🔑 Autenticar con {platform_label}' para abrir "
                "el navegador e iniciar sesión.\n\n"
                "La aplicación usará la API directa de "
                f"{platform_label} (sin rclone).\n"
                "Espera a que la autenticación se complete antes de continuar."
            ),
            wraplength=530,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        # Summary box
        summary_frame = tk.LabelFrame(
            self._frame, text="Resumen de configuración", padx=10, pady=10
        )
        summary_frame.pack(fill=tk.X, pady=10)

        tk.Label(summary_frame, text=f"Nombre: {self._service_name}", anchor="w").pack(anchor="w")
        tk.Label(summary_frame, text=f"Plataforma: {platform_label}", anchor="w").pack(anchor="w")
        tk.Label(summary_frame, text=f"Proveedor: Nativo (API directa)", anchor="w").pack(anchor="w")
        tk.Label(
            summary_frame,
            text=f"Carpeta local: {self._local_path}",
            anchor="w",
            wraplength=500,
        ).pack(anchor="w")

        self._auth_status_var = tk.StringVar(value="Estado: esperando autenticación…")
        tk.Label(
            self._frame,
            textvariable=self._auth_status_var,
            fg="gray",
            font=("Segoe UI", 10, "italic"),
            wraplength=530,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        nav = tk.Frame(self._frame)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))

        back_target = self._show_step2_5 if self._platform in NATIVE_SYNC_PLATFORMS else self._show_step2
        tk.Button(nav, text="← Atrás", command=back_target).pack(side=tk.LEFT)

        self._sync_btn = tk.Button(
            nav,
            text=f"🔑 Autenticar con {platform_label}",
            command=self._start_native_auth,
            bg="#0078d4",
            fg="white",
            font=("Segoe UI", 10, "bold"),
        )
        self._sync_btn.pack(side=tk.RIGHT)

    def _start_native_auth(self) -> None:
        """Launch the native OAuth flow in a background thread."""
        self._sync_btn.configure(state=tk.DISABLED, text="Autenticando…")
        self._auth_status_var.set("Estado: abriendo el navegador para autenticación nativa…")

        remote_name = self._service_name.lower().replace(" ", "_")

        def on_done(success: bool, error_msg: str) -> None:
            if success:
                self._root.after(0, self._native_auth_success, remote_name)
            else:
                self._root.after(0, self._native_auth_failed, error_msg)

        self._native.authenticate(
            service_name=self._service_name,
            platform=self._platform,
            remote_name=remote_name,
            on_done=on_done,
            timeout=float(_OAUTH_TIMEOUT_SECONDS),
        )

    def _native_auth_success(self, remote_name: str) -> None:
        """Called on the main thread after successful native authentication."""
        self._auth_status_var.set("✅ Autenticación nativa completada correctamente.")
        self._root.after(1500, lambda: self._finish_native(remote_name))

    def _native_auth_failed(self, error_msg: str) -> None:
        """Called on the main thread if native authentication failed."""
        platform_label = PLATFORM_LABELS.get(self._platform, self._platform)
        display = f"❌ Error: {error_msg}" if error_msg else "❌ La autenticación falló. Intenta de nuevo."
        self._auth_status_var.set(display)
        self._sync_btn.configure(
            state=tk.NORMAL,
            text=f"🔑 Autenticar con {platform_label}",
        )

    def _finish_native(self, remote_name: str) -> None:
        """Save the native service and close the wizard."""
        svc = self._config.add_service(
            name=self._service_name,
            platform=self._platform,
            local_path=self._local_path,
        )
        self._config.update_service(
            self._service_name,
            {
                "remote_name": remote_name,
                "sync_provider": "nativo",
            },
        )
        self._root.destroy()
        if self._on_complete:
            self._on_complete(self._service_name)

    def _show_step3_mega(self) -> None:
        """Render step 3 for Mega: collect email and password credentials."""
        self._clear_frame()

        tk.Label(
            self._frame,
            text="Paso 3 de 3 – Credenciales de Mega",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            self._frame,
            text=(
                "Mega requiere tu dirección de correo electrónico y contraseña "
                "para acceder a tu cuenta. Estos datos se almacenan de forma "
                "segura en el fichero de configuración local de rclone."
            ),
            wraplength=550,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        # Summary box
        summary_frame = tk.LabelFrame(self._frame, text="Resumen de configuración", padx=10, pady=10)
        summary_frame.pack(fill=tk.X, pady=10)

        tk.Label(summary_frame, text=f"Nombre: {self._service_name}", anchor="w").pack(anchor="w")
        tk.Label(summary_frame, text="Plataforma: Mega", anchor="w").pack(anchor="w")
        tk.Label(summary_frame, text=f"Carpeta local: {self._local_path}", anchor="w", wraplength=500).pack(anchor="w")

        # Credentials form
        creds_frame = tk.Frame(self._frame)
        creds_frame.pack(fill=tk.X, pady=(15, 0))

        tk.Label(creds_frame, text="Correo electrónico (usuario):", anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 5)
        )
        self._mega_user_var = tk.StringVar()
        tk.Entry(creds_frame, textvariable=self._mega_user_var, width=40).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 5)
        )

        tk.Label(creds_frame, text="Contraseña:", anchor="w").grid(
            row=1, column=0, sticky="w"
        )
        self._mega_pass_var = tk.StringVar()
        tk.Entry(creds_frame, textvariable=self._mega_pass_var, show="*", width=40).grid(
            row=1, column=1, sticky="ew", padx=(8, 0)
        )
        creds_frame.columnconfigure(1, weight=1)

        # Status label — wraplength allows long rclone error lines to wrap.
        self._auth_status_var = tk.StringVar(value="")
        tk.Label(
            self._frame,
            textvariable=self._auth_status_var,
            fg="gray",
            font=("Segoe UI", 10, "italic"),
            wraplength=550,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        nav = tk.Frame(self._frame)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))

        tk.Button(nav, text="← Atrás", command=self._show_step2).pack(side=tk.LEFT)

        self._sync_btn = tk.Button(
            nav,
            text="✅ Crear configuración",
            command=self._start_mega_auth,
            bg="#d4003f",
            fg="white",
            font=("Segoe UI", 10, "bold"),
        )
        self._sync_btn.pack(side=tk.RIGHT)

    def _start_mega_auth(self) -> None:
        """Validate credentials and create the Mega rclone remote."""
        user = self._mega_user_var.get().strip()
        password = self._mega_pass_var.get()

        if not user:
            messagebox.showwarning(
                "Campo requerido",
                "Por favor escribe tu dirección de correo electrónico.",
                parent=self._root,
            )
            return
        if not password:
            messagebox.showwarning(
                "Campo requerido",
                "Por favor escribe tu contraseña.",
                parent=self._root,
            )
            return

        self._sync_btn.configure(state=tk.DISABLED, text="Configurando…")
        self._auth_status_var.set("Estado: creando configuración de Mega…")

        remote_name = self._service_name.lower().replace(" ", "_")

        def run_create() -> None:
            ok, error_msg = self._rclone.create_mega_remote(remote_name, user, password)
            if ok:
                self._root.after(0, self._auth_success, remote_name)
            else:
                self._root.after(0, self._mega_auth_failed, error_msg)

        threading.Thread(target=run_create, daemon=True).start()

    def _start_auth(self) -> None:
        """Launch rclone auth in a background thread, opening the browser."""
        self._sync_btn.configure(state=tk.DISABLED, text="Autenticando…")
        self._auth_status_var.set("Estado: abriendo el navegador para autenticación…")

        remote_name = self._service_name.lower().replace(" ", "_")

        def run_auth() -> None:
            proc = self._rclone.open_browser_auth(remote_name, self._platform)

            # Keys that must be present in rclone.conf before we consider auth
            # complete.  OneDrive requires drive_id (written after token) so
            # that bisync has a fully-configured remote and does not fail with
            # exit-code 1 due to a missing drive_id/drive_type.
            extra_keys: "tuple[str, ...]" = (
                ("drive_id",) if self._platform == "onedrive" else ()
            )

            # Wait up to _OAUTH_TIMEOUT_SECONDS for either:
            #   a) rclone to exit on its own (normal case), or
            #   b) the token (plus any provider-specific extra keys) to appear in
            #      rclone.conf (handles OneDrive where rclone can hang on a
            #      post-OAuth drive-selection prompt even after the browser shows
            #      "success").
            deadline = time.monotonic() + _OAUTH_TIMEOUT_SECONDS
            success = False
            while time.monotonic() < deadline:
                ret = proc.poll()
                if ret is not None:
                    success = ret == 0
                    break
                if self._rclone.remote_has_token(remote_name, extra_required_keys=extra_keys):
                    # OAuth token (and required extra keys) already written —
                    # give rclone a brief moment to flush/close the config file
                    # before terminating.
                    time.sleep(0.5)
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    success = True
                    break
                time.sleep(1)
            else:
                # Timed out — kill the stalled process.
                try:
                    proc.terminate()
                except OSError:
                    pass

            if success:
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
        """Called on the main thread if OAuth authentication failed."""
        self._auth_status_var.set("❌ La autenticación falló. Intenta de nuevo.")
        self._sync_btn.configure(state=tk.NORMAL, text="🔑 Sincronizar sesión")

    def _mega_auth_failed(self, error_msg: str) -> None:
        """Called on the main thread if Mega credential setup failed."""
        display = f"❌ Error: {error_msg}" if error_msg else "❌ La configuración falló. Intenta de nuevo."
        self._auth_status_var.set(display)
        self._sync_btn.configure(state=tk.NORMAL, text="✅ Crear configuración")

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
