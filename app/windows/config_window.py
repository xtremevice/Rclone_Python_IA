"""
Service configuration window.

Left-hand navigation menu with 7 sections:
  1. Configuración por Defecto
  2. Directorios
  3. Excepciones
  4. Árbol de Carpetas
  5. Intervalo de Sincronización
  6. Espacio en Disco
  7. Información del Servicio

Window size: 70 % of screen width × 60 % of screen height.
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Dict, List, Optional

from app.config import AVAILABLE_SERVICES, SYNC_INTERVALS, AppConfig
from app.rclone_manager import RcloneManager
from app.sync_manager import SyncManager
from app.utils import center_window


# How often (ms) disk-usage label auto-refreshes while this window is open
_DISK_REFRESH_MS = 10_000


class ConfigWindow(tk.Toplevel):
    """
    Configuration window for a single service.

    Shows a left-side navigation menu and a right-side content pane that
    changes according to the selected menu item.
    """

    def __init__(
        self,
        parent: tk.Tk | tk.Toplevel,
        service_name: str,
        app_config: AppConfig,
        rclone: RcloneManager,
        sync_manager: SyncManager,
        on_service_deleted: Optional[Callable[[str], None]] = None,
        on_saved: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Open the config window for *service_name*.

        Args:
            parent: Parent tkinter window.
            service_name: The service whose settings are edited.
            app_config: Shared application configuration.
            rclone: RcloneManager instance.
            sync_manager: Running SyncManager.
            on_service_deleted: Callback(name) when service is removed.
            on_saved: Callback(name) after settings are saved.
        """
        super().__init__(parent)
        self.app_config = app_config
        self.rclone = rclone
        self.sync_manager = sync_manager
        self._on_service_deleted = on_service_deleted
        self._on_saved = on_saved

        # Work on a mutable copy of the service config; only persist on Save
        original = app_config.get_service(service_name)
        if original is None:
            self.destroy()
            return
        # Deep copy via JSON round-trip
        import copy
        self._svc: Dict = copy.deepcopy(original)

        self.grab_set()
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Size: 70 % wide × 60 % tall
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = int(sw * 0.70)
        h = int(sh * 0.60)
        center_window(self, w, h)
        self.title(
            f"Configuración – {self._svc.get('display_name', service_name)}"
        )

        self._build_layout(w, h)
        # Initialise disk-refresh handle before any section can set it
        self._disk_refresh_id: Optional[str] = None
        self.bind("<Destroy>", self._on_destroy)
        # Show first section by default
        self._select_section(0)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self, w: int, h: int) -> None:
        """Create the two-pane layout: left menu + right content."""
        # ---- Left navigation menu (~22 % of width) ----
        menu_w = max(160, int(w * 0.22))
        self._menu_frame = tk.Frame(self, bg="#2c3e50", width=menu_w)
        self._menu_frame.pack(side=tk.LEFT, fill=tk.Y)
        self._menu_frame.pack_propagate(False)

        tk.Label(
            self._menu_frame,
            text="Configuración",
            bg="#2c3e50",
            fg="white",
            font=("TkDefaultFont", 10, "bold"),
            pady=12,
        ).pack(fill=tk.X)

        ttk.Separator(self._menu_frame, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=8, pady=4
        )

        # Menu items
        self._sections = [
            "1. Config por Defecto",
            "2. Directorios",
            "3. Excepciones",
            "4. Árbol de Carpetas",
            "5. Intervalo de Sync",
            "6. Espacio en Disco",
            "7. Información",
        ]

        self._menu_buttons: List[tk.Button] = []
        for idx, label in enumerate(self._sections):
            btn = tk.Button(
                self._menu_frame,
                text=label,
                anchor=tk.W,
                padx=10,
                bg="#2c3e50",
                fg="white",
                relief=tk.FLAT,
                activebackground="#34495e",
                activeforeground="white",
                command=lambda i=idx: self._select_section(i),
            )
            btn.pack(fill=tk.X, pady=1)
            self._menu_buttons.append(btn)

        # ---- Right content area ----
        right_frame = tk.Frame(self, bg="#f5f5f5")
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Content pane (swapped per section)
        self._content = tk.Frame(right_frame, bg="#f5f5f5")
        self._content.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        # ---- Save button at bottom right ----
        save_bar = tk.Frame(right_frame, bg="#e8e8e8", pady=6)
        save_bar.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(
            save_bar,
            text="💾  Guardar cambios",
            command=self._save,
            bg="#0078d4",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
        ).pack(side=tk.RIGHT, padx=12)

        self._selected_idx = -1

    # ------------------------------------------------------------------
    # Section navigation
    # ------------------------------------------------------------------

    def _select_section(self, idx: int) -> None:
        """Highlight the selected menu item and render the corresponding pane."""
        if idx == self._selected_idx:
            return

        # Update button colours
        for i, btn in enumerate(self._menu_buttons):
            btn.config(bg="#1a252f" if i == idx else "#2c3e50")

        self._selected_idx = idx

        # Clear content area
        for widget in self._content.winfo_children():
            widget.destroy()

        # Cancel any pending disk refresh
        if self._disk_refresh_id is not None:
            self.after_cancel(self._disk_refresh_id)
            self._disk_refresh_id = None

        # Render chosen section
        renderers = [
            self._render_defaults,
            self._render_directories,
            self._render_exceptions,
            self._render_folder_tree,
            self._render_interval,
            self._render_disk,
            self._render_info,
        ]
        if 0 <= idx < len(renderers):
            renderers[idx]()

    # ------------------------------------------------------------------
    # Section 1 – Default rclone configuration
    # ------------------------------------------------------------------

    def _render_defaults(self) -> None:
        """Render the default rclone options section."""
        frame = self._content
        tk.Label(
            frame, text="Configuración por Defecto de Rclone",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 12))

        opts = self._svc.get("rclone_options", {})

        fields = [
            ("Transferencias simultáneas:", "transfers", opts.get("transfers", 16)),
            ("Checkers paralelos:", "checkers", opts.get("checkers", 32)),
            ("Tamaño chunk Drive:", "drive_chunk_size", opts.get("drive_chunk_size", "128M")),
            ("Tamaño buffer:", "buffer_size", opts.get("buffer_size", "64M")),
        ]

        self._default_vars: Dict[str, tk.StringVar] = {}
        for label, key, val in fields:
            row = tk.Frame(frame, bg="#f5f5f5")
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, bg="#f5f5f5", width=28, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=str(val))
            self._default_vars[key] = var
            tk.Entry(row, textvariable=var, width=14).pack(side=tk.LEFT)

        # Resync on first run toggle
        tk.Label(frame, text="", bg="#f5f5f5").pack()
        self._resync_var = tk.BooleanVar(
            value=not self._svc.get("first_sync_done", False)
        )
        tk.Checkbutton(
            frame,
            text="Forzar --resync en la próxima sincronización",
            variable=self._resync_var,
            bg="#f5f5f5",
        ).pack(anchor=tk.W)

    # ------------------------------------------------------------------
    # Section 2 – Directories
    # ------------------------------------------------------------------

    def _render_directories(self) -> None:
        """Render the directory configuration section."""
        frame = self._content
        tk.Label(
            frame, text="Directorios",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 12))

        # Local path
        loc_row = tk.Frame(frame, bg="#f5f5f5")
        loc_row.pack(fill=tk.X, pady=4)
        tk.Label(loc_row, text="Carpeta local:", bg="#f5f5f5", width=20, anchor=tk.W).pack(side=tk.LEFT)
        self._local_var = tk.StringVar(value=self._svc.get("local_path", ""))
        tk.Entry(loc_row, textvariable=self._local_var, width=40).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(loc_row, text="…", command=self._browse_local).pack(side=tk.LEFT)

        # Remote path
        rem_row = tk.Frame(frame, bg="#f5f5f5")
        rem_row.pack(fill=tk.X, pady=4)
        tk.Label(rem_row, text="Ruta en el servicio:", bg="#f5f5f5", width=20, anchor=tk.W).pack(side=tk.LEFT)
        self._remote_var = tk.StringVar(value=self._svc.get("remote_path", "/"))
        tk.Entry(rem_row, textvariable=self._remote_var, width=40).pack(side=tk.LEFT)

        tk.Label(
            frame,
            text="(Use '/' para sincronizar desde la raíz del servicio)",
            bg="#f5f5f5", fg="#888", font=("TkDefaultFont", 8),
        ).pack(anchor=tk.W, pady=(4, 0))

    def _browse_local(self) -> None:
        """Open a folder chooser for the local directory field."""
        path = filedialog.askdirectory(parent=self, title="Seleccione carpeta local")
        if path:
            self._local_var.set(path)

    # ------------------------------------------------------------------
    # Section 3 – Exclusion patterns
    # ------------------------------------------------------------------

    def _render_exceptions(self) -> None:
        """Render the file/folder exception patterns section."""
        frame = self._content
        tk.Label(
            frame, text="Carpetas y Patrones de Excepción",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 6))

        tk.Label(
            frame,
            text="Los patrones usan la sintaxis de rclone (ej. 'Almacén personal/**').",
            bg="#f5f5f5", fg="#555", font=("TkDefaultFont", 8),
        ).pack(anchor=tk.W, pady=(0, 8))

        list_frame = tk.Frame(frame, bg="#f5f5f5")
        list_frame.pack(fill=tk.BOTH, expand=True)

        self._exc_listbox = tk.Listbox(list_frame, selectmode=tk.SINGLE, height=12)
        for p in self._svc.get("exclude_patterns", []):
            self._exc_listbox.insert(tk.END, p)
        self._exc_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(list_frame, command=self._exc_listbox.yview)
        self._exc_listbox.config(yscrollcommand=sb.set)
        sb.pack(side=tk.LEFT, fill=tk.Y)

        btn_col = tk.Frame(frame, bg="#f5f5f5")
        btn_col.pack(anchor=tk.W, pady=6)
        tk.Button(btn_col, text="+ Agregar", command=self._exc_add).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_col, text="– Eliminar", command=self._exc_remove).pack(side=tk.LEFT, padx=2)

    def _exc_add(self) -> None:
        """Prompt for a new exclusion pattern and add it to the list."""
        pattern = simpledialog.askstring(
            "Nuevo patrón",
            "Ingrese el patrón de exclusión:",
            parent=self,
        )
        if pattern:
            self._exc_listbox.insert(tk.END, pattern)

    def _exc_remove(self) -> None:
        """Remove the selected exclusion pattern."""
        sel = self._exc_listbox.curselection()
        if sel:
            self._exc_listbox.delete(sel[0])

    # ------------------------------------------------------------------
    # Section 4 – Folder tree with sync toggles
    # ------------------------------------------------------------------

    def _render_folder_tree(self) -> None:
        """Render the remote folder tree with sync checkboxes."""
        frame = self._content
        tk.Label(
            frame, text="Árbol de Carpetas del Servicio",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 6))

        tk.Label(
            frame,
            text="Marque las carpetas que desea sincronizar.",
            bg="#f5f5f5", fg="#555", font=("TkDefaultFont", 8),
        ).pack(anchor=tk.W, pady=(0, 8))

        # Treeview
        tree_frame = tk.Frame(frame, bg="#f5f5f5")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self._folder_tree = ttk.Treeview(tree_frame, columns=("sync",), show="tree headings")
        self._folder_tree.heading("sync", text="Sincronizar")
        self._folder_tree.column("#0", stretch=True)
        self._folder_tree.column("sync", width=90, stretch=False)
        self._folder_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._folder_tree.yview)
        self._folder_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._folder_check_vars: Dict[str, tk.BooleanVar] = {}

        # Load folders in background to avoid blocking UI
        tk.Button(
            frame,
            text="🔄 Cargar carpetas del servicio",
            command=self._load_folder_tree,
        ).pack(anchor=tk.W, pady=4)

        self._tree_status_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self._tree_status_var, bg="#f5f5f5", fg="#888").pack(anchor=tk.W)

    def _load_folder_tree(self) -> None:
        """Fetch top-level directories from the remote in a background thread."""
        self._tree_status_var.set("Cargando…")

        def _fetch() -> None:
            """Worker: list remote dirs and populate the treeview."""
            dirs = self.rclone.list_remote_dirs(self._svc["name"])
            self.after(0, lambda: self._populate_folder_tree(dirs))

        threading.Thread(target=_fetch, daemon=True).start()

    def _populate_folder_tree(self, dirs: List[str]) -> None:
        """Fill the treeview with *dirs* and mark synced/excluded status."""
        self._folder_tree.delete(*self._folder_tree.get_children())
        self._folder_check_vars.clear()
        excluded = set(self._svc.get("excluded_folders", []))
        for d in dirs:
            synced = d not in excluded
            self._folder_tree.insert(
                "", tk.END, text=d, values=("✓ Sí" if synced else "✗ No",), iid=d
            )
        self._tree_status_var.set(f"{len(dirs)} carpetas cargadas" if dirs else "Sin carpetas")
        # Toggle on double-click
        self._folder_tree.bind("<Double-1>", self._toggle_folder_sync)

    def _toggle_folder_sync(self, event: tk.Event) -> None:
        """Toggle the sync flag for the double-clicked folder."""
        item = self._folder_tree.focus()
        if not item:
            return
        excluded = set(self._svc.get("excluded_folders", []))
        if item in excluded:
            excluded.discard(item)
            self._folder_tree.item(item, values=("✓ Sí",))
        else:
            excluded.add(item)
            self._folder_tree.item(item, values=("✗ No",))
        self._svc["excluded_folders"] = list(excluded)

    # ------------------------------------------------------------------
    # Section 5 – Sync interval
    # ------------------------------------------------------------------

    def _render_interval(self) -> None:
        """Render the sync interval and autostart options."""
        frame = self._content
        tk.Label(
            frame, text="Intervalo de Sincronización",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 12))

        # Interval dropdown
        int_row = tk.Frame(frame, bg="#f5f5f5")
        int_row.pack(fill=tk.X, pady=4)
        tk.Label(int_row, text="Sincronizar cada:", bg="#f5f5f5", width=20, anchor=tk.W).pack(side=tk.LEFT)

        current_minutes = self._svc.get("sync_interval", 15)
        current_label = next(
            (lbl for lbl, m in SYNC_INTERVALS.items() if m == current_minutes),
            "15 minutos",
        )
        self._interval_var = tk.StringVar(value=current_label)
        ttk.Combobox(
            int_row,
            textvariable=self._interval_var,
            values=list(SYNC_INTERVALS.keys()),
            state="readonly",
            width=20,
        ).pack(side=tk.LEFT)

        # Autostart
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        tk.Label(
            frame, text="Inicio automático",
            font=("TkDefaultFont", 10, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 6))

        self._autostart_var = tk.BooleanVar(value=self._svc.get("autostart", False))
        tk.Checkbutton(
            frame,
            text="Iniciar sincronización con el sistema",
            variable=self._autostart_var,
            bg="#f5f5f5",
        ).pack(anchor=tk.W)

        # Autostart delay
        delay_row = tk.Frame(frame, bg="#f5f5f5")
        delay_row.pack(fill=tk.X, pady=4)
        tk.Label(delay_row, text="Retraso al inicio (segundos):", bg="#f5f5f5", width=28, anchor=tk.W).pack(side=tk.LEFT)
        self._delay_var = tk.StringVar(value=str(self._svc.get("autostart_delay", 0)))
        tk.Spinbox(delay_row, from_=0, to=300, textvariable=self._delay_var, width=6).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Section 6 – Disk space
    # ------------------------------------------------------------------

    def _render_disk(self) -> None:
        """Render the disk usage and free-cache section."""
        frame = self._content
        tk.Label(
            frame, text="Espacio en Disco",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 12))

        # Usage display (auto-refreshes)
        usage_row = tk.Frame(frame, bg="#f5f5f5")
        usage_row.pack(fill=tk.X, pady=4)
        tk.Label(usage_row, text="Espacio utilizado:", bg="#f5f5f5", width=20, anchor=tk.W).pack(side=tk.LEFT)
        self._disk_var = tk.StringVar(value="Calculando…")
        tk.Label(usage_row, textvariable=self._disk_var, bg="#f5f5f5", fg="#0078d4").pack(side=tk.LEFT)

        btn_row = tk.Frame(frame, bg="#f5f5f5")
        btn_row.pack(anchor=tk.W, pady=8)
        tk.Button(btn_row, text="🔄 Actualizar", command=self._refresh_disk).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="🗑 Liberar espacio en disco",
            command=self._free_disk,
            bg="#e67e22", fg="white", relief=tk.FLAT, padx=8,
        ).pack(side=tk.LEFT)

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=16)

        # Delete service
        tk.Label(
            frame, text="Zona de Peligro",
            font=("TkDefaultFont", 10, "bold"), bg="#f5f5f5", fg="#dc3545",
        ).pack(anchor=tk.W, pady=(0, 6))
        tk.Button(
            frame,
            text="🗑 Eliminar este servicio",
            command=self._delete_service,
            bg="#dc3545", fg="white", relief=tk.FLAT, padx=8, pady=4,
        ).pack(anchor=tk.W)

        # Start auto-refresh
        self._do_disk_refresh()

    def _do_disk_refresh(self) -> None:
        """Compute disk usage in a background thread and schedule next refresh."""
        def _compute() -> None:
            usage = self.rclone.get_local_disk_usage(self._svc["local_path"])
            self.after(0, lambda: self._disk_var.set(usage))

        threading.Thread(target=_compute, daemon=True).start()
        self._disk_refresh_id = self.after(_DISK_REFRESH_MS, self._do_disk_refresh)

    def _refresh_disk(self) -> None:
        """Manually trigger a disk-usage refresh."""
        self._disk_var.set("Calculando…")
        self._do_disk_refresh()

    def _free_disk(self) -> None:
        """Confirm and delete locally-cached files for this service."""
        if not messagebox.askyesno(
            "Liberar espacio",
            "¿Desea borrar todos los archivos locales descargados?\n"
            "Los archivos en la nube no se verán afectados.",
            parent=self,
        ):
            return
        ok, msg = self.rclone.free_local_cache(self._svc["local_path"])
        if ok:
            messagebox.showinfo("Listo", msg, parent=self)
        else:
            messagebox.showerror("Error", msg, parent=self)
        self._refresh_disk()

    def _delete_service(self) -> None:
        """Ask for confirmation and permanently remove the service."""
        name = self._svc["name"]
        display = self._svc.get("display_name", name)
        if not messagebox.askyesno(
            "Eliminar servicio",
            f"¿Está seguro de que desea eliminar el servicio '{display}'?\n"
            "Esta acción no se puede deshacer.",
            parent=self,
            icon="warning",
        ):
            return
        # Stop sync, remove rclone remote and app config
        self.sync_manager.remove_service(name)
        self.rclone.delete_remote(name)
        self.app_config.remove_service(name)
        messagebox.showinfo(
            "Servicio eliminado",
            f"El servicio '{display}' fue eliminado.",
            parent=self,
        )
        self.grab_release()
        self.destroy()
        if self._on_service_deleted:
            self._on_service_deleted(name)

    # ------------------------------------------------------------------
    # Section 7 – Service info
    # ------------------------------------------------------------------

    def _render_info(self) -> None:
        """Render read-only service information."""
        frame = self._content
        tk.Label(
            frame, text="Información del Servicio",
            font=("TkDefaultFont", 11, "bold"), bg="#f5f5f5",
        ).pack(anchor=tk.W, pady=(0, 12))

        svc = self._svc
        name = svc.get("name", "—")
        display = svc.get("display_name", name)
        stype = svc.get("service_type", "—")
        platform = AVAILABLE_SERVICES.get(stype, stype)
        local = svc.get("local_path", "—")
        remote = svc.get("remote_path", "/")
        interval = svc.get("sync_interval", 15)
        interval_label = next(
            (l for l, m in SYNC_INTERVALS.items() if m == interval), f"{interval} min"
        )
        status = self.sync_manager.get_status(name)
        rclone_ver = self.rclone.get_version()

        rows = [
            ("Nombre del servicio:", display),
            ("Nombre interno (rclone):", name),
            ("Plataforma:", platform),
            ("Carpeta local:", local),
            ("Ruta en el servicio:", remote),
            ("Intervalo de sync:", interval_label),
            ("Estado actual:", status),
            ("Versión de rclone:", rclone_ver),
        ]

        info_frame = tk.Frame(frame, bg="#f0f0f0", padx=12, pady=10, relief=tk.SUNKEN)
        info_frame.pack(fill=tk.X, pady=4)

        for r, (label, value) in enumerate(rows):
            tk.Label(
                info_frame, text=label, bg="#f0f0f0",
                font=("TkDefaultFont", 9, "bold"), anchor=tk.W,
            ).grid(row=r, column=0, sticky=tk.W, pady=2)
            tk.Label(
                info_frame, text=value, bg="#f0f0f0",
                font=("TkDefaultFont", 9), anchor=tk.W,
            ).grid(row=r, column=1, sticky=tk.W, padx=(8, 0))

    # ------------------------------------------------------------------
    # Save / close
    # ------------------------------------------------------------------

    def _collect_form_values(self) -> None:
        """
        Harvest all form-widget values into self._svc.

        Only collects from the currently rendered section; other sections'
        values were already mutated in-place via their callbacks.
        """
        idx = self._selected_idx

        if idx == 0:
            # Defaults
            if hasattr(self, "_default_vars"):
                opts = self._svc.setdefault("rclone_options", {})
                for key, var in self._default_vars.items():
                    raw = var.get().strip()
                    try:
                        opts[key] = int(raw)
                    except ValueError:
                        opts[key] = raw
            if hasattr(self, "_resync_var"):
                # If user checked 'force resync' we reset first_sync_done
                if self._resync_var.get():
                    self._svc["first_sync_done"] = False

        elif idx == 1:
            # Directories
            if hasattr(self, "_local_var"):
                self._svc["local_path"] = self._local_var.get().strip()
            if hasattr(self, "_remote_var"):
                self._svc["remote_path"] = self._remote_var.get().strip() or "/"

        elif idx == 2:
            # Exceptions list
            if hasattr(self, "_exc_listbox"):
                self._svc["exclude_patterns"] = list(
                    self._exc_listbox.get(0, tk.END)
                )

        elif idx == 4:
            # Interval
            if hasattr(self, "_interval_var"):
                label = self._interval_var.get()
                self._svc["sync_interval"] = SYNC_INTERVALS.get(label, 15)
            if hasattr(self, "_autostart_var"):
                self._svc["autostart"] = self._autostart_var.get()
            if hasattr(self, "_delay_var"):
                try:
                    self._svc["autostart_delay"] = int(self._delay_var.get())
                except ValueError:
                    self._svc["autostart_delay"] = 0

    def _save(self) -> None:
        """Collect form values and persist to config."""
        self._collect_form_values()
        self.app_config.update_service(self._svc["name"], self._svc)
        messagebox.showinfo(
            "Guardado",
            "La configuración fue guardada exitosamente.",
            parent=self,
        )
        if self._on_saved:
            self._on_saved(self._svc["name"])

    def _on_close(self) -> None:
        """Close without saving (ask for confirmation if changes pending)."""
        self.grab_release()
        self.destroy()

    def _on_destroy(self, event: tk.Event) -> None:
        """Clean up the disk-refresh timer when the window is destroyed."""
        if event.widget is self and self._disk_refresh_id is not None:
            try:
                self.after_cancel(self._disk_refresh_id)
            except Exception:
                pass
