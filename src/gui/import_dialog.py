"""
Dialog for importing rclone remote configurations from external config files.

Allows users to:
  1. Browse for a rclone .conf file (including system / other-user paths).
  2. Optionally provide a sudo password if the file requires elevated access.
  3. Select one of the remotes found in that file.
  4. Choose a local service name and sync folder for the imported remote.
  5. Confirm the import, which copies the remote into the app's own rclone
     config and registers a new service entry in ConfigManager.
"""

import configparser
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Dict, Optional

from src.config.config_manager import ConfigManager
from src.rclone.rclone_manager import RcloneManager


class ImportConfigDialog:
    """Modal Toplevel dialog that guides the user through importing a remote."""

    def __init__(
        self,
        parent: tk.Misc,
        config_manager: ConfigManager,
        rclone_manager: RcloneManager,
        on_complete: Callable[[str], None],
    ) -> None:
        self._parent = parent
        self._config = config_manager
        self._rclone = rclone_manager
        self._on_complete = on_complete

        self._win = tk.Toplevel(parent)
        self._win.title("Importar configuración rclone")
        self._win.resizable(False, False)
        self._win.grab_set()  # modal

        # Map: remote_name → {key: value, ...} (includes the 'type' key)
        self._remotes: Dict[str, Dict[str, str]] = {}
        self._selected_remote: Optional[str] = None
        self._sudo_frame_visible = False

        self._build_ui()
        _center_toplevel(self._win, parent)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad: Dict = {"padx": 12, "pady": 6}

        # ── Section 1: file selection ──────────────────────────────────
        file_frame = tk.LabelFrame(
            self._win, text="Archivo de configuración rclone", **pad
        )
        file_frame.pack(fill=tk.X, padx=12, pady=(12, 6))

        self._path_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self._path_var, width=44).grid(
            row=0, column=0, sticky="ew", padx=(6, 4), pady=4
        )
        tk.Button(
            file_frame,
            text="Examinar…",
            command=self._browse_file,
            cursor="hand2",
        ).grid(row=0, column=1, padx=(0, 6))

        # ── Section 2: sudo password (hidden until needed) ─────────────
        self._sudo_frame = tk.LabelFrame(
            self._win, text="Contraseña de superusuario (sudo)", **pad
        )
        # Packed later if and only if the file can't be read normally.
        self._sudo_var = tk.StringVar()
        tk.Label(self._sudo_frame, text="Contraseña:").grid(
            row=0, column=0, sticky="w", padx=(6, 4)
        )
        tk.Entry(
            self._sudo_frame, textvariable=self._sudo_var, show="*", width=30
        ).grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=4)

        # ── Load button ────────────────────────────────────────────────
        self._load_btn = tk.Button(
            self._win,
            text="Cargar remotes",
            command=self._load_remotes,
            bg="#0078d4",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            cursor="hand2",
        )
        self._load_btn.pack(pady=(0, 4))

        # ── Section 3: remote list ─────────────────────────────────────
        list_frame = tk.LabelFrame(self._win, text="Remotes disponibles", **pad)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self._listbox = tk.Listbox(
            list_frame,
            height=8,
            exportselection=False,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self._listbox.yview)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=4)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=4, padx=(0, 6))
        self._listbox.bind("<<ListboxSelect>>", self._on_remote_selected)

        # ── Section 4: service name ────────────────────────────────────
        name_frame = tk.Frame(self._win)
        name_frame.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(name_frame, text="Nombre del servicio:").pack(side=tk.LEFT)
        self._name_var = tk.StringVar()
        tk.Entry(name_frame, textvariable=self._name_var, width=30).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # ── Section 5: local sync folder ──────────────────────────────
        lpath_frame = tk.Frame(self._win)
        lpath_frame.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(lpath_frame, text="Carpeta local:").pack(side=tk.LEFT)
        self._lpath_var = tk.StringVar(value=str(Path.home()))
        tk.Entry(lpath_frame, textvariable=self._lpath_var, width=28).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        tk.Button(
            lpath_frame,
            text="…",
            command=self._browse_local_path,
            cursor="hand2",
            width=3,
        ).pack(side=tk.LEFT, padx=(4, 0))

        # ── Status / error label ───────────────────────────────────────
        self._status_var = tk.StringVar()
        self._status_lbl = tk.Label(
            self._win,
            textvariable=self._status_var,
            fg="#cc0000",
            wraplength=400,
            justify=tk.LEFT,
        )
        self._status_lbl.pack(padx=12, pady=(0, 4))

        # ── Bottom buttons ─────────────────────────────────────────────
        btn_row = tk.Frame(self._win)
        btn_row.pack(padx=12, pady=(0, 12))

        self._import_btn = tk.Button(
            btn_row,
            text="📥 Importar",
            command=self._do_import,
            bg="#107c10",
            fg="white",
            relief=tk.FLAT,
            padx=10,
            state=tk.DISABLED,
            cursor="hand2",
        )
        self._import_btn.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_row,
            text="Cancelar",
            command=self._win.destroy,
            relief=tk.FLAT,
            padx=10,
            cursor="hand2",
        ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _browse_file(self) -> None:
        """Open a file picker and populate the path entry."""
        initial = Path.home() / ".config" / "rclone"
        if not initial.exists():
            initial = Path.home()
        path = filedialog.askopenfilename(
            parent=self._win,
            title="Seleccionar archivo de configuración rclone",
            initialdir=str(initial),
            filetypes=[
                ("Configuración rclone", "*.conf"),
                ("Todos los archivos", "*"),
            ],
        )
        if path:
            self._path_var.set(path)

    def _browse_local_path(self) -> None:
        """Open a directory picker for the local sync folder."""
        directory = filedialog.askdirectory(
            parent=self._win,
            title="Seleccionar carpeta local de sincronización",
            initialdir=self._lpath_var.get() or str(Path.home()),
        )
        if directory:
            self._lpath_var.set(directory)

    def _read_config_file(
        self, path: str, sudo_password: Optional[str]
    ) -> Optional[str]:
        """
        Return the text content of *path*.

        Tries a normal ``open()`` first.  If permission is denied **and**
        a *sudo_password* was provided, retries with ``sudo -S cat``.
        Returns ``None`` on any failure so the caller can show an error.

        The *path* argument is passed as a list element to ``subprocess.run``
        (not via a shell string), so there is no shell-injection risk even for
        unusual file names.
        """
        # Basic sanity check before touching the filesystem
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return None

        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            if sudo_password:
                try:
                    # Password is delivered via stdin (-S flag), never via argv.
                    # subprocess list form ensures no shell metacharacter expansion.
                    result = subprocess.run(
                        ["sudo", "-S", "cat", str(resolved)],
                        input=sudo_password + "\n",
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if result.returncode == 0:
                        return result.stdout
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return None
        except OSError:
            return None

    @staticmethod
    def _parse_remotes(content: str) -> Dict[str, Dict[str, str]]:
        """
        Parse an rclone ``.conf`` (INI) file.

        Returns ``{remote_name: {key: value, ...}}`` for every section.
        The ``type`` key is always present in the value dict when the remote
        has a valid ``type`` entry.
        """
        parser = configparser.RawConfigParser()
        parser.read_string(content)
        return {
            section: dict(parser.items(section))
            for section in parser.sections()
        }

    def _set_status(self, msg: str, color: str = "#cc0000") -> None:
        self._status_var.set(msg)
        self._status_lbl.config(fg=color)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _load_remotes(self) -> None:
        """Read the chosen config file and populate the remotes listbox."""
        path = self._path_var.get().strip()
        if not path:
            self._set_status("❌ Por favor selecciona un archivo de configuración.")
            return

        self._listbox.delete(0, tk.END)
        self._remotes.clear()
        self._import_btn.config(state=tk.DISABLED)
        self._set_status("")

        # First attempt: unprivileged read
        content = self._read_config_file(path, sudo_password=None)
        if content is None:
            # Show the sudo password frame and inform the user
            if not self._sudo_frame_visible:
                self._sudo_frame.pack(
                    fill=tk.X,
                    padx=12,
                    pady=(0, 6),
                    before=self._load_btn,
                )
                self._sudo_frame_visible = True
            self._set_status(
                "⚠️ No se puede leer el archivo. Si requiere contraseña de "
                "superusuario, ingrésala arriba y vuelve a hacer clic en "
                "'Cargar remotes'.",
                color="#885500",
            )
            # If the password field is already filled, retry immediately
            sudo_pw = self._sudo_var.get()
            if not sudo_pw:
                return
            content = self._read_config_file(path, sudo_password=sudo_pw)
            if content is None:
                self._set_status(
                    "❌ No se pudo leer el archivo; verifica la contraseña."
                )
                return

        remotes = self._parse_remotes(content)
        if not remotes:
            self._set_status(
                "⚠️ No se encontraron remotes en ese archivo.", color="#885500"
            )
            return

        self._remotes = remotes
        for name, data in remotes.items():
            remote_type = data.get("type", "?")
            self._listbox.insert(tk.END, f"{name}  ({remote_type})")

        self._set_status(
            f"✅ {len(remotes)} remote(s) encontrado(s).", color="#107c10"
        )

    def _on_remote_selected(self, _event: object) -> None:
        """Pre-fill the service-name entry and enable the Import button."""
        selection = self._listbox.curselection()
        if not selection:
            return
        entry = self._listbox.get(selection[0])
        # Entry format: "remote_name  (type)"
        remote_name = entry.split("  (")[0].strip()
        self._selected_remote = remote_name
        if not self._name_var.get():
            self._name_var.set(remote_name)
        self._import_btn.config(state=tk.NORMAL)

    def _do_import(self) -> None:
        """Validate inputs and kick off the import in a background thread."""
        remote_name = self._selected_remote
        service_name = self._name_var.get().strip()
        local_path = self._lpath_var.get().strip()

        if not remote_name:
            self._set_status("❌ Selecciona un remote de la lista.")
            return
        if not service_name:
            self._set_status("❌ Ingresa un nombre para el servicio.")
            return
        if not local_path:
            self._set_status("❌ Ingresa la carpeta local de sincronización.")
            return
        if self._config.get_service(service_name):
            self._set_status(f"❌ Ya existe un servicio llamado '{service_name}'.")
            return

        remote_data = self._remotes.get(remote_name, {})
        self._import_btn.config(state=tk.DISABLED, text="⏳ Importando…")
        self._set_status("")

        def _worker() -> None:
            ok, err = self._rclone.import_remote(
                remote_name=remote_name,
                new_name=service_name,
                remote_data=remote_data,
            )
            self._win.after(
                0,
                lambda: self._on_import_done(
                    ok, err, service_name, local_path, remote_data
                ),
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_import_done(
        self,
        ok: bool,
        err: str,
        service_name: str,
        local_path: str,
        remote_data: Dict[str, str],
    ) -> None:
        """Handle the result of the import operation on the main thread."""
        if not ok:
            self._import_btn.config(state=tk.NORMAL, text="📥 Importar")
            self._set_status(f"❌ Error: {err}")
            return

        # Register the new service in ConfigManager
        remote_type = remote_data.get("type", "")
        self._config.add_service(
            name=service_name,
            platform=remote_type,
            local_path=local_path,
        )

        self._win.destroy()
        self._on_complete(service_name)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _center_toplevel(win: tk.Toplevel, parent: tk.Misc) -> None:
    """Center *win* over *parent*."""
    win.update_idletasks()
    px = parent.winfo_rootx()
    py = parent.winfo_rooty()
    pw = parent.winfo_width()
    ph = parent.winfo_height()
    ww = win.winfo_reqwidth()
    wh = win.winfo_reqheight()
    x = px + (pw - ww) // 2
    y = py + (ph - wh) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
