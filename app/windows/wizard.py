"""
New-service setup wizard (3 steps).

Step 1 – Choose local folder.
Step 2 – Choose cloud service type and give it a name.
Step 3 – Authenticate via browser OAuth and confirm.

Window size: 70 % of screen height × 60 % of screen width.
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, Optional

from app.config import AVAILABLE_SERVICES, AppConfig
from app.rclone_manager import RcloneManager
from app.utils import center_window


class NewServiceWizard(tk.Toplevel):
    """
    Three-step wizard for adding a new cloud-storage service.

    Call :meth:`open` on the class to instantiate and show the wizard.
    *on_finish* is invoked with the completed service config dict when
    the user successfully adds a service.
    """

    def __init__(
        self,
        parent: tk.Tk | tk.Toplevel,
        app_config: AppConfig,
        rclone: RcloneManager,
        on_finish: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        """Initialise the wizard and show the first step."""
        super().__init__(parent)
        self.app_config = app_config
        self.rclone = rclone
        self.on_finish = on_finish

        # Wizard state shared across steps
        self._local_path: str = ""
        self._service_type: str = ""
        self._service_name: str = ""
        self._display_name: str = ""
        self._auth_process = None

        # Prevent interaction with parent while open
        self.grab_set()
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Size: 60 % wide × 70 % tall
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = int(sw * 0.60)
        h = int(sh * 0.70)
        center_window(self, w, h)
        self._win_w = w
        self._win_h = h

        # Container that holds the step frames (only one visible at a time)
        self._container = tk.Frame(self, bg="#f5f5f5")
        self._container.pack(fill=tk.BOTH, expand=True)

        self._show_step1()

    # ------------------------------------------------------------------
    # Step 1 – Choose local folder
    # ------------------------------------------------------------------

    def _show_step1(self) -> None:
        """Render Step 1: local directory selection."""
        self._clear_container()
        self.title("Nuevo Servicio – Paso 1 de 3: Carpeta Local")

        frame = tk.Frame(self._container, bg="#f5f5f5", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # Title label
        tk.Label(
            frame,
            text="Paso 1 de 3",
            bg="#f5f5f5",
            font=("TkDefaultFont", 9),
            fg="#888",
        ).pack(anchor=tk.W)

        tk.Label(
            frame,
            text="Seleccione la carpeta local",
            bg="#f5f5f5",
            font=("TkDefaultFont", 14, "bold"),
        ).pack(anchor=tk.W, pady=(4, 12))

        tk.Label(
            frame,
            text=(
                "Elija el directorio de su equipo donde se guardarán los\n"
                "archivos sincronizados con el servicio en la nube."
            ),
            bg="#f5f5f5",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 20))

        # Directory row
        dir_frame = tk.Frame(frame, bg="#f5f5f5")
        dir_frame.pack(fill=tk.X, pady=(0, 10))

        self._path_var = tk.StringVar(value=self._local_path)
        tk.Label(dir_frame, text="Carpeta:", bg="#f5f5f5", width=10, anchor=tk.W).pack(
            side=tk.LEFT
        )
        tk.Entry(dir_frame, textvariable=self._path_var, width=50).pack(
            side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True
        )
        tk.Button(
            dir_frame,
            text="Examinar…",
            command=self._browse_folder,
        ).pack(side=tk.LEFT)

        # Spacer
        tk.Frame(frame, bg="#f5f5f5").pack(fill=tk.BOTH, expand=True)

        # Navigation buttons
        btn_frame = tk.Frame(frame, bg="#f5f5f5")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(btn_frame, text="Cancelar", command=self._on_cancel).pack(
            side=tk.LEFT
        )
        tk.Button(
            btn_frame,
            text="Siguiente →",
            command=self._step1_next,
        ).pack(side=tk.RIGHT)

    def _browse_folder(self) -> None:
        """Open a folder-chooser dialog and populate the path entry."""
        path = filedialog.askdirectory(
            parent=self,
            title="Seleccione la carpeta de sincronización",
        )
        if path:
            self._path_var.set(path)

    def _step1_next(self) -> None:
        """Validate step 1 and advance to step 2."""
        path = self._path_var.get().strip()
        if not path:
            messagebox.showwarning(
                "Campo requerido",
                "Por favor seleccione una carpeta.",
                parent=self,
            )
            return
        self._local_path = path
        self._show_step2()

    # ------------------------------------------------------------------
    # Step 2 – Choose service
    # ------------------------------------------------------------------

    def _show_step2(self) -> None:
        """Render Step 2: cloud service selection."""
        self._clear_container()
        self.title("Nuevo Servicio – Paso 2 de 3: Servicio en la Nube")

        frame = tk.Frame(self._container, bg="#f5f5f5", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Paso 2 de 3",
            bg="#f5f5f5",
            font=("TkDefaultFont", 9),
            fg="#888",
        ).pack(anchor=tk.W)

        tk.Label(
            frame,
            text="Seleccione el servicio",
            bg="#f5f5f5",
            font=("TkDefaultFont", 14, "bold"),
        ).pack(anchor=tk.W, pady=(4, 12))

        tk.Label(
            frame,
            text=(
                "Elija la plataforma de almacenamiento en la nube y\n"
                "asigne un nombre único a este servicio."
            ),
            bg="#f5f5f5",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 20))

        # Service type dropdown
        svc_frame = tk.Frame(frame, bg="#f5f5f5")
        svc_frame.pack(fill=tk.X, pady=4)
        tk.Label(svc_frame, text="Servicio:", bg="#f5f5f5", width=16, anchor=tk.W).pack(
            side=tk.LEFT
        )
        service_names = list(AVAILABLE_SERVICES.values())
        self._service_display_var = tk.StringVar(
            value=service_names[0] if service_names else ""
        )
        ttk.Combobox(
            svc_frame,
            textvariable=self._service_display_var,
            values=service_names,
            state="readonly",
            width=40,
        ).pack(side=tk.LEFT)

        # Name field
        name_frame = tk.Frame(frame, bg="#f5f5f5")
        name_frame.pack(fill=tk.X, pady=4)
        tk.Label(
            name_frame, text="Nombre interno:", bg="#f5f5f5", width=16, anchor=tk.W
        ).pack(side=tk.LEFT)
        self._name_var = tk.StringVar(value=self._service_name)
        tk.Entry(name_frame, textvariable=self._name_var, width=40).pack(side=tk.LEFT)
        tk.Label(
            name_frame, text="(sin espacios)", bg="#f5f5f5", fg="#888", font=("TkDefaultFont", 8)
        ).pack(side=tk.LEFT, padx=6)

        # Display-name field
        disp_frame = tk.Frame(frame, bg="#f5f5f5")
        disp_frame.pack(fill=tk.X, pady=4)
        tk.Label(
            disp_frame, text="Nombre para mostrar:", bg="#f5f5f5", width=16, anchor=tk.W
        ).pack(side=tk.LEFT)
        self._display_var = tk.StringVar(value=self._display_name)
        tk.Entry(disp_frame, textvariable=self._display_var, width=40).pack(
            side=tk.LEFT
        )

        tk.Frame(frame, bg="#f5f5f5").pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(frame, bg="#f5f5f5")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(btn_frame, text="Cancelar", command=self._on_cancel).pack(
            side=tk.LEFT
        )
        tk.Button(btn_frame, text="← Atrás", command=self._show_step1).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(
            btn_frame,
            text="Siguiente →",
            command=self._step2_next,
        ).pack(side=tk.RIGHT)

    def _step2_next(self) -> None:
        """Validate step 2 inputs and advance to step 3."""
        display_choice = self._service_display_var.get()
        name = self._name_var.get().strip().replace(" ", "_")
        disp = self._display_var.get().strip()

        if not name:
            messagebox.showwarning(
                "Campo requerido",
                "Por favor ingrese un nombre interno para el servicio.",
                parent=self,
            )
            return

        # Resolve rclone type from display name
        service_type = next(
            (k for k, v in AVAILABLE_SERVICES.items() if v == display_choice),
            "onedrive",
        )

        # Check for name collision
        if self.app_config.get_service(name):
            messagebox.showwarning(
                "Nombre en uso",
                f"Ya existe un servicio llamado '{name}'. Elija otro nombre.",
                parent=self,
            )
            return

        self._service_type = service_type
        self._service_name = name
        self._display_name = disp or display_choice
        self._show_step3()

    # ------------------------------------------------------------------
    # Step 3 – Authentication
    # ------------------------------------------------------------------

    def _show_step3(self) -> None:
        """Render Step 3: browser-based OAuth confirmation."""
        self._clear_container()
        self.title("Nuevo Servicio – Paso 3 de 3: Autenticación")

        frame = tk.Frame(self._container, bg="#f5f5f5", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Paso 3 de 3",
            bg="#f5f5f5",
            font=("TkDefaultFont", 9),
            fg="#888",
        ).pack(anchor=tk.W)

        tk.Label(
            frame,
            text="Autenticar servicio",
            bg="#f5f5f5",
            font=("TkDefaultFont", 14, "bold"),
        ).pack(anchor=tk.W, pady=(4, 12))

        # Summary of chosen options
        summary = (
            f"  Servicio  : {AVAILABLE_SERVICES.get(self._service_type, self._service_type)}\n"
            f"  Nombre    : {self._service_name}\n"
            f"  Carpeta   : {self._local_path}"
        )
        tk.Label(
            frame,
            text=summary,
            bg="#f0f0f0",
            justify=tk.LEFT,
            relief=tk.SUNKEN,
            padx=10,
            pady=8,
        ).pack(fill=tk.X, pady=(0, 20))

        tk.Label(
            frame,
            text=(
                "Haga clic en 'Sincronizar Sesión' para abrir el navegador\n"
                "y autenticarse en la plataforma seleccionada.\n\n"
                "Espere a que el proceso finalice antes de continuar."
            ),
            bg="#f5f5f5",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 16))

        # Status label
        self._auth_status_var = tk.StringVar(value="")
        self._auth_status_lbl = tk.Label(
            frame,
            textvariable=self._auth_status_var,
            bg="#f5f5f5",
            fg="#007bff",
        )
        self._auth_status_lbl.pack(anchor=tk.W, pady=(0, 8))

        # Big auth button
        self._sync_btn = tk.Button(
            frame,
            text="🔐  Sincronizar Sesión",
            font=("TkDefaultFont", 12),
            bg="#0078d4",
            fg="white",
            activebackground="#005a9e",
            activeforeground="white",
            padx=20,
            pady=10,
            command=self._start_auth,
        )
        self._sync_btn.pack(pady=10)

        tk.Frame(frame, bg="#f5f5f5").pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(frame, bg="#f5f5f5")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(btn_frame, text="Cancelar", command=self._on_cancel).pack(
            side=tk.LEFT
        )
        tk.Button(btn_frame, text="← Atrás", command=self._show_step2).pack(
            side=tk.LEFT, padx=4
        )
        self._finish_btn = tk.Button(
            btn_frame,
            text="Finalizar ✓",
            state=tk.DISABLED,
            command=self._finish,
        )
        self._finish_btn.pack(side=tk.RIGHT)

    def _start_auth(self) -> None:
        """
        Create the rclone remote and launch the OAuth browser flow.

        Disables the auth button while waiting for the process to complete
        then re-enables the Finish button on success.
        """
        self._sync_btn.config(state=tk.DISABLED)
        self._auth_status_var.set("Creando configuración en rclone…")

        def _run() -> None:
            """Background thread: create remote then authenticate."""
            # Create the remote entry in rclone config
            created = self.rclone.create_remote(
                self._service_name, self._service_type
            )
            if not created:
                self.after(
                    0,
                    lambda: self._auth_failed(
                        "No se pudo crear el remoto en rclone. "
                        "¿Está rclone instalado?"
                    ),
                )
                return

            # Update UI to show browser is opening
            self.after(
                0,
                lambda: self._auth_status_var.set(
                    "Abriendo navegador para autenticación…"
                ),
            )

            # Launch OAuth and wait for completion
            self.rclone.authenticate(
                self._service_name,
                on_complete=self._on_auth_complete,
            )

        threading.Thread(target=_run, daemon=True).start()

    def _on_auth_complete(self, success: bool) -> None:
        """Called from the auth thread when the OAuth process finishes."""
        # Marshal back to UI thread
        self.after(0, lambda: self._handle_auth_result(success))

    def _handle_auth_result(self, success: bool) -> None:
        """Update the UI after authentication completes."""
        if success:
            self._auth_status_var.set("✅ Autenticación exitosa. Puede finalizar.")
            self._auth_status_lbl.config(fg="#28a745")
            self._finish_btn.config(state=tk.NORMAL)
        else:
            self._auth_failed("La autenticación falló o fue cancelada.")

    def _auth_failed(self, msg: str) -> None:
        """Show an error and re-enable the auth button."""
        self._auth_status_var.set(f"❌ {msg}")
        self._auth_status_lbl.config(fg="#dc3545")
        self._sync_btn.config(state=tk.NORMAL)

    # ------------------------------------------------------------------
    # Finish / cancel
    # ------------------------------------------------------------------

    def _finish(self) -> None:
        """Build the service config, persist it and close the wizard."""
        service = self.app_config.create_service_config(
            name=self._service_name,
            service_type=self._service_type,
            local_path=self._local_path,
            display_name=self._display_name,
        )
        added = self.app_config.add_service(service)
        if not added:
            messagebox.showerror(
                "Error",
                "No se pudo agregar el servicio (nombre duplicado).",
                parent=self,
            )
            return

        messagebox.showinfo(
            "Servicio agregado",
            f"El servicio '{self._display_name}' fue agregado exitosamente.",
            parent=self,
        )
        self.grab_release()
        self.destroy()
        if self.on_finish:
            self.on_finish(service)

    def _on_cancel(self) -> None:
        """Close the wizard without saving."""
        self.grab_release()
        self.destroy()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_container(self) -> None:
        """Remove all children from the main container frame."""
        for widget in self._container.winfo_children():
            widget.destroy()
