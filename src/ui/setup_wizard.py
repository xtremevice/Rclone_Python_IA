"""
setup_wizard.py
---------------
Three-step wizard for adding a new rclone sync service.

Step 1 – Choose the local directory for the service.
Step 2 – Choose the cloud platform/provider.
Step 3 – Authorise the session via browser OAuth; wait for token; confirm.

Window size: 70% screen height × 60% screen width.
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.core.rclone_manager import RcloneManager, SUPPORTED_PLATFORMS
from src.ui.utils import (
    COLORS,
    apply_theme,
    center_window,
    platform_display_name,
    set_window_size_percent,
)


class SetupWizard(tk.Toplevel):
    """
    Modal wizard window that walks the user through creating a new service.
    On completion, calls on_complete(name, platform, local_path, remote_name, token).
    """

    def __init__(self, parent, on_complete=None):
        """
        Initialise the wizard.

        Parameters
        ----------
        parent      : Parent tk window
        on_complete : Callback(name, platform, local_path, remote_name, token)
                      invoked after successful authorisation
        """
        super().__init__(parent)
        self.on_complete = on_complete
        self.rclone = RcloneManager()

        # Wizard state accumulated across steps
        self._local_path = tk.StringVar()
        self._platform_type = tk.StringVar(value="onedrive")
        self._service_name = tk.StringVar()
        self._token = None           # OAuth token received from rclone authorize
        self._auth_thread = None     # Background thread running rclone authorize

        # Window setup
        self.title("Agregar Nuevo Servicio")
        self.transient(parent)
        self.grab_set()              # Make modal
        self.resizable(False, False)
        set_window_size_percent(self, w_pct=0.60, h_pct=0.70)

        apply_theme(self)

        # Container that shows one step at a time
        self._container = ttk.Frame(self)
        self._container.pack(fill="both", expand=True)

        # Build all step frames (hidden until navigated to)
        self._step1_frame = self._build_step1()
        self._step2_frame = self._build_step2()
        self._step3_frame = self._build_step3()

        # Show the first step
        self._show_step(1)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _show_step(self, step: int):
        """Hide all step frames and show only the requested step number."""
        self._step1_frame.pack_forget()
        self._step2_frame.pack_forget()
        self._step3_frame.pack_forget()

        self._current_step = step
        frames = {1: self._step1_frame, 2: self._step2_frame, 3: self._step3_frame}
        frames[step].pack(fill="both", expand=True, padx=30, pady=30)

    # ------------------------------------------------------------------
    # Step 1 – Choose local directory
    # ------------------------------------------------------------------

    def _build_step1(self):
        """
        Build and return the Step 1 frame.
        Allows the user to select a local folder and give the service a name.
        """
        frame = ttk.Frame(self._container)

        # Step indicator
        ttk.Label(frame, text="Paso 1 de 3", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Seleccionar Directorio",
            style="Title.TLabel",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(0, 20))

        # Service name field
        ttk.Label(frame, text="Nombre del servicio:").pack(anchor="w")
        name_entry = ttk.Entry(frame, textvariable=self._service_name, width=50)
        name_entry.pack(anchor="w", pady=(4, 16))
        name_entry.insert(0, "Mi Servicio")

        # Local directory picker
        ttk.Label(frame, text="Carpeta local de sincronización:").pack(anchor="w")
        path_frame = ttk.Frame(frame)
        path_frame.pack(fill="x", pady=(4, 8))
        path_entry = ttk.Entry(path_frame, textvariable=self._local_path, width=45)
        path_entry.pack(side="left", expand=True, fill="x")
        ttk.Button(
            path_frame,
            text="Examinar…",
            style="Secondary.TButton",
            command=self._browse_directory,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            frame,
            text="Elige la carpeta donde los archivos del servicio serán sincronizados.",
            style="Subtitle.TLabel",
            wraplength=500,
        ).pack(anchor="w", pady=(0, 20))

        # Navigation buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(side="bottom", fill="x", pady=(20, 0))
        ttk.Button(
            btn_frame,
            text="Cancelar",
            style="Secondary.TButton",
            command=self.destroy,
        ).pack(side="left")
        ttk.Button(
            btn_frame,
            text="Siguiente →",
            style="Primary.TButton",
            command=self._step1_next,
        ).pack(side="right")

        return frame

    def _browse_directory(self):
        """Open a native folder selection dialog and populate the path field."""
        chosen = filedialog.askdirectory(
            title="Seleccionar carpeta de sincronización",
            initialdir=str(Path.home()),
        )
        if chosen:
            self._local_path.set(chosen)

    def _step1_next(self):
        """Validate Step 1 inputs and advance to Step 2."""
        if not self._service_name.get().strip():
            messagebox.showwarning(
                "Nombre requerido",
                "Por favor ingresa un nombre para el servicio.",
                parent=self,
            )
            return
        if not self._local_path.get().strip():
            messagebox.showwarning(
                "Directorio requerido",
                "Por favor selecciona una carpeta de sincronización.",
                parent=self,
            )
            return
        self._show_step(2)

    # ------------------------------------------------------------------
    # Step 2 – Choose cloud platform
    # ------------------------------------------------------------------

    def _build_step2(self):
        """
        Build and return the Step 2 frame.
        Presents a combobox with all supported rclone platforms.
        """
        frame = ttk.Frame(self._container)

        # Step indicator
        ttk.Label(frame, text="Paso 2 de 3", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Seleccionar Plataforma",
            style="Title.TLabel",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(0, 20))

        ttk.Label(frame, text="Plataforma de almacenamiento en la nube:").pack(anchor="w")

        # Map display names → rclone type keys for the combobox
        self._platform_display_map = {v: k for k, v in SUPPORTED_PLATFORMS.items()}
        display_names = list(SUPPORTED_PLATFORMS.values())

        platform_combo = ttk.Combobox(
            frame,
            values=display_names,
            state="readonly",
            width=40,
        )
        platform_combo.set(SUPPORTED_PLATFORMS["onedrive"])
        platform_combo.pack(anchor="w", pady=(4, 16))

        def _on_platform_changed(event):
            """Update internal platform type variable when selection changes."""
            selected_display = platform_combo.get()
            self._platform_type.set(self._platform_display_map.get(selected_display, "onedrive"))

        platform_combo.bind("<<ComboboxSelected>>", _on_platform_changed)
        # Store reference for reading in next step
        self._platform_combo = platform_combo

        ttk.Label(
            frame,
            text="Selecciona el proveedor de nube donde están almacenados tus datos.",
            style="Subtitle.TLabel",
            wraplength=500,
        ).pack(anchor="w", pady=(0, 20))

        # Navigation buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(side="bottom", fill="x", pady=(20, 0))
        ttk.Button(
            btn_frame,
            text="← Atrás",
            style="Secondary.TButton",
            command=lambda: self._show_step(1),
        ).pack(side="left")
        ttk.Button(
            btn_frame,
            text="Siguiente →",
            style="Primary.TButton",
            command=self._step2_next,
        ).pack(side="right")

        return frame

    def _step2_next(self):
        """Read combobox selection and advance to Step 3."""
        selected_display = self._platform_combo.get()
        self._platform_type.set(
            self._platform_display_map.get(selected_display, "onedrive")
        )
        self._show_step(3)
        # Update step 3 summary labels now that platform is confirmed
        self._update_step3_summary()

    # ------------------------------------------------------------------
    # Step 3 – Authorise & confirm
    # ------------------------------------------------------------------

    def _build_step3(self):
        """
        Build and return the Step 3 frame.
        Shows a summary of choices and an Authorise button that opens the browser.
        """
        frame = ttk.Frame(self._container)

        # Step indicator
        ttk.Label(frame, text="Paso 3 de 3", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Autorizar Sesión",
            style="Title.TLabel",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(0, 20))

        # Summary labels (populated dynamically in _update_step3_summary)
        self._s3_service_lbl = ttk.Label(frame, text="")
        self._s3_service_lbl.pack(anchor="w", pady=2)
        self._s3_platform_lbl = ttk.Label(frame, text="")
        self._s3_platform_lbl.pack(anchor="w", pady=2)
        self._s3_path_lbl = ttk.Label(frame, text="", wraplength=480)
        self._s3_path_lbl.pack(anchor="w", pady=2)

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=16)

        # Status label shown during auth
        self._auth_status_var = tk.StringVar(
            value="Haz clic en 'Sincronizar Sesión' para autorizar el acceso."
        )
        self._auth_status_lbl = ttk.Label(
            frame,
            textvariable=self._auth_status_var,
            style="Subtitle.TLabel",
            wraplength=480,
        )
        self._auth_status_lbl.pack(anchor="w", pady=(0, 12))

        # Progress indicator (hidden until auth starts)
        self._auth_progress = ttk.Progressbar(frame, mode="indeterminate", length=300)
        self._auth_progress.pack(anchor="w", pady=(0, 16))
        self._auth_progress.pack_forget()  # Hide until needed

        # Authorise button
        self._auth_btn = ttk.Button(
            frame,
            text="🔐 Sincronizar Sesión",
            style="Primary.TButton",
            command=self._start_auth,
        )
        self._auth_btn.pack(anchor="w")

        # Navigation buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(side="bottom", fill="x", pady=(20, 0))
        ttk.Button(
            btn_frame,
            text="← Atrás",
            style="Secondary.TButton",
            command=lambda: self._show_step(2),
        ).pack(side="left")
        self._finish_btn = ttk.Button(
            btn_frame,
            text="✔ Finalizar",
            style="Primary.TButton",
            command=self._finish,
            state="disabled",   # Enabled after successful auth
        )
        self._finish_btn.pack(side="right")

        return frame

    def _update_step3_summary(self):
        """Refresh the summary labels on Step 3 with current wizard state."""
        self._s3_service_lbl.config(
            text=f"Servicio:   {self._service_name.get().strip()}"
        )
        self._s3_platform_lbl.config(
            text=f"Plataforma: {platform_display_name(self._platform_type.get())}"
        )
        self._s3_path_lbl.config(
            text=f"Directorio: {self._local_path.get().strip()}"
        )

    def _start_auth(self):
        """
        Disable the auth button, show progress, then launch rclone authorize
        in a background thread.  The browser will open automatically.
        """
        if not self.rclone.is_rclone_available():
            messagebox.showerror(
                "rclone no encontrado",
                "rclone no está instalado o no se encuentra en el PATH del sistema.\n"
                "Por favor instala rclone y vuelve a intentarlo.",
                parent=self,
            )
            return

        self._auth_btn.config(state="disabled")
        self._auth_status_var.set(
            "Se abrirá el navegador para autorizar el acceso. "
            "Completa la autenticación y regresa aquí."
        )
        # Show spinner
        self._auth_progress.pack(anchor="w", pady=(0, 16))
        self._auth_progress.start(12)

        # Run authorization in background thread
        self._auth_thread = self.rclone.authorize(
            platform_type=self._platform_type.get(),
            callback=self._on_auth_result,
        )

    def _on_auth_result(self, success: bool, token_or_error: str):
        """
        Called by the background auth thread when authorization completes.
        Schedules UI updates back on the main thread.
        """
        # Marshal back to the tkinter main thread using after()
        self.after(0, self._apply_auth_result, success, token_or_error)

    def _apply_auth_result(self, success: bool, token_or_error: str):
        """Update UI after the OAuth flow finishes (runs on main thread)."""
        self._auth_progress.stop()
        self._auth_progress.pack_forget()

        if success:
            # Store the token for use in _finish()
            self._token = token_or_error
            # Show brief success message
            self._auth_status_var.set("✔ Sesión autorizada correctamente. Haz clic en Finalizar.")
            self._auth_status_lbl.config(foreground=COLORS["success"])
            self._finish_btn.config(state="normal")
            # Flash a brief popup confirmation
            messagebox.showinfo(
                "Autorización exitosa",
                "¡El token fue recibido correctamente!\nYa puedes finalizar la configuración.",
                parent=self,
            )
        else:
            # Show the error and re-enable the auth button
            self._auth_status_var.set(f"✗ Error: {token_or_error}")
            self._auth_status_lbl.config(foreground=COLORS["error"])
            self._auth_btn.config(state="normal")

    def _finish(self):
        """
        Validate all wizard data and invoke the on_complete callback,
        then close the wizard.
        """
        name = self._service_name.get().strip()
        platform_type = self._platform_type.get()
        local_path = self._local_path.get().strip()

        if not self._token:
            messagebox.showwarning(
                "Autorización pendiente",
                "Debes completar la autorización antes de finalizar.",
                parent=self,
            )
            return

        # Generate a unique safe rclone remote name from the service name
        safe_name = "".join(c if c.isalnum() else "_" for c in name).lower()
        import uuid as _uuid
        remote_name = f"{safe_name}_{_uuid.uuid4().hex[:6]}"

        # Create the rclone remote config entry
        ok, err = self.rclone.create_remote(remote_name, platform_type, self._token)
        if not ok:
            # Warn but continue - user can fix rclone config manually
            messagebox.showwarning(
                "Advertencia",
                f"No se pudo crear la configuración rclone automáticamente:\n{err}\n\n"
                "El servicio se agregará de todas formas. "
                "Puedes configurar rclone manualmente si es necesario.",
                parent=self,
            )

        # Invoke the parent callback with the collected data
        if self.on_complete:
            self.on_complete(name, platform_type, local_path, remote_name, self._token)

        self.destroy()
