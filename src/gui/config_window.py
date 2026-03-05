"""
Service configuration window.

Shows a left-side menu with 7 sections and a corresponding right-side panel:
  1. Default configuration
  2. Change directory (local / remote)
  3. Exclusions management
  4. Folder tree with sync toggle
  5. Sync schedule & startup options
  6. Free disk space / delete service
  7. Service information

Window size: 60 % of screen height × 35 % of screen width.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Dict, List, Optional

from src.config.config_manager import (
    PLATFORM_LABELS,
    DEFAULT_SYNC_INTERVAL,
    PERSONAL_VAULT_PATTERN,
    ConfigManager,
)
from src.rclone.rclone_manager import RcloneManager
from src.gui.setup_wizard import _center_window


# Mapping from human-readable interval label → seconds
INTERVAL_OPTIONS: Dict[str, int] = {
    "1 minuto": 60,
    "5 minutos": 300,
    "15 minutos": 900,
    "30 minutos": 1800,
    "1 hora": 3600,
    "2 horas": 7200,
    "3 horas": 10800,
    "6 horas": 21600,
    "12 horas": 43200,
    "24 horas": 86400,
}


class ConfigWindow:
    """
    Configuration window for a single service.

    Displays seven option panels in a left-side menu.
    All changes are applied only when the user clicks 'Guardar'.
    """

    def __init__(
        self,
        parent: tk.Tk,
        config_manager: ConfigManager,
        rclone_manager: RcloneManager,
        service_name: str,
        on_saved: Optional[Callable[[], None]] = None,
        on_deleted: Optional[Callable[[str], None]] = None,
    ) -> None:
        # Store references
        self._config = config_manager
        self._rclone = rclone_manager
        self._service_name = service_name
        self._on_saved = on_saved
        self._on_deleted = on_deleted

        # Load a mutable copy of the service data
        svc = config_manager.get_service(service_name) or {}
        self._svc: Dict = dict(svc)

        # Create top-level window
        self._win = tk.Toplevel(parent)
        self._win.title(f"Configuración – {service_name}")
        self._win.resizable(False, False)
        _center_window(self._win, height_pct=0.60, width_pct=0.35)

        self._build_layout()
        # Show the first panel by default
        self._show_panel(0)

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        """Build the two-column layout: left menu + right content area."""
        # Main container
        main = tk.Frame(self._win)
        main.pack(fill=tk.BOTH, expand=True)

        # Left sidebar menu
        sidebar = tk.Frame(main, width=180, bg="#2c2c2c")
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        menu_items = [
            "1. Configuración por defecto",
            "2. Cambiar directorio",
            "3. Excepciones",
            "4. Árbol de carpetas",
            "5. Programación de sync",
            "6. Espacio en disco",
            "7. Información del servicio",
        ]

        self._menu_buttons: List[tk.Button] = []
        for idx, label in enumerate(menu_items):
            btn = tk.Button(
                sidebar,
                text=label,
                anchor="w",
                padx=10,
                bg="#2c2c2c",
                fg="white",
                relief=tk.FLAT,
                font=("Segoe UI", 9),
                command=lambda i=idx: self._show_panel(i),
            )
            btn.pack(fill=tk.X, pady=1)
            self._menu_buttons.append(btn)

        # Right content area
        self._content = tk.Frame(main, bg="white")
        self._content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bottom save button
        bottom = tk.Frame(self._win, bg="#f0f0f0")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=8)

        tk.Button(
            bottom,
            text="💾  Guardar cambios",
            command=self._save_all,
            bg="#0078d4",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=15,
            pady=5,
        ).pack(side=tk.RIGHT)

        tk.Button(
            bottom,
            text="Cancelar",
            command=self._win.destroy,
            relief=tk.FLAT,
            padx=10,
            pady=5,
        ).pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------------
    # Panel switching
    # ------------------------------------------------------------------

    def _show_panel(self, index: int) -> None:
        """Clear the content area and render the selected panel."""
        # Highlight the active menu button
        for i, btn in enumerate(self._menu_buttons):
            btn.configure(bg="#0078d4" if i == index else "#2c2c2c")

        # Destroy previous panel widgets
        for w in self._content.winfo_children():
            w.destroy()

        panels = [
            self._panel_defaults,
            self._panel_directory,
            self._panel_exclusions,
            self._panel_tree,
            self._panel_schedule,
            self._panel_disk,
            self._panel_info,
        ]
        panels[index]()

    # ------------------------------------------------------------------
    # Panel 1 – Default configuration
    # ------------------------------------------------------------------

    def _panel_defaults(self) -> None:
        """Panel showing and editing the default rclone sync options."""
        p = self._make_panel("Configuración por defecto")

        tk.Label(p, text="Estas opciones se aplican al ejecutar bisync para este servicio.", wraplength=450, justify="left").pack(anchor="w", pady=(0, 10))

        # Remote path (from root by default)
        tk.Label(p, text="Ruta remota base:", anchor="w").pack(anchor="w")
        self._remote_path_var = tk.StringVar(value=self._svc.get("remote_path", "/"))
        tk.Entry(p, textvariable=self._remote_path_var, width=40).pack(anchor="w", pady=(2, 10))

        # VFS cache mode
        tk.Label(p, text="Modo de caché VFS:", anchor="w").pack(anchor="w")
        self._vfs_var = tk.StringVar(value=self._svc.get("vfs_cache_mode", "on_demand"))
        cache_modes = ["off", "minimal", "writes", "on_demand", "full"]
        ttk.Combobox(p, textvariable=self._vfs_var, values=cache_modes, state="readonly", width=20).pack(anchor="w", pady=(2, 10))

        # Max cache size
        tk.Label(p, text="Tamaño máximo de caché:", anchor="w").pack(anchor="w")
        self._cache_size_var = tk.StringVar(value=self._svc.get("vfs_cache_max_size", "10G"))
        tk.Entry(p, textvariable=self._cache_size_var, width=15).pack(anchor="w", pady=(2, 10))

        # Resync checkbox
        self._resync_var = tk.BooleanVar(value=self._svc.get("use_resync", True))
        tk.Checkbutton(p, text="Usar --resync al detectar conflictos", variable=self._resync_var).pack(anchor="w", pady=5)

    # ------------------------------------------------------------------
    # Panel 2 – Change directory
    # ------------------------------------------------------------------

    def _panel_directory(self) -> None:
        """Panel to change the local or remote sync path."""
        p = self._make_panel("Cambiar directorio")

        # Local path
        tk.Label(p, text="Carpeta local de sincronización:", anchor="w").pack(anchor="w")
        local_frame = tk.Frame(p)
        local_frame.pack(fill=tk.X, pady=(2, 10))

        self._local_path_var = tk.StringVar(value=self._svc.get("local_path", ""))
        tk.Entry(local_frame, textvariable=self._local_path_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(local_frame, text="…", command=self._browse_local).pack(side=tk.LEFT, padx=4)
        tk.Button(local_frame, text="📁 Nueva", command=self._create_local_subfolder).pack(side=tk.LEFT, padx=(0, 4))

        # Remote path
        tk.Label(p, text="Ruta dentro del servicio remoto (ej. /MiCarpeta):", anchor="w").pack(anchor="w", pady=(10, 0))
        self._remote_dir_var = tk.StringVar(value=self._svc.get("remote_path", "/"))
        tk.Entry(p, textvariable=self._remote_dir_var, width=40).pack(anchor="w", pady=(2, 0))

    def _browse_local(self) -> None:
        """Open folder picker to change local sync directory."""
        folder = filedialog.askdirectory(
            initialdir=self._local_path_var.get(),
            parent=self._win,
        )
        if folder:
            self._local_path_var.set(folder)

    def _create_local_subfolder(self) -> None:
        """Create a new subfolder inside the current local path and update the entry."""
        parent_path = self._local_path_var.get().strip() or os.path.expanduser("~")
        name = simpledialog.askstring(
            "Nueva carpeta",
            "Nombre de la nueva carpeta:",
            parent=self._win,
        )
        if not name:
            return
        new_path = os.path.join(parent_path, name)
        try:
            os.makedirs(new_path, exist_ok=True)
            self._local_path_var.set(new_path)
        except OSError as exc:
            messagebox.showerror(
                "Error al crear carpeta",
                f"No se pudo crear la carpeta:\n{exc}",
                parent=self._win,
            )

    # ------------------------------------------------------------------
    # Panel 3 – Exclusions
    # ------------------------------------------------------------------

    def _panel_exclusions(self) -> None:
        """Panel to add or remove exclusion glob patterns."""
        p = self._make_panel("Excepciones")

        tk.Label(p, text="Patrones de exclusión (un patrón por línea):", anchor="w").pack(anchor="w")
        tk.Label(p, text="Ejemplo: /Almacén personal/**", fg="gray", font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 5))

        # Personal vault toggle
        self._personal_vault_var = tk.BooleanVar(value=self._svc.get("exclude_personal_vault", True))
        tk.Checkbutton(
            p,
            text='Excluir "Almacén personal" de OneDrive (recomendado)',
            variable=self._personal_vault_var,
        ).pack(anchor="w", pady=(0, 10))

        # Text area for custom exclusions
        text_frame = tk.Frame(p)
        text_frame.pack(fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(text_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._excl_text = tk.Text(text_frame, yscrollcommand=sb.set, width=50, height=10, font=("Courier", 10))
        self._excl_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self._excl_text.yview)

        # Pre-fill with existing exclusions (excluding the personal vault default)
        existing = [
            e for e in self._svc.get("exclusions", [])
            if e != "/Almacén personal/**"
        ]
        self._excl_text.insert("1.0", "\n".join(existing))

    # ------------------------------------------------------------------
    # Panel 4 – Folder tree
    # ------------------------------------------------------------------

    def _panel_tree(self) -> None:
        """Panel showing the remote folder tree with sync checkboxes."""
        p = self._make_panel("Árbol de carpetas remotas")

        tk.Label(p, text="Carpetas del servicio remoto (obtener lista puede tardar unos segundos):", wraplength=450, justify="left").pack(anchor="w", pady=(0, 5))

        # Treeview widget
        tree_frame = tk.Frame(p)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        sb_y = tk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=("synced",),
            yscrollcommand=sb_y.set,
            selectmode="browse",
        )
        self._tree.heading("#0", text="Carpeta")
        self._tree.heading("synced", text="Sincronizar")
        self._tree.column("synced", width=90, anchor="center")
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_y.config(command=self._tree.yview)

        self._tree.bind("<ButtonRelease-1>", self._toggle_tree_item)

        # Track which folders are excluded
        self._excluded_folders: List[str] = list(self._svc.get("excluded_folders", []))
        self._tree_items: Dict[str, str] = {}  # item_id → folder path

        tk.Button(p, text="🔄 Cargar carpetas", command=self._load_tree).pack(anchor="w", pady=(8, 0))

    def _load_tree(self) -> None:
        """Fetch the remote folder list in a background thread."""
        self._tree.delete(*self._tree.get_children())

        def fetch() -> None:
            items = self._rclone.list_remote_tree(self._service_name)
            self._win.after(0, lambda: self._populate_tree(items))

        threading.Thread(target=fetch, daemon=True).start()

    def _populate_tree(self, items: List[Dict]) -> None:
        """Insert fetched items into the treeview."""
        for item in items:
            path = item.get("path", "")
            synced = path not in self._excluded_folders
            item_id = self._tree.insert(
                "",
                tk.END,
                text=f"  📁 {path}",
                values=("✅ Sí" if synced else "❌ No",),
            )
            self._tree_items[item_id] = path

    def _toggle_tree_item(self, _event: tk.Event) -> None:
        """Toggle sync on/off for the clicked tree item."""
        item_id = self._tree.focus()
        if not item_id:
            return
        path = self._tree_items.get(item_id)
        if path is None:
            return
        if path in self._excluded_folders:
            self._excluded_folders.remove(path)
            self._tree.item(item_id, values=("✅ Sí",))
        else:
            self._excluded_folders.append(path)
            self._tree.item(item_id, values=("❌ No",))

    # ------------------------------------------------------------------
    # Panel 5 – Sync schedule
    # ------------------------------------------------------------------

    def _panel_schedule(self) -> None:
        """Panel for configuring sync interval and startup options."""
        p = self._make_panel("Programación de sincronización")

        tk.Label(p, text="Intervalo de sincronización:", anchor="w").pack(anchor="w")

        self._interval_var = tk.StringVar()
        current_secs = self._svc.get("sync_interval", DEFAULT_SYNC_INTERVAL)
        # Find the matching label or default to the closest
        current_label = next(
            (lbl for lbl, secs in INTERVAL_OPTIONS.items() if secs == current_secs),
            "15 minutos",
        )
        self._interval_var.set(current_label)

        ttk.Combobox(
            p,
            textvariable=self._interval_var,
            values=list(INTERVAL_OPTIONS.keys()),
            state="readonly",
            width=20,
        ).pack(anchor="w", pady=(2, 15))

        # Startup options
        tk.Label(p, text="Opciones de inicio:", anchor="w", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 5))

        self._startup_var = tk.BooleanVar(
            value=self._config.get_preference("start_with_system", False)
        )
        tk.Checkbutton(p, text="Iniciar este programa con el sistema", variable=self._startup_var).pack(anchor="w")

        tk.Label(p, text="Retraso de inicio (segundos):", anchor="w").pack(anchor="w", pady=(10, 2))
        self._startup_delay_var = tk.IntVar(
            value=self._config.get_preference("startup_delay_seconds", 30)
        )
        tk.Spinbox(p, from_=0, to=300, textvariable=self._startup_delay_var, width=8).pack(anchor="w")

    # ------------------------------------------------------------------
    # Panel 6 – Disk space / delete service
    # ------------------------------------------------------------------

    def _panel_disk(self) -> None:
        """Panel to free cached files or delete the service entirely."""
        p = self._make_panel("Espacio en disco")

        # Disk usage display
        disk_frame = tk.Frame(p)
        disk_frame.pack(fill=tk.X, pady=5)

        tk.Label(disk_frame, text="Espacio en disco usado:").pack(side=tk.LEFT)
        self._disk_usage_var = tk.StringVar(value="Calculando…")
        tk.Label(disk_frame, textvariable=self._disk_usage_var, fg="#0078d4", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=8)

        tk.Button(disk_frame, text="🔄", command=self._refresh_disk_usage).pack(side=tk.LEFT)

        # Calculate usage immediately (also schedules the first auto-refresh)
        self._refresh_disk_usage()

        tk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)

        tk.Button(
            p,
            text="🗂️  Liberar espacio en disco",
            command=self._free_cache,
            bg="#107c10",
            fg="white",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=10,
            pady=6,
        ).pack(anchor="w", pady=5)
        tk.Label(p, text="Vuelve los archivos abiertos a modo 'solo en la nube'.", fg="gray", font=("Segoe UI", 9)).pack(anchor="w")

        tk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)

        tk.Button(
            p,
            text="🗑️  Eliminar este servicio",
            command=self._confirm_delete,
            bg="#c50f1f",
            fg="white",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=10,
            pady=6,
        ).pack(anchor="w", pady=5)

    def _refresh_disk_usage(self) -> None:
        """Fetch disk usage in background and update the label, then reschedule."""
        def fetch() -> None:
            usage = self._rclone.get_disk_usage(self._service_name)
            self._win.after(0, lambda: self._disk_usage_var.set(usage))

        threading.Thread(target=fetch, daemon=True).start()
        # Reschedule the next auto-refresh in 10 seconds
        self._disk_refresh_id = self._win.after(10000, self._refresh_disk_usage)

    def _free_cache(self) -> None:
        """Run the vfs/forget command to free up local cache."""
        ok = self._rclone.free_cache(self._service_name)
        if ok:
            messagebox.showinfo("Listo", "Espacio liberado correctamente.", parent=self._win)
        else:
            messagebox.showerror(
                "Error",
                "No se pudo liberar el espacio. Asegúrate de que el servicio esté montado.",
                parent=self._win,
            )
        self._refresh_disk_usage()

    def _confirm_delete(self) -> None:
        """Ask for confirmation and delete the service if accepted."""
        confirmed = messagebox.askyesno(
            "Confirmar eliminación",
            f"¿Estás seguro de que deseas eliminar el servicio '{self._service_name}'?\n\n"
            "Esta acción eliminará la configuración. Los archivos locales no se borrarán.",
            parent=self._win,
        )
        if confirmed:
            # Remove the rclone remote
            remote_name = self._svc.get("remote_name", self._service_name.lower())
            self._rclone.delete_remote(remote_name)
            # Remove from app config
            self._config.remove_service(self._service_name)
            self._win.destroy()
            if self._on_deleted:
                self._on_deleted(self._service_name)

    # ------------------------------------------------------------------
    # Panel 7 – Service information
    # ------------------------------------------------------------------

    def _panel_info(self) -> None:
        """Panel displaying read-only information about the service."""
        p = self._make_panel("Información del servicio")

        platform_label = PLATFORM_LABELS.get(self._svc.get("platform", ""), "Desconocido")
        interval_secs = self._svc.get("sync_interval", DEFAULT_SYNC_INTERVAL)
        interval_label = next(
            (lbl for lbl, secs in INTERVAL_OPTIONS.items() if secs == interval_secs),
            f"{interval_secs} s",
        )
        sync_running = self._rclone.is_running(self._service_name)

        rows = [
            ("Nombre del servicio:", self._service_name),
            ("Plataforma:", platform_label),
            ("Cuenta / remote:", self._svc.get("remote_name", "—")),
            ("Carpeta local:", self._svc.get("local_path", "—")),
            ("Ruta remota:", self._svc.get("remote_path", "/")),
            ("Intervalo de sync:", interval_label),
            ("Estado:", "Sincronizando activamente" if sync_running else "Detenido"),
            ("Versión de rclone:", self._config.get_rclone_version()),
        ]

        for label, value in rows:
            row = tk.Frame(p)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, width=22, anchor="w", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
            tk.Label(row, text=value, anchor="w", wraplength=350, justify="left").pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_all(self) -> None:
        """Collect all panel data and persist it to the config."""
        updates: Dict = {}

        # Panel 1 – defaults
        if hasattr(self, "_remote_path_var"):
            updates["remote_path"] = self._remote_path_var.get()
        if hasattr(self, "_vfs_var"):
            updates["vfs_cache_mode"] = self._vfs_var.get()
        if hasattr(self, "_cache_size_var"):
            updates["vfs_cache_max_size"] = self._cache_size_var.get()
        if hasattr(self, "_resync_var"):
            updates["use_resync"] = self._resync_var.get()

        # Panel 2 – directory
        if hasattr(self, "_local_path_var"):
            updates["local_path"] = self._local_path_var.get()
        if hasattr(self, "_remote_dir_var"):
            updates["remote_path"] = self._remote_dir_var.get()

        # Panel 3 – exclusions
        if hasattr(self, "_personal_vault_var"):
            updates["exclude_personal_vault"] = self._personal_vault_var.get()
        if hasattr(self, "_excl_text"):
            raw = self._excl_text.get("1.0", tk.END).strip()
            # Collect non-empty custom patterns, excluding the personal vault
            # pattern (it is managed separately via the checkbox above)
            custom = [
                line.strip()
                for line in raw.splitlines()
                if line.strip() and line.strip() != PERSONAL_VAULT_PATTERN
            ]
            # Prepend the personal vault pattern if the checkbox is enabled
            if updates.get("exclude_personal_vault", self._svc.get("exclude_personal_vault")):
                custom = [PERSONAL_VAULT_PATTERN] + custom
            updates["exclusions"] = custom

        # Panel 4 – tree
        if hasattr(self, "_excluded_folders"):
            updates["excluded_folders"] = self._excluded_folders

        # Panel 5 – schedule
        if hasattr(self, "_interval_var"):
            label = self._interval_var.get()
            updates["sync_interval"] = INTERVAL_OPTIONS.get(label, DEFAULT_SYNC_INTERVAL)
        if hasattr(self, "_startup_var"):
            self._config.set_preference("start_with_system", self._startup_var.get())
        if hasattr(self, "_startup_delay_var"):
            self._config.set_preference("startup_delay_seconds", self._startup_delay_var.get())

        # Cancel the disk refresh timer if it exists
        if hasattr(self, "_disk_refresh_id"):
            self._win.after_cancel(self._disk_refresh_id)

        self._config.update_service(self._service_name, updates)

        messagebox.showinfo("Guardado", "Los cambios han sido guardados correctamente.", parent=self._win)

        self._win.destroy()
        if self._on_saved:
            self._on_saved()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_panel(self, title: str) -> tk.Frame:
        """Create and return a padded content frame with a title label."""
        frame = tk.Frame(self._content, bg="white", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text=title,
            font=("Segoe UI", 13, "bold"),
            bg="white",
        ).pack(anchor="w", pady=(0, 12))

        return frame
