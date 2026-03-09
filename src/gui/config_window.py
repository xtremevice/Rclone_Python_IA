"""
Service configuration window.

Shows a left-side menu with 8 sections and a corresponding right-side panel:
  1. Default configuration
  2. Change directory (local / remote)
  3. Exclusions management
  4. Folder tree with sync toggle
  5. Sync schedule & startup options
  6. Free disk space / delete service
  7. Service information
  8. Errors

Window size: 60 % of screen height × 35 % of screen width.
"""

import os
import threading
import time
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
from src.gui.setup_wizard import _center_window, _OAUTH_TIMEOUT_SECONDS

# Import type for annotation; avoid circular imports at runtime by using TYPE_CHECKING
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.gui.error_logger import ErrorLogger


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

    Displays eight option panels in a left-side menu.
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
        error_logger: Optional["ErrorLogger"] = None,
    ) -> None:
        # Store references
        self._config = config_manager
        self._rclone = rclone_manager
        self._service_name = service_name
        self._on_saved = on_saved
        self._on_deleted = on_deleted
        # Optional ErrorLogger instance; may be None if not provided
        self._error_logger = error_logger

        # Load a mutable copy of the service data
        svc = config_manager.get_service(service_name) or {}
        self._svc: Dict = dict(svc)

        # Create top-level window
        self._win = tk.Toplevel(parent)
        self._win.title(f"Configuración – {service_name}")
        self._win.resizable(False, False)
        _center_window(self._win, height_pct=0.60, width_pct=0.35)

        # Build exclusion checklist data before any panel is rendered so
        # _toggle_tree_item (Panel 4) can mutate it even while Panel 3 is hidden.
        self._excl_items: List[Dict] = self._build_excl_items()

        # Persist excluded-folder list across panel switches (initialised once
        # here so _panel_tree never resets it on re-entry).
        self._excluded_folders: List[str] = list(self._svc.get("excluded_folders", []))

        # Remote tree cache – populated on first fetch; re-used when the user
        # re-visits Panel 4 or when the config window is opened.
        self._remote_tree_cache: List[Dict] = []

        self._build_layout()
        # Show the first panel by default
        self._show_panel(0)

        # Kick off a background tree fetch immediately so the data is likely
        # ready by the time the user navigates to Panel 4.
        self._prefetch_tree()

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
            "8. Errores",
            "9. Montaje",
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
            self._panel_errors,
            self._panel_mount,
        ]
        panels[index]()

    # ------------------------------------------------------------------
    # Panel 1 – Default configuration
    # ------------------------------------------------------------------

    def _panel_defaults(self) -> None:
        """Panel showing and editing the default rclone sync options."""
        p = self._make_panel("Configuración por defecto")

        tk.Label(p, text="Estas opciones se aplican al ejecutar bisync para este servicio.", wraplength=450, justify="left").pack(anchor="w", pady=(0, 10))

        # Service name (editable; changing it renames the service)
        tk.Label(p, text="Nombre del servicio:", anchor="w").pack(anchor="w")
        self._service_name_var = tk.StringVar(value=self._service_name)
        tk.Entry(p, textvariable=self._service_name_var, width=40).pack(anchor="w", pady=(2, 10))

        # Remote path (from root by default)
        tk.Label(p, text="Ruta remota base:", anchor="w").pack(anchor="w")
        self._remote_path_var = tk.StringVar(value=self._svc.get("remote_path", "/"))
        tk.Entry(p, textvariable=self._remote_path_var, width=40).pack(anchor="w", pady=(2, 10))

        # VFS cache mode (used by the mount command; not applicable to bisync)
        tk.Label(p, text="Modo de caché VFS (solo para montaje):", anchor="w").pack(anchor="w")
        self._vfs_var = tk.StringVar(value=self._svc.get("vfs_cache_mode", "writes"))
        cache_modes = ["off", "minimal", "writes", "full"]
        ttk.Combobox(p, textvariable=self._vfs_var, values=cache_modes, state="readonly", width=20).pack(anchor="w", pady=(2, 10))

        # Max cache size
        tk.Label(p, text="Tamaño máximo de caché:", anchor="w").pack(anchor="w")
        self._cache_size_var = tk.StringVar(value=self._svc.get("vfs_cache_max_size", "10G"))
        tk.Entry(p, textvariable=self._cache_size_var, width=15).pack(anchor="w", pady=(2, 10))

        # Cache directory
        tk.Label(p, text="Directorio de caché (vacío = predeterminado de rclone):", anchor="w").pack(anchor="w")
        cache_dir_frame = tk.Frame(p)
        cache_dir_frame.pack(fill=tk.X, pady=(2, 10))
        self._cache_dir_var = tk.StringVar(value=self._svc.get("vfs_cache_dir", ""))
        tk.Entry(cache_dir_frame, textvariable=self._cache_dir_var, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            cache_dir_frame,
            text="…",
            command=self._browse_cache_dir,
        ).pack(side=tk.LEFT, padx=4)

        # Resync checkbox
        self._resync_var = tk.BooleanVar(value=self._svc.get("use_resync", True))
        tk.Checkbutton(p, text="Usar --resync al detectar conflictos", variable=self._resync_var).pack(anchor="w", pady=5)

        # Resync mode (conflict resolution strategy used with --resync)
        tk.Label(p, text="Modo de resolución de conflictos (--resync-mode):", anchor="w").pack(anchor="w", pady=(10, 0))
        self._resync_mode_var = tk.StringVar(value=self._svc.get("resync_mode", "newer"))
        resync_modes = ["newer", "older", "larger", "path1", "path2", "union"]
        ttk.Combobox(p, textvariable=self._resync_mode_var, values=resync_modes, state="readonly", width=15).pack(anchor="w", pady=(2, 10))

        # Verbose sync
        self._verbose_sync_var = tk.BooleanVar(value=self._svc.get("verbose_sync", False))
        tk.Checkbutton(p, text="Activar --verbose en sincronización (más detalles en el registro)", variable=self._verbose_sync_var).pack(anchor="w", pady=5)

        # --- Delete service ---
        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(20, 10))
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
        ).pack(anchor="w", pady=(0, 4))
        tk.Label(
            p,
            text="Elimina la configuración de este servicio. Los archivos locales no se borrarán.",
            fg="gray",
            font=("Segoe UI", 9),
        ).pack(anchor="w")

    def _browse_cache_dir(self) -> None:
        """Open folder picker to select a custom VFS cache directory."""
        current = self._cache_dir_var.get().strip() or os.path.expanduser("~")
        folder = filedialog.askdirectory(initialdir=current, parent=self._win)
        if folder:
            self._cache_dir_var.set(folder)

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
    # Panel 3 – Exclusions (checklist)
    # ------------------------------------------------------------------

    def _build_excl_items(self) -> List[Dict]:
        """
        Build the initial exclusion checklist from the saved service config.

        The personal-vault pattern is always present as the first entry
        (checked / unchecked according to the saved 'exclude_personal_vault'
        flag), followed by any other saved patterns.
        """
        items: List[Dict] = []
        existing: List[str] = list(self._svc.get("exclusions", []))

        # Personal vault – always first, label as "recomendado"
        # Prefer the authoritative pattern list when it exists; fall back to
        # the legacy boolean flag for configs that predate the list approach.
        if existing:
            pv_enabled = PERSONAL_VAULT_PATTERN in existing
        else:
            pv_enabled = bool(self._svc.get("exclude_personal_vault", True))
        items.append({
            "pattern": PERSONAL_VAULT_PATTERN,
            "var": tk.BooleanVar(value=pv_enabled),
            "recommended": True,
        })

        # All other saved patterns
        for pattern in existing:
            if pattern != PERSONAL_VAULT_PATTERN:
                items.append({
                    "pattern": pattern,
                    "var": tk.BooleanVar(value=True),
                    "recommended": False,
                })

        return items

    def _panel_exclusions(self) -> None:
        """Panel to manage exclusion glob patterns as an interactive checklist."""
        p = self._make_panel("Excepciones")

        tk.Label(
            p,
            text=(
                "Activa o desactiva los patrones que deseas excluir de la sincronización. "
                "Las carpetas desmarcadas en el árbol de carpetas se añaden aquí automáticamente."
            ),
            wraplength=400,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # ── Scrollable checklist ──────────────────────────────────────
        list_outer = tk.Frame(p, bd=1, relief=tk.SUNKEN)
        list_outer.pack(fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(list_outer, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._excl_canvas = tk.Canvas(
            list_outer, yscrollcommand=sb.set, bg="white", highlightthickness=0
        )
        self._excl_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self._excl_canvas.yview)

        self._excl_inner = tk.Frame(self._excl_canvas, bg="white")
        self._excl_canvas_win_id = self._excl_canvas.create_window(
            (0, 0), window=self._excl_inner, anchor="nw"
        )

        self._excl_inner.bind(
            "<Configure>",
            lambda e: self._excl_canvas.configure(
                scrollregion=self._excl_canvas.bbox("all")
            ),
        )
        self._excl_canvas.bind(
            "<Configure>",
            lambda e: self._excl_canvas.itemconfig(
                self._excl_canvas_win_id, width=e.width
            ),
        )

        self._render_excl_rows()

        # ── Add button ────────────────────────────────────────────────
        btn_frame = tk.Frame(p, bg="white")
        btn_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Button(
            btn_frame,
            text="➕ Agregar patrón",
            command=self._add_excl_pattern,
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

    def _render_excl_rows(self) -> None:
        """Repopulate the exclusion checklist inner frame from ``_excl_items``.

        This method is safe to call at any time; it is a no-op when Panel 3
        has not been rendered yet (``_excl_inner`` attribute absent) or has
        already been destroyed (``TclError``).
        """
        # _excl_inner is only set when Panel 3 is rendered; guard against
        # calls from _toggle_tree_item while a different panel is visible.
        if not hasattr(self, "_excl_inner"):
            return
        try:
            for w in self._excl_inner.winfo_children():
                w.destroy()
        except tk.TclError:
            return

        for item in self._excl_items:
            row = tk.Frame(self._excl_inner, bg="white")
            row.pack(fill=tk.X, padx=4, pady=1)

            tk.Checkbutton(row, variable=item["var"], bg="white").pack(side=tk.LEFT)

            tk.Label(
                row,
                text=item["pattern"],
                bg="white",
                anchor="w",
                font=("Segoe UI", 9),
            ).pack(side=tk.LEFT)

            if item.get("recommended"):
                tk.Label(
                    row,
                    text="(recomendado)",
                    fg="gray",
                    bg="white",
                    font=("Segoe UI", 8, "italic"),
                ).pack(side=tk.LEFT, padx=(4, 0))

            # Capture pattern string, not a mutable index, so removal is safe
            # even if the list is mutated between renders.
            tk.Button(
                row,
                text="✕",
                command=lambda pat=item["pattern"]: self._remove_excl_by_pattern(pat),
                relief=tk.FLAT,
                bg="white",
                fg="#c50f1f",
                font=("Segoe UI", 8),
                cursor="hand2",
            ).pack(side=tk.RIGHT)

        try:
            self._excl_inner.update_idletasks()
            self._excl_canvas.configure(scrollregion=self._excl_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _add_excl_pattern(self) -> None:
        """Ask the user for a new glob pattern and add it to the checklist."""
        pattern = simpledialog.askstring(
            "Agregar patrón",
            "Ingresa el patrón de exclusión:\nEjemplo: /Documentos/**",
            parent=self._win,
        )
        if not pattern or not pattern.strip():
            return
        pattern = pattern.strip()
        if any(i["pattern"] == pattern for i in self._excl_items):
            messagebox.showwarning(
                "Duplicado",
                f"El patrón '{pattern}' ya existe en la lista.",
                parent=self._win,
            )
            return
        self._excl_items.append({
            "pattern": pattern,
            "var": tk.BooleanVar(value=True),
            "recommended": False,
        })
        self._render_excl_rows()

    def _remove_excl_by_pattern(self, pattern: str) -> None:
        """Remove the exclusion item matching *pattern* and refresh the list."""
        self._excl_items = [i for i in self._excl_items if i["pattern"] != pattern]
        self._render_excl_rows()

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

        # item_id → folder path mapping (rebuilt whenever the tree is populated)
        self._tree_items: Dict[str, str] = {}

        btn_frame = tk.Frame(p)
        btn_frame.pack(fill=tk.X, pady=(8, 0))

        tk.Button(btn_frame, text="🔄 Actualizar carpetas", command=self._load_tree).pack(side=tk.LEFT)

        # If we already have cached data, populate immediately without a new
        # network request so the user's previous selections are preserved.
        if self._remote_tree_cache:
            self._populate_tree(self._remote_tree_cache)
        else:
            tk.Label(
                btn_frame,
                text="  Cargando…",
                fg="gray",
                font=("Segoe UI", 9, "italic"),
            ).pack(side=tk.LEFT, padx=(6, 0))

    def _prefetch_tree(self) -> None:
        """Fetch the remote folder list silently in a background thread.

        Result is stored in ``_remote_tree_cache``.  If Panel 4 happens to be
        visible when the fetch completes, the tree is populated automatically.
        """
        def fetch() -> None:
            items = self._rclone.list_remote_tree(self._service_name)
            self._remote_tree_cache = items
            # If Panel 4 is currently visible, populate the treeview.
            try:
                self._win.after(0, lambda: self._populate_tree_if_visible(items))
            except tk.TclError:
                pass  # Window was closed before fetch completed

        threading.Thread(target=fetch, daemon=True).start()

    def _populate_tree_if_visible(self, items: List[Dict]) -> None:
        """Populate the treeview only if Panel 4 is currently rendered."""
        if hasattr(self, "_tree"):
            try:
                if self._tree.winfo_exists():
                    self._populate_tree(items)
            except tk.TclError:
                pass

    def _load_tree(self) -> None:
        """Fetch the remote folder list in a background thread (user-triggered)."""
        if hasattr(self, "_tree"):
            self._tree.delete(*self._tree.get_children())
        self._tree_items = {}

        def fetch() -> None:
            items = self._rclone.list_remote_tree(self._service_name)
            self._remote_tree_cache = items
            try:
                self._win.after(0, lambda: self._populate_tree(items))
            except tk.TclError:
                pass  # Window was closed before fetch completed

        threading.Thread(target=fetch, daemon=True).start()

    def _populate_tree(self, items: List[Dict]) -> None:
        """Insert fetched items into the treeview, rebuilding the id→path map."""
        if not hasattr(self, "_tree"):
            return
        try:
            self._tree.delete(*self._tree.get_children())
        except tk.TclError:
            return
        self._tree_items = {}
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
        pattern = f"{path}/**"
        if path in self._excluded_folders:
            # Re-including: remove from excluded folders and exclusions checklist
            self._excluded_folders.remove(path)
            self._tree.item(item_id, values=("✅ Sí",))
            self._excl_items = [i for i in self._excl_items if i["pattern"] != pattern]
        else:
            # Excluding: add to excluded folders and exclusions checklist
            self._excluded_folders.append(path)
            self._tree.item(item_id, values=("❌ No",))
            if not any(i["pattern"] == pattern for i in self._excl_items):
                self._excl_items.append({
                    "pattern": pattern,
                    "var": tk.BooleanVar(value=True),
                    "recommended": False,
                })
        # Refresh the exclusions checklist if Panel 3 is currently visible
        self._render_excl_rows()

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

        # ── Reconnect section ────────────────────────────────────────────────
        # Shown for all OAuth-based platforms (non-Mega).  Lets the user re-run
        # the OAuth flow to fix a missing drive_id/drive_type in rclone.conf,
        # which causes bisync to fail immediately with "unable to get drive_id
        # and drive_type - if you are upgrading from older versions of rclone".
        platform = self._svc.get("platform", "")
        if platform and platform != "mega":
            sep = tk.Frame(p, height=1, bg="#cccccc")
            sep.pack(fill=tk.X, pady=(12, 8))

            tk.Label(
                p,
                text=(
                    "Si la sincronización falla con un error de configuración "
                    "(p. ej. drive_id o drive_type ausentes), haz clic en "
                    "'Reconectar' para volver a autenticar este remoto con rclone."
                ),
                wraplength=450,
                justify="left",
                fg="#555555",
            ).pack(anchor="w", pady=(0, 8))

            # Status label updated during reconnect / drive_id search
            self._reconnect_status_var = tk.StringVar(value="")
            tk.Label(
                p,
                textvariable=self._reconnect_status_var,
                fg="gray",
                font=("Segoe UI", 9, "italic"),
                wraplength=450,
                justify="left",
            ).pack(anchor="w", pady=(0, 6))

            btn_row = tk.Frame(p)
            btn_row.pack(anchor="w")

            self._find_drive_id_btn = tk.Button(
                btn_row,
                text="🔎 Buscar drive_id automáticamente",
                command=self._start_find_drive_id,
                relief=tk.FLAT,
                bg="#107c10",
                fg="white",
                font=("Segoe UI", 9, "bold"),
            )
            self._find_drive_id_btn.pack(side=tk.LEFT, padx=(0, 8))

            self._reconnect_btn = tk.Button(
                btn_row,
                text="🔄 Reconectar",
                command=self._start_reconnect,
                relief=tk.FLAT,
                bg="#0078d4",
                fg="white",
                font=("Segoe UI", 9, "bold"),
            )
            self._reconnect_btn.pack(side=tk.LEFT)

    def _start_find_drive_id(self) -> None:
        """Search known rclone config files for drive_id/drive_type in a background thread.

        If exactly one candidate is found it is offered to the user for
        immediate patching.  If multiple candidates are found the user is
        presented with a choice.  If nothing is found the user is advised to
        use Reconectar instead.
        """
        remote_name = self._svc.get("remote_name", "")
        if not remote_name:
            messagebox.showwarning(
                "Datos insuficientes",
                "No se pudo determinar el nombre del remoto.",
                parent=self._win,
            )
            return

        self._find_drive_id_btn.configure(state=tk.DISABLED, text="Buscando…")
        self._reconnect_btn.configure(state=tk.DISABLED)
        self._reconnect_status_var.set("Buscando drive_id en configuraciones conocidas…")

        def run_search() -> None:
            candidates = self._rclone.find_drive_id_in_known_configs(remote_name)
            self._win.after(0, self._on_find_drive_id_done, candidates, remote_name)

        threading.Thread(target=run_search, daemon=True).start()

    def _on_find_drive_id_done(
        self, candidates: "list[dict]", remote_name: str
    ) -> None:
        """Called on the main thread with the search results."""
        self._find_drive_id_btn.configure(
            state=tk.NORMAL, text="🔎 Buscar drive_id automáticamente"
        )
        self._reconnect_btn.configure(state=tk.NORMAL)

        if not candidates:
            self._reconnect_status_var.set(
                "❌ No se encontró drive_id en ninguna configuración conocida. "
                "Usa 'Reconectar' para volver a autenticarte."
            )
            return

        # Build a user-readable list for the choice dialog
        choice_lines = [
            f"{i + 1}. [{c['section']}] drive_id={c['drive_id']}  "
            f"drive_type={c['drive_type']}\n   ({c['source_file']})"
            for i, c in enumerate(candidates)
        ]

        if len(candidates) == 1:
            c = candidates[0]
            msg = (
                f"Se encontró un drive_id en:\n\n"
                f"  Archivo : {c['source_file']}\n"
                f"  Sección : [{c['section']}]\n"
                f"  drive_id   = {c['drive_id']}\n"
                f"  drive_type = {c['drive_type']}\n\n"
                "¿Deseas aplicar estos valores al remoto actual?"
            )
            apply = messagebox.askyesno(
                "drive_id encontrado", msg, parent=self._win
            )
            if apply:
                self._apply_drive_id_patch(remote_name, c["drive_id"], c["drive_type"])
        else:
            # Multiple candidates — ask the user to pick one via simpledialog
            prompt = (
                f"Se encontraron {len(candidates)} candidatos. "
                "Escribe el número del que quieres aplicar (o 0 para cancelar):\n\n"
                + "\n".join(choice_lines)
            )
            raw = simpledialog.askstring(
                "Seleccionar drive_id",
                prompt,
                parent=self._win,
            )
            if raw is None:
                self._reconnect_status_var.set("")
                return
            try:
                idx = int(raw.strip()) - 1
            except ValueError:
                self._reconnect_status_var.set("❌ Entrada no válida.")
                return
            if idx < 0 or idx >= len(candidates):
                self._reconnect_status_var.set("")
                return
            c = candidates[idx]
            self._apply_drive_id_patch(remote_name, c["drive_id"], c["drive_type"])

    def _apply_drive_id_patch(
        self, remote_name: str, drive_id: str, drive_type: str
    ) -> None:
        """Write drive_id/drive_type into rclone.conf and report the result."""
        ok, error = self._rclone.patch_remote_drive_fields(
            remote_name, drive_id, drive_type
        )
        if ok:
            self._reconnect_status_var.set(
                "✅ drive_id y drive_type aplicados. Reinicia la sincronización."
            )
        else:
            self._reconnect_status_var.set(f"❌ Error al aplicar: {error}")

    def _start_reconnect(self) -> None:
        """Re-run the OAuth flow for this service's remote in a background thread.

        This fixes a rclone.conf that is missing the ``drive_id`` and
        ``drive_type`` fields that newer rclone versions require for
        OneDrive/SharePoint remotes.  Calling ``rclone config create`` again
        overwrites the remote section in rclone.conf with the full set of
        fields after the user re-authenticates.
        """
        remote_name = self._svc.get("remote_name", "")
        platform = self._svc.get("platform", "")
        if not remote_name or not platform:
            messagebox.showwarning(
                "Datos insuficientes",
                "No se pudo determinar el nombre o la plataforma del remoto.",
                parent=self._win,
            )
            return

        self._reconnect_btn.configure(state=tk.DISABLED, text="Reconectando…")
        self._find_drive_id_btn.configure(state=tk.DISABLED)
        self._reconnect_status_var.set("Abriendo el navegador para autenticación…")

        # Keys that must be present before we consider auth complete.
        # OneDrive requires drive_id (written after the token) so that bisync
        # has a fully-configured remote and does not fail with exit-code 1.
        extra_keys: "tuple[str, ...]" = (
            ("drive_id",) if platform == "onedrive" else ()
        )

        def run_reconnect() -> None:
            proc = self._rclone.open_browser_auth(remote_name, platform)

            deadline = time.monotonic() + _OAUTH_TIMEOUT_SECONDS
            success = False
            while time.monotonic() < deadline:
                ret = proc.poll()
                if ret is not None:
                    success = ret == 0
                    break
                if self._rclone.remote_has_token(remote_name, extra_required_keys=extra_keys):
                    time.sleep(0.5)
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    success = True
                    break
                time.sleep(1)
            else:
                try:
                    proc.terminate()
                except OSError:
                    pass

            if success:
                self._win.after(0, self._reconnect_success)
            else:
                self._win.after(0, self._reconnect_failed)

        threading.Thread(target=run_reconnect, daemon=True).start()

    def _reconnect_success(self) -> None:
        """Called on the main thread after successful re-authentication."""
        self._reconnect_status_var.set("✅ Reconexión completada. Reinicia la sincronización.")
        self._reconnect_btn.configure(state=tk.NORMAL, text="🔄 Reconectar")
        self._find_drive_id_btn.configure(state=tk.NORMAL)

    def _reconnect_failed(self) -> None:
        """Called on the main thread if re-authentication failed or timed out."""
        self._reconnect_status_var.set("❌ La autenticación falló o superó el tiempo límite. Intenta de nuevo.")
        self._reconnect_btn.configure(state=tk.NORMAL, text="🔄 Reconectar")
        self._find_drive_id_btn.configure(state=tk.NORMAL)

    # ------------------------------------------------------------------
    # Panel 8 – Errors
    # ------------------------------------------------------------------

    def _panel_errors(self) -> None:
        """Panel displaying the application error log with copy and export actions."""
        p = self._make_panel("Errores")

        tk.Label(
            p,
            text=(
                "Registro de errores ocurridos durante la ejecución de la aplicación.\n"
                "Los mensajes más recientes aparecen primero."
            ),
            wraplength=450,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # ── Scrollable text box ──────────────────────────────────────
        text_frame = tk.Frame(p, bd=1, relief=tk.SUNKEN)
        text_frame.pack(fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(text_frame, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._errors_text = tk.Text(
            text_frame,
            yscrollcommand=sb.set,
            wrap=tk.WORD,
            font=("Courier", 9),
            bg="white",
            state=tk.DISABLED,
            relief=tk.FLAT,
        )
        self._errors_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self._errors_text.yview)

        # Populate the text box
        self._refresh_errors_text()

        # ── Action buttons ───────────────────────────────────────────
        btn_frame = tk.Frame(p, bg="white")
        btn_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Button(
            btn_frame,
            text="📋 Copiar selección",
            command=self._copy_selected_errors,
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_frame,
            text="📋 Copiar todo",
            command=self._copy_all_errors,
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_frame,
            text="💾 Exportar",
            command=self._export_errors,
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_frame,
            text="🔄 Actualizar",
            command=self._refresh_errors_text,
            relief=tk.FLAT,
            bg="#e0e0e0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

    def _refresh_errors_text(self) -> None:
        """Reload the error log text from the logger into the text widget."""
        if not hasattr(self, "_errors_text"):
            return
        try:
            if not self._errors_text.winfo_exists():
                return
        except tk.TclError:
            return

        if self._error_logger is not None:
            content = self._error_logger.get_text_for_service(self._service_name)
        else:
            content = "(Registro de errores no disponible)"

        self._errors_text.configure(state=tk.NORMAL)
        self._errors_text.delete("1.0", tk.END)
        self._errors_text.insert(tk.END, content if content else "(Sin errores registrados)")
        self._errors_text.configure(state=tk.DISABLED)

    def _copy_selected_errors(self) -> None:
        """Copy any selected text from the errors text box to the clipboard."""
        if not hasattr(self, "_errors_text"):
            return
        try:
            selected = self._errors_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self._win.clipboard_clear()
            self._win.clipboard_append(selected)
        except tk.TclError:
            # No selection – copy nothing
            pass

    def _copy_all_errors(self) -> None:
        """Copy all error log text to the clipboard."""
        if not hasattr(self, "_errors_text"):
            return
        text = self._errors_text.get("1.0", tk.END).strip()
        if text:
            self._win.clipboard_clear()
            self._win.clipboard_append(text)

    def _export_errors(self) -> None:
        """Save the error log to a user-chosen text file."""
        if not hasattr(self, "_errors_text"):
            return
        text = self._errors_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo(
                "Sin errores",
                "No hay errores registrados para exportar.",
                parent=self._win,
            )
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Texto", "*.txt"), ("Todos los archivos", "*.*")],
            initialfile="errores_rclone.txt",
            parent=self._win,
            title="Exportar registro de errores",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            messagebox.showinfo(
                "Exportado",
                f"El registro de errores se guardó en:\n{path}",
                parent=self._win,
            )
        except (OSError, IOError) as exc:
            messagebox.showerror(
                "Error al exportar",
                f"No se pudo guardar el archivo:\n{exc}",
                parent=self._win,
            )

    # ------------------------------------------------------------------
    # Panel 9 – Mount
    # ------------------------------------------------------------------

    def _panel_mount(self) -> None:
        """Panel to configure and control the persistent rclone mount service."""
        p = self._make_panel("Montaje (rclone mount)")

        tk.Label(
            p,
            text=(
                "Ejecuta un proceso de montaje permanente que hace que el almacenamiento "
                "remoto aparezca como una carpeta local del sistema.\n"
                "Los ajustes de caché VFS (modo, tamaño, directorio) se comparten con bisync "
                "y se configuran en la sección 1."
            ),
            wraplength=450,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # Enable mount checkbox
        self._mount_enabled_var = tk.BooleanVar(value=self._svc.get("mount_enabled", False))
        tk.Checkbutton(
            p,
            text="Activar montaje automático al iniciar la aplicación",
            variable=self._mount_enabled_var,
        ).pack(anchor="w", pady=(0, 10))

        # Mount path
        tk.Label(p, text="Directorio de montaje (punto de montaje local):", anchor="w").pack(anchor="w")
        mount_frame = tk.Frame(p)
        mount_frame.pack(fill=tk.X, pady=(2, 10))
        self._mount_path_var = tk.StringVar(value=self._svc.get("mount_path", ""))
        tk.Entry(mount_frame, textvariable=self._mount_path_var, width=45).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(mount_frame, text="…", command=self._browse_mount_dir).pack(side=tk.LEFT, padx=4)

        # VFS read chunk size
        tk.Label(p, text="Tamaño de bloque de lectura VFS (--vfs-read-chunk-size):", anchor="w").pack(anchor="w")
        self._vfs_read_chunk_var = tk.StringVar(value=self._svc.get("vfs_read_chunk_size", "10M"))
        tk.Entry(p, textvariable=self._vfs_read_chunk_var, width=15).pack(anchor="w", pady=(2, 10))

        # VFS read chunk size limit
        tk.Label(p, text="Límite de bloque de lectura VFS (--vfs-read-chunk-size-limit):", anchor="w").pack(anchor="w")
        self._vfs_read_chunk_limit_var = tk.StringVar(value=self._svc.get("vfs_read_chunk_size_limit", "100M"))
        tk.Entry(p, textvariable=self._vfs_read_chunk_limit_var, width=15).pack(anchor="w", pady=(2, 10))

        tk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        # Live mount controls
        mount_status = "Montado ✅" if self._rclone.is_mounted(self._service_name) else "No montado ❌"
        self._mount_status_var = tk.StringVar(value=mount_status)
        tk.Label(p, textvariable=self._mount_status_var, font=("Segoe UI", 10, "bold"), fg="#0078d4").pack(anchor="w", pady=(0, 8))

        btn_row = tk.Frame(p)
        btn_row.pack(anchor="w")
        tk.Button(
            btn_row,
            text="▶ Iniciar montaje ahora",
            command=self._start_mount_now,
            bg="#107c10",
            fg="white",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=4,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="⏹ Detener montaje",
            command=self._stop_mount_now,
            bg="#c50f1f",
            fg="white",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=4,
        ).pack(side=tk.LEFT)

    def _browse_mount_dir(self) -> None:
        """Open folder picker to select the mount point directory."""
        current = self._mount_path_var.get().strip() or os.path.expanduser("~")
        folder = filedialog.askdirectory(initialdir=current, parent=self._win)
        if folder:
            self._mount_path_var.set(folder)

    def _start_mount_now(self) -> None:
        """Apply mount path from the entry and start the mount immediately."""
        # Temporarily update the service config with the current UI values so
        # start_mount() uses the latest settings without requiring a full save.
        mount_path = self._mount_path_var.get().strip()
        if not mount_path:
            messagebox.showwarning(
                "Sin ruta",
                "Configura el directorio de montaje antes de iniciar.",
                parent=self._win,
            )
            return
        self._config.update_service(self._service_name, {
            "mount_enabled": True,
            "mount_path": mount_path,
            "vfs_read_chunk_size": self._vfs_read_chunk_var.get().strip(),
            "vfs_read_chunk_size_limit": self._vfs_read_chunk_limit_var.get().strip(),
        })
        self._svc = dict(self._config.get_service(self._service_name) or {})
        ok = self._rclone.start_mount(self._service_name)
        if ok:
            self._mount_status_var.set("Montado ✅")
        else:
            self._mount_status_var.set("Error al montar ❌")

    def _stop_mount_now(self) -> None:
        """Stop the running mount process immediately."""
        self._rclone.stop_mount(self._service_name)
        self._mount_status_var.set("No montado ❌")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_all(self) -> None:
        """Collect all panel data and persist it to the config."""
        updates: Dict = {}

        # Panel 1 – defaults (service name + rclone options)
        new_name = self._service_name_var.get().strip()
        if new_name and new_name != self._service_name:
            # Include the new name in the update dict; update_service() will
            # overwrite the 'name' key while looking up by the old name.
            updates["name"] = new_name
        if hasattr(self, "_remote_path_var"):
            updates["remote_path"] = self._remote_path_var.get()
        if hasattr(self, "_vfs_var"):
            updates["vfs_cache_mode"] = self._vfs_var.get()
        if hasattr(self, "_cache_size_var"):
            updates["vfs_cache_max_size"] = self._cache_size_var.get()
        if hasattr(self, "_cache_dir_var"):
            updates["vfs_cache_dir"] = self._cache_dir_var.get().strip()
        if hasattr(self, "_resync_var"):
            updates["use_resync"] = self._resync_var.get()
        if hasattr(self, "_resync_mode_var"):
            updates["resync_mode"] = self._resync_mode_var.get()
        if hasattr(self, "_verbose_sync_var"):
            updates["verbose_sync"] = self._verbose_sync_var.get()

        # Panel 2 – directory
        if hasattr(self, "_local_path_var"):
            updates["local_path"] = self._local_path_var.get()
        if hasattr(self, "_remote_dir_var"):
            updates["remote_path"] = self._remote_dir_var.get()

        # Panel 3 – exclusions (checklist-based)
        if hasattr(self, "_excl_items"):
            enabled_patterns = [
                item["pattern"]
                for item in self._excl_items
                if item["var"].get()
            ]
            updates["exclusions"] = enabled_patterns
            vault_item = next(
                (i for i in self._excl_items if i["pattern"] == PERSONAL_VAULT_PATTERN),
                None,
            )
            updates["exclude_personal_vault"] = vault_item is not None and vault_item["var"].get()

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

        # Panel 9 – mount
        if hasattr(self, "_mount_enabled_var"):
            updates["mount_enabled"] = self._mount_enabled_var.get()
        if hasattr(self, "_mount_path_var"):
            updates["mount_path"] = self._mount_path_var.get().strip()
        if hasattr(self, "_vfs_read_chunk_var"):
            updates["vfs_read_chunk_size"] = self._vfs_read_chunk_var.get().strip()
        if hasattr(self, "_vfs_read_chunk_limit_var"):
            updates["vfs_read_chunk_size_limit"] = self._vfs_read_chunk_limit_var.get().strip()

        self._config.update_service(self._service_name, updates)

        # If the name changed, update internal state and the window title so
        # they stay consistent.  The main window rebuilds its tabs via on_saved.
        if updates.get("name") and updates["name"] != self._service_name:
            self._service_name = updates["name"]
            self._win.title(f"Configuración – {self._service_name}")

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
