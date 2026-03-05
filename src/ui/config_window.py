"""
config_window.py
----------------
Service configuration window with a left sidebar menu (7 options).

Menu options:
  1. Configuración por defecto   – default sync settings toggles
  2. Directorios                 – change local/remote paths
  3. Excepciones                 – manage exclusion patterns
  4. Carpetas                    – tree view with sync checkboxes
  5. Intervalo de sincronización – frequency + startup options
  6. Espacio en disco            – free space + delete service
  7. Información del servicio    – read-only metadata

Save button at the bottom persists all pending changes.

Window size: 60% screen height × 70% screen width.
"""

import platform as _platform
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.core.service_manager import ServiceManager
from src.ui.utils import (
    COLORS,
    apply_theme,
    platform_display_name,
    set_window_size_percent,
)


class ConfigWindow(tk.Toplevel):
    """Configuration window for a single service with 7 sidebar menu options."""

    def __init__(self, parent, service_manager: ServiceManager, service_id: str):
        """
        Initialise the configuration window.

        Parameters
        ----------
        parent          : Parent tk window
        service_manager : Shared ServiceManager instance
        service_id      : ID of the service to configure
        """
        super().__init__(parent)
        self.service_manager = service_manager
        self.service_id = service_id
        # Fetch current service config from the manager
        self._svc = service_manager.config.get_service(service_id)
        if not self._svc:
            self.destroy()
            return

        # Pending changes – written to config only when Save is clicked
        self._pending: dict = {}

        # --- Window setup ---
        self.title(f"Configuración – {self._svc.get('name', '')}")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        set_window_size_percent(self, w_pct=0.70, h_pct=0.60)
        apply_theme(self)

        # Build layout: sidebar on the left, content pane on the right
        self._build_layout()
        # Default to the first menu item
        self._show_section(0)

    # ------------------------------------------------------------------
    # Layout skeleton
    # ------------------------------------------------------------------

    def _build_layout(self):
        """Create the outer 2-column layout (sidebar + content area)."""
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        # -- Left sidebar --
        self._sidebar = tk.Frame(outer, bg=COLORS["sidebar_bg"], width=180)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # Sidebar title
        tk.Label(
            self._sidebar,
            text="Configuración",
            bg=COLORS["sidebar_bg"],
            fg=COLORS["sidebar_fg"],
            font=("Segoe UI", 11, "bold") if _platform.system() == "Windows" else ("Helvetica", 11, "bold"),
            pady=12,
        ).pack(fill="x", padx=10)

        # Sidebar menu buttons
        menu_items = [
            "1. Por defecto",
            "2. Directorios",
            "3. Excepciones",
            "4. Carpetas",
            "5. Intervalo",
            "6. Espacio en disco",
            "7. Información",
        ]
        self._sidebar_btns = []
        for idx, label in enumerate(menu_items):
            btn = tk.Button(
                self._sidebar,
                text=label,
                bg=COLORS["sidebar_bg"],
                fg=COLORS["sidebar_fg"],
                activebackground=COLORS["sidebar_selected"],
                activeforeground=COLORS["sidebar_fg"],
                relief="flat",
                anchor="w",
                padx=14,
                pady=8,
                cursor="hand2",
                command=lambda i=idx: self._show_section(i),
            )
            btn.pack(fill="x")
            self._sidebar_btns.append(btn)

        # -- Right content pane --
        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True)

        # Content area (swapped per section)
        self._content = ttk.Frame(right)
        self._content.pack(fill="both", expand=True, padx=20, pady=16)

        ttk.Separator(right, orient="horizontal").pack(fill="x", side="bottom")

        # -- Save button at bottom --
        btn_frame = ttk.Frame(right, style="Surface.TFrame")
        btn_frame.pack(fill="x", side="bottom", padx=20, pady=10)
        ttk.Button(
            btn_frame,
            text="💾 Guardar cambios",
            style="Primary.TButton",
            command=self._save_all,
        ).pack(side="right")
        ttk.Button(
            btn_frame,
            text="Cancelar",
            style="Secondary.TButton",
            command=self.destroy,
        ).pack(side="right", padx=(0, 8))

    # ------------------------------------------------------------------
    # Sidebar navigation
    # ------------------------------------------------------------------

    def _show_section(self, index: int):
        """Clear the content pane and display the selected section."""
        # Highlight selected sidebar button
        for i, btn in enumerate(self._sidebar_btns):
            if i == index:
                btn.config(bg=COLORS["sidebar_selected"], fg=COLORS["sidebar_fg"])
            else:
                btn.config(bg=COLORS["sidebar_bg"], fg=COLORS["sidebar_fg"])

        # Remove current content
        for widget in self._content.winfo_children():
            widget.destroy()

        # Render the selected section
        builders = [
            self._section_defaults,
            self._section_directories,
            self._section_exceptions,
            self._section_folders,
            self._section_interval,
            self._section_disk_space,
            self._section_info,
        ]
        builders[index]()

    # ------------------------------------------------------------------
    # Section 1 – Default settings
    # ------------------------------------------------------------------

    def _section_defaults(self):
        """
        Render the default rclone settings section.
        Shows toggle checkbuttons for the main sync behaviour flags.
        """
        self._section_title("Configuración por defecto")

        # Local copies of the current setting values backed by BooleanVars
        self._var_root = tk.BooleanVar(value=self._svc.get("sync_from_root", True))
        self._var_ondemand = tk.BooleanVar(value=self._svc.get("on_demand", True))
        self._var_resync = tk.BooleanVar(value=self._svc.get("resync", True))
        self._var_vault = tk.BooleanVar(value=self._svc.get("exclude_personal_vault", True))

        # Each checkbox toggles a sync flag
        options = [
            (self._var_root,     "sync_from_root",
             "Sincronizar desde el directorio raíz /",
             "Los datos se sincronizarán desde la raíz del servicio."),
            (self._var_ondemand, "on_demand",
             "Descargar archivos sólo cuando se usen (VFS on-demand)",
             "Los archivos se descargan localmente sólo cuando se abren."),
            (self._var_resync,   "resync",
             "Usar --resync en bisync",
             "Reconcilia diferencias completas entre local y la nube."),
            (self._var_vault,    "exclude_personal_vault",
             "Excluir 'Almacén personal' de OneDrive",
             "Evita errores al sincronizar el vault cifrado de OneDrive."),
        ]
        for var, key, label, desc in options:
            row = ttk.Frame(self._content)
            row.pack(fill="x", pady=6)
            cb = ttk.Checkbutton(row, variable=var, text=label,
                                 command=lambda k=key, v=var: self._pending.update({k: v.get()}))
            cb.pack(anchor="w")
            ttk.Label(row, text=f"   {desc}", style="Subtitle.TLabel").pack(anchor="w")

    # ------------------------------------------------------------------
    # Section 2 – Directories
    # ------------------------------------------------------------------

    def _section_directories(self):
        """
        Render the directory configuration section.
        Allows changing the local sync path and the remote cloud path prefix.
        """
        self._section_title("Directorios")

        # Local path
        ttk.Label(self._content, text="Carpeta local de sincronización:").pack(anchor="w", pady=(0, 4))
        self._var_local_path = tk.StringVar(value=self._svc.get("local_path", ""))
        path_row = ttk.Frame(self._content)
        path_row.pack(fill="x", pady=(0, 16))
        ttk.Entry(path_row, textvariable=self._var_local_path, width=50).pack(side="left", expand=True, fill="x")
        ttk.Button(
            path_row, text="Examinar…", style="Secondary.TButton",
            command=lambda: self._var_local_path.set(
                filedialog.askdirectory(title="Carpeta local", initialdir=str(Path.home())) or self._var_local_path.get()
            ),
        ).pack(side="left", padx=(8, 0))

        # Remote path (subdirectory within the remote)
        ttk.Label(self._content, text="Ruta dentro del servicio remoto (ej. /Documentos):").pack(anchor="w", pady=(0, 4))
        self._var_remote_path = tk.StringVar(value=self._svc.get("remote_path", "/"))
        ttk.Entry(self._content, textvariable=self._var_remote_path, width=50).pack(anchor="w", pady=(0, 8))

        ttk.Label(
            self._content,
            text="Usa '/' para sincronizar todo el almacenamiento del servicio.",
            style="Subtitle.TLabel",
        ).pack(anchor="w")

        def _apply():
            """Copy directory values into the pending changes dict."""
            self._pending["local_path"] = self._var_local_path.get().strip()
            self._pending["remote_path"] = self._var_remote_path.get().strip() or "/"

        # Track changes on variable write
        self._var_local_path.trace_add("write", lambda *_: _apply())
        self._var_remote_path.trace_add("write", lambda *_: _apply())

    # ------------------------------------------------------------------
    # Section 3 – Exceptions
    # ------------------------------------------------------------------

    def _section_exceptions(self):
        """
        Render the exclusion-pattern management section.
        Users can add/remove rclone --exclude patterns.
        """
        self._section_title("Carpetas de excepción")

        ttk.Label(
            self._content,
            text="Patrones de exclusión (formato rclone, ej. /Carpeta/**)",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        # Listbox showing current exclusion patterns
        list_frame = ttk.Frame(self._content)
        list_frame.pack(fill="both", expand=True)
        self._exc_listbox = tk.Listbox(
            list_frame, bg=COLORS["surface"], fg=COLORS["text"],
            selectbackground=COLORS["primary"], relief="flat", borderwidth=1,
            highlightthickness=1, highlightcolor=COLORS["border"],
        )
        # Populate with current exclusion patterns
        current_excludes = list(self._svc.get("excluded_paths", []))
        # Prepend the default personal vault exclusion if enabled
        if self._svc.get("exclude_personal_vault", True):
            current_excludes = ["/Almacén personal/**"] + current_excludes
        for pattern in current_excludes:
            self._exc_listbox.insert("end", pattern)
        exc_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self._exc_listbox.yview)
        self._exc_listbox.configure(yscrollcommand=exc_scroll.set)
        self._exc_listbox.pack(side="left", fill="both", expand=True)
        exc_scroll.pack(side="right", fill="y")

        # Add / Remove controls
        ctrl_row = ttk.Frame(self._content)
        ctrl_row.pack(fill="x", pady=8)
        self._exc_entry = ttk.Entry(ctrl_row, width=40)
        self._exc_entry.pack(side="left", expand=True, fill="x")
        ttk.Button(ctrl_row, text="Agregar", style="Primary.TButton",
                   command=self._exc_add).pack(side="left", padx=(8, 4))
        ttk.Button(ctrl_row, text="Quitar", style="Secondary.TButton",
                   command=self._exc_remove).pack(side="left")

    def _exc_add(self):
        """Add a new exclusion pattern to the listbox and pending changes."""
        pattern = self._exc_entry.get().strip()
        if not pattern:
            return
        self._exc_listbox.insert("end", pattern)
        self._exc_entry.delete(0, "end")
        self._sync_exc_pending()

    def _exc_remove(self):
        """Remove the selected exclusion pattern from the listbox."""
        sel = self._exc_listbox.curselection()
        if sel:
            self._exc_listbox.delete(sel[0])
            self._sync_exc_pending()

    def _sync_exc_pending(self):
        """Push the current listbox contents into pending changes (excluding default vault entry)."""
        patterns = list(self._exc_listbox.get(0, "end"))
        # Remove the hard-coded vault exclusion from the user-editable list
        patterns = [p for p in patterns if p != "/Almacén personal/**"]
        self._pending["excluded_paths"] = patterns

    # ------------------------------------------------------------------
    # Section 4 – Folder tree
    # ------------------------------------------------------------------

    def _section_folders(self):
        """
        Render the remote folder tree section.
        Fetches folder list from rclone and shows checkboxes to toggle sync per folder.
        """
        self._section_title("Carpetas del servicio")

        ttk.Label(
            self._content,
            text="Cargando árbol de carpetas…",
            style="Subtitle.TLabel",
        ).pack(anchor="w")

        # Treeview for the folder tree
        tree_frame = ttk.Frame(self._content)
        tree_frame.pack(fill="both", expand=True, pady=8)
        self._folder_tree = ttk.Treeview(
            tree_frame,
            columns=("synced",),
            show="tree headings",
            selectmode="browse",
        )
        self._folder_tree.heading("#0", text="Carpeta")
        self._folder_tree.heading("synced", text="Sincronizada")
        self._folder_tree.column("#0", stretch=True)
        self._folder_tree.column("synced", width=90, anchor="center", stretch=False)

        folder_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._folder_tree.yview)
        self._folder_tree.configure(yscrollcommand=folder_scroll.set)
        self._folder_tree.pack(side="left", fill="both", expand=True)
        folder_scroll.pack(side="right", fill="y")

        # Load folders in background to avoid blocking the UI
        threading.Thread(target=self._load_folder_tree, daemon=True).start()

    def _load_folder_tree(self):
        """Fetch the remote folder tree in a background thread and populate the Treeview."""
        remote = self._svc.get("remote_name", "")
        folders = self.service_manager.rclone.list_folder_tree(remote)
        # Update UI on main thread
        self.after(0, self._populate_folder_tree, folders)

    def _populate_folder_tree(self, folders: list):
        """
        Insert folder entries into the Treeview.

        Parameters
        ----------
        folders : List of dicts with 'path' and 'name' keys from rclone
        """
        # Clear any existing items
        self._folder_tree.delete(*self._folder_tree.get_children())
        # Track which folders are in the selected_folders list
        selected = set(self._svc.get("selected_folders", []))
        # Build a mapping of path → tree item id
        node_map: dict[str, str] = {}
        for entry in folders:
            path = entry["path"]
            name = entry["name"]
            # Determine parent: everything before the last '/'
            parts = path.rsplit("/", 1)
            parent_path = parts[0] if len(parts) > 1 else ""
            parent_node = node_map.get(parent_path, "")
            # Sync checkbox value
            is_synced = path in selected or not selected  # all by default if none configured
            synced_text = "✔" if is_synced else "✗"
            node_id = self._folder_tree.insert(
                parent_node, "end",
                text=f"  📁 {name}",
                values=(synced_text,),
                open=True,
            )
            node_map[path] = node_id

        # Bind double-click to toggle sync status
        self._folder_tree.bind("<Double-1>", self._toggle_folder_sync)

    def _toggle_folder_sync(self, event):
        """Toggle the sync checkbox for a double-clicked folder row."""
        item = self._folder_tree.identify_row(event.y)
        if not item:
            return
        current = self._folder_tree.set(item, "synced")
        new_val = "✗" if current == "✔" else "✔"
        self._folder_tree.set(item, "synced", new_val)
        # Rebuild selected_folders list from treeview state
        selected = []
        for iid in self._folder_tree.get_children(""):
            self._collect_selected(iid, selected)
        self._pending["selected_folders"] = selected

    def _collect_selected(self, node_id: str, selected: list):
        """Recursively collect all checked folder paths from the tree."""
        val = self._folder_tree.set(node_id, "synced")
        if val == "✔":
            # The folder label is stored as "  📁 name" – we use the tree structure
            selected.append(self._folder_tree.item(node_id, "text").strip().replace("📁 ", ""))
        for child in self._folder_tree.get_children(node_id):
            self._collect_selected(child, selected)

    # ------------------------------------------------------------------
    # Section 5 – Sync interval
    # ------------------------------------------------------------------

    def _section_interval(self):
        """
        Render the sync interval & startup configuration section.
        Lets the user choose how frequently to sync and whether to start with the OS.
        """
        self._section_title("Intervalo de sincronización")

        # Sync interval choices
        ttk.Label(self._content, text="Sincronizar cada:").pack(anchor="w", pady=(0, 6))
        interval_options = [
            ("1 minuto", 1),
            ("5 minutos", 5),
            ("15 minutos", 15),
            ("30 minutos", 30),
            ("1 hora", 60),
            ("2 horas", 120),
            ("3 horas", 180),
            ("6 horas", 360),
            ("12 horas", 720),
            ("24 horas", 1440),
        ]
        display_vals = [label for label, _ in interval_options]
        self._interval_map = {label: mins for label, mins in interval_options}
        # Find the current selection
        current_mins = self._svc.get("sync_interval", 15)
        current_display = next(
            (lbl for lbl, m in interval_options if m == current_mins),
            "15 minutos",
        )
        self._var_interval = tk.StringVar(value=current_display)
        interval_combo = ttk.Combobox(
            self._content,
            values=display_vals,
            textvariable=self._var_interval,
            state="readonly",
            width=20,
        )
        interval_combo.pack(anchor="w", pady=(0, 20))

        def _interval_changed(event=None):
            """Write the chosen interval in minutes to pending changes."""
            minutes = self._interval_map.get(self._var_interval.get(), 15)
            self._pending["sync_interval"] = minutes

        interval_combo.bind("<<ComboboxSelected>>", _interval_changed)

        ttk.Separator(self._content, orient="horizontal").pack(fill="x", pady=10)

        # Start with system toggle
        self._var_autostart = tk.BooleanVar(value=self._svc.get("autostart", False))
        ttk.Checkbutton(
            self._content,
            text="Iniciar este programa con el sistema operativo",
            variable=self._var_autostart,
            command=lambda: self._pending.update({"autostart": self._var_autostart.get()}),
        ).pack(anchor="w")

        # Startup delay
        delay_row = ttk.Frame(self._content)
        delay_row.pack(anchor="w", pady=8)
        ttk.Label(delay_row, text="Retraso al iniciar (segundos):").pack(side="left")
        self._var_delay = tk.IntVar(value=self._svc.get("autostart_delay", 30))
        delay_spin = ttk.Spinbox(
            delay_row,
            from_=0, to=600, increment=5,
            textvariable=self._var_delay,
            width=8,
        )
        delay_spin.pack(side="left", padx=8)

        def _delay_changed(*_):
            self._pending["autostart_delay"] = self._var_delay.get()

        self._var_delay.trace_add("write", _delay_changed)

    # ------------------------------------------------------------------
    # Section 6 – Disk space
    # ------------------------------------------------------------------

    def _section_disk_space(self):
        """
        Render the disk space management section.
        Shows current usage, allows freeing space, and offers a delete service button.
        """
        self._section_title("Espacio en disco")

        # Disk usage display
        self._disk_usage_lbl = ttk.Label(
            self._content,
            text="Calculando espacio en disco…",
            style="Subtitle.TLabel",
        )
        self._disk_usage_lbl.pack(anchor="w", pady=(0, 8))

        # Load disk usage in background
        threading.Thread(target=self._update_disk_usage_once, daemon=True).start()
        # Auto-refresh every 10 seconds while this window is open
        self._disk_refresh_job = self.after(10000, self._auto_refresh_disk)

        # Free space button
        ttk.Button(
            self._content,
            text="💾 Liberar espacio en disco",
            style="Primary.TButton",
            command=self._free_space,
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self._content,
            text="Los archivos descargados volverán a modo 'solo en la nube'.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 20))

        ttk.Separator(self._content, orient="horizontal").pack(fill="x", pady=10)

        # Delete service (danger zone)
        ttk.Label(self._content, text="Zona de peligro", foreground=COLORS["error"]).pack(anchor="w")
        ttk.Button(
            self._content,
            text="🗑 Eliminar este servicio",
            style="Danger.TButton",
            command=self._delete_service,
        ).pack(anchor="w", pady=8)

    def _update_disk_usage_once(self):
        """Fetch disk usage stats in a background thread and update the label."""
        local_path = self._svc.get("local_path", "")
        if not local_path:
            return
        used, total, free = self.service_manager.rclone.get_disk_usage(local_path)
        # Format sizes to human-readable GB / MB
        def fmt(b):
            if b >= 1_073_741_824:
                return f"{b / 1_073_741_824:.1f} GB"
            elif b >= 1_048_576:
                return f"{b / 1_048_576:.1f} MB"
            return f"{b} B"
        text = (
            f"Espacio total: {fmt(total)}  |  "
            f"Usado: {fmt(used)}  |  "
            f"Libre: {fmt(free)}"
        )
        self.after(0, self._disk_usage_lbl.config, {"text": text})

    def _auto_refresh_disk(self):
        """Schedule a disk usage refresh every 10 s while this section is visible."""
        threading.Thread(target=self._update_disk_usage_once, daemon=True).start()
        self._disk_refresh_job = self.after(10000, self._auto_refresh_disk)

    def _free_space(self):
        """Run rclone cleanup in background and show a notification when done."""
        remote = self._svc.get("remote_name", "")

        def _done(success, msg):
            self.after(0, lambda: messagebox.showinfo(
                "Espacio liberado" if success else "Error",
                msg,
                parent=self,
            ))
            # Refresh disk usage after freeing
            threading.Thread(target=self._update_disk_usage_once, daemon=True).start()

        self.service_manager.rclone.free_space(remote, on_done=_done)

    def _delete_service(self):
        """Prompt for confirmation and delete the service if confirmed."""
        answer = messagebox.askyesno(
            "Confirmar eliminación",
            f"¿Estás seguro de que deseas eliminar el servicio '{self._svc.get('name')}'?\n\n"
            "Esta acción eliminará la configuración y no se puede deshacer.",
            parent=self,
        )
        if answer:
            self.service_manager.delete_service(self.service_id)
            # Refresh the parent main window tabs
            if hasattr(self.master, "refresh_tabs"):
                self.master.after(0, self.master.refresh_tabs)
            self.destroy()

    # ------------------------------------------------------------------
    # Section 7 – Service info
    # ------------------------------------------------------------------

    def _section_info(self):
        """
        Render the read-only service information section.
        Shows account, platform, paths, interval, status, and rclone version.
        """
        self._section_title("Información del servicio")

        status = self.service_manager.get_status(self.service_id)
        last_sync = self.service_manager.get_last_sync_time(self.service_id)
        rclone_version = self.service_manager.rclone.get_version() or "rclone no encontrado"

        info_rows = [
            ("Nombre del servicio:", self._svc.get("name", "")),
            ("Plataforma:", platform_display_name(self._svc.get("platform", ""))),
            ("Remote rclone:", self._svc.get("remote_name", "")),
            ("Carpeta local:", self._svc.get("local_path", "")),
            ("Intervalo de sync:", f"{self._svc.get('sync_interval', 15)} minutos"),
            ("Estado actual:", status.capitalize()),
            ("Última sincronización:", last_sync or "Nunca"),
            ("Versión de rclone:", rclone_version),
        ]
        for label, value in info_rows:
            row = ttk.Frame(self._content)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=22, anchor="w",
                      font=("Segoe UI", 9, "bold") if _platform.system() == "Windows" else ("Helvetica", 9, "bold")).pack(side="left")
            ttk.Label(row, text=value, style="Subtitle.TLabel", wraplength=400).pack(side="left")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _section_title(self, title: str):
        """Render a section heading at the top of the content pane."""
        ttk.Label(self._content, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Separator(self._content, orient="horizontal").pack(fill="x", pady=(4, 16))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_all(self):
        """Write all pending changes to the config and close the window."""
        if self._pending:
            self.service_manager.config.update_service(self.service_id, self._pending)
            self._pending.clear()
        messagebox.showinfo(
            "Guardado",
            "Los cambios han sido guardados correctamente.",
            parent=self,
        )
        self.destroy()

    def destroy(self):
        """Cancel the auto-refresh timer before destroying the window."""
        try:
            if hasattr(self, "_disk_refresh_job") and self._disk_refresh_job:
                self.after_cancel(self._disk_refresh_job)
        except Exception:
            pass
        super().destroy()
