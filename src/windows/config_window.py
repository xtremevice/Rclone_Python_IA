"""
src/windows/config_window.py

Configuration window for a single rclone service.  A QListWidget on the left
acts as a navigation menu; each option loads a different panel on the right.

Menu options
  1. Default configuration
  2. Directories (local & remote paths)
  3. Exceptions / excluded folders
  4. Folder tree with sync checkboxes
  5. Sync interval & startup settings
  6. Disk space / free up space / delete service
  7. Service information

Window size: 60 % height × 70 % width.
"""
from typing import Optional, List

from PyQt5.QtCore import Qt, QTimer, QThread
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from src.core.config import AppConfig, ServiceConfig, SYNC_INTERVAL_OPTIONS
from src.core.rclone import (
    delete_remote,
    get_disk_usage,
    get_rclone_version,
    list_remote_folders,
    rclone_available,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_title(text: str) -> QLabel:
    """Return a bold section-title QLabel."""
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(12)
    font.setBold(True)
    lbl.setFont(font)
    return lbl


def _separator() -> QFrame:
    """Return a horizontal separator line."""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


# ---------------------------------------------------------------------------
# Individual option panels
# ---------------------------------------------------------------------------

class _DefaultConfigPanel(QWidget):
    """Option 1 – Show and edit default rclone parameters for the service."""

    def __init__(self, service: ServiceConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the default-config panel."""
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_section_title("Configuración por defecto"))
        layout.addWidget(_separator())

        # Download on demand
        self.download_on_demand_cb = QCheckBox(
            "Descargar archivos solo cuando se necesiten (VFS on-demand)"
        )
        self.download_on_demand_cb.setChecked(service.download_on_demand)
        layout.addWidget(self.download_on_demand_cb)

        # Resync
        self.resync_cb = QCheckBox(
            "Usar --resync (mantiene la nube y el disco sincronizados bidireccionalmente)"
        )
        self.resync_cb.setChecked(service.use_resync)
        layout.addWidget(self.resync_cb)

        # Exclude personal vault (OneDrive)
        self.exclude_vault_cb = QCheckBox(
            "Excluir 'Almacén personal' de OneDrive (evita errores de sincronización)"
        )
        personal_vault_rule = "/Almacén personal/**"
        self.exclude_vault_cb.setChecked(personal_vault_rule in service.exclude_rules)
        layout.addWidget(self.exclude_vault_cb)

        layout.addStretch()

    def apply_changes(self) -> None:
        """Write UI values back to the service config object."""
        self.service.download_on_demand = self.download_on_demand_cb.isChecked()
        self.service.use_resync = self.resync_cb.isChecked()
        personal_vault_rule = "/Almacén personal/**"
        if self.exclude_vault_cb.isChecked():
            if personal_vault_rule not in self.service.exclude_rules:
                self.service.exclude_rules.append(personal_vault_rule)
        else:
            self.service.exclude_rules = [
                r for r in self.service.exclude_rules if r != personal_vault_rule
            ]


class _DirectoriesPanel(QWidget):
    """Option 2 – Change the local and remote directory paths."""

    def __init__(self, service: ServiceConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the directories panel."""
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_section_title("Directorios"))
        layout.addWidget(_separator())

        # Local path
        layout.addWidget(QLabel("Carpeta local:"))
        local_row = QHBoxLayout()
        self.local_edit = QLineEdit(service.local_path)
        local_row.addWidget(self.local_edit)
        browse_btn = QPushButton("Examinar …")
        browse_btn.setFixedWidth(110)
        browse_btn.clicked.connect(self._browse_local)
        local_row.addWidget(browse_btn)
        layout.addLayout(local_row)

        # Remote path
        layout.addWidget(QLabel("Ruta remota (dentro del servicio en la nube):"))
        self.remote_edit = QLineEdit(service.remote_path)
        layout.addWidget(self.remote_edit)

        layout.addStretch()

    def _browse_local(self) -> None:
        """Open a folder dialog to choose the local directory."""
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta local", self.local_edit.text()
        )
        if folder:
            self.local_edit.setText(folder)

    def apply_changes(self) -> None:
        """Write UI values back to the service config object."""
        self.service.local_path = self.local_edit.text().strip()
        self.service.remote_path = self.remote_edit.text().strip() or "/"


class _ExceptionsPanel(QWidget):
    """Option 3 – Manage rclone --exclude rules for the service."""

    def __init__(self, service: ServiceConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the exceptions panel."""
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_section_title("Excepciones / Carpetas excluidas"))
        layout.addWidget(_separator())
        layout.addWidget(QLabel(
            "Lista de reglas de exclusión (una por línea, formato glob de rclone):"
        ))

        from PyQt5.QtWidgets import QPlainTextEdit
        self.rules_edit = QPlainTextEdit()
        self.rules_edit.setPlainText("\n".join(service.exclude_rules))
        layout.addWidget(self.rules_edit)

        layout.addWidget(QLabel(
            "Ejemplo: /Almacén personal/**   |   *.tmp   |   /backups/**"
        ))
        layout.addStretch()

    def apply_changes(self) -> None:
        """Write UI values back to the service config object."""
        text = self.rules_edit.toPlainText()
        self.service.exclude_rules = [
            line.strip() for line in text.splitlines() if line.strip()
        ]


class _FolderTreePanel(QWidget):
    """Option 4 – Show remote folders in a tree with sync checkboxes."""

    def __init__(self, service: ServiceConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the folder-tree panel."""
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_section_title("Árbol de carpetas remotas"))
        layout.addWidget(_separator())
        layout.addWidget(QLabel(
            "Marca o desmarca las carpetas que deseas sincronizar.\n"
            "(Requiere conexión activa para cargar la lista)"
        ))

        # Refresh button
        refresh_btn = QPushButton("🔄 Actualizar lista")
        refresh_btn.clicked.connect(self._load_folders)
        layout.addWidget(refresh_btn)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Carpeta", "Sincronizar"])
        self.tree.setColumnWidth(0, 300)
        layout.addWidget(self.tree)

        # Load on construction
        self._load_folders()

    def _load_folders(self) -> None:
        """Query rclone for remote folders and populate the tree."""
        self.tree.clear()
        if not rclone_available():
            root_item = QTreeWidgetItem(["rclone no disponible"])
            self.tree.addTopLevelItem(root_item)
            return

        folders = list_remote_folders(self.service.remote_name, self.service.remote_path)
        excluded = set(self.service.excluded_folders)

        for folder in folders:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, folder)
            item.setCheckState(
                1,
                Qt.Unchecked if folder in excluded else Qt.Checked,
            )
        if not folders:
            placeholder = QTreeWidgetItem(["No se encontraron carpetas"])
            self.tree.addTopLevelItem(placeholder)

    def apply_changes(self) -> None:
        """Write the unchecked folders to the service's excluded_folders list."""
        excluded: List[str] = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(1) == Qt.Unchecked:
                excluded.append(item.text(0))
        self.service.excluded_folders = excluded
        # Rebuild exclude_rules from excluded_folders plus user-defined rules
        base_rules = [
            r for r in self.service.exclude_rules
            if not any(r.startswith(f"/{f}/") for f in excluded)
        ]
        for folder in excluded:
            rule = f"/{folder}/**"
            if rule not in base_rules:
                base_rules.append(rule)
        self.service.exclude_rules = base_rules


class _SyncIntervalPanel(QWidget):
    """Option 5 – Configure sync interval and startup behaviour."""

    def __init__(self, service: ServiceConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the sync-interval panel."""
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_section_title("Intervalo de sincronización"))
        layout.addWidget(_separator())

        # Sync interval combo box
        layout.addWidget(QLabel("Sincronizar cada:"))
        self.interval_combo = QComboBox()
        current_label = "15 minutos"
        for label, minutes in SYNC_INTERVAL_OPTIONS.items():
            self.interval_combo.addItem(label, userData=minutes)
            if minutes == service.sync_interval:
                current_label = label
        self.interval_combo.setCurrentText(current_label)
        layout.addWidget(self.interval_combo)

        layout.addWidget(_separator())
        layout.addWidget(_section_title("Inicio con el sistema"))

        # Startup checkbox
        self.startup_cb = QCheckBox("Iniciar este programa con el sistema operativo")
        self.startup_cb.setChecked(service.sync_on_startup)
        layout.addWidget(self.startup_cb)

        # Startup delay
        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Retraso antes de la primera sincronización (segundos):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 3600)
        self.delay_spin.setValue(service.startup_delay)
        delay_row.addWidget(self.delay_spin)
        layout.addLayout(delay_row)

        layout.addStretch()

    def apply_changes(self) -> None:
        """Write UI values back to the service config object."""
        self.service.sync_interval = self.interval_combo.currentData()
        self.service.sync_on_startup = self.startup_cb.isChecked()
        self.service.startup_delay = self.delay_spin.value()


class _DiskSpacePanel(QWidget):
    """Option 6 – Free up disk space and optionally delete the service."""

    def __init__(
        self,
        service: ServiceConfig,
        config: AppConfig,
        on_delete_callback,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialise the disk-space panel."""
        super().__init__(parent)
        self.service = service
        self.config = config
        self.on_delete_callback = on_delete_callback
        self._refresh_timer: Optional[QTimer] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_section_title("Espacio en disco"))
        layout.addWidget(_separator())

        # Disk usage display
        usage_row = QHBoxLayout()
        usage_row.addWidget(QLabel("Espacio utilizado actualmente:"))
        self.usage_label = QLabel("Calculando …")
        usage_row.addWidget(self.usage_label)
        layout.addLayout(usage_row)

        # Free up space button
        free_btn = QPushButton("☁️  Liberar espacio (volver a solo en la nube)")
        free_btn.clicked.connect(self._free_space)
        layout.addWidget(free_btn)

        layout.addWidget(_separator())

        # Delete service button
        layout.addWidget(_section_title("Peligro"))
        delete_btn = QPushButton("🗑️  Eliminar este servicio")
        delete_btn.setStyleSheet("QPushButton { color: red; font-weight: bold; }")
        delete_btn.clicked.connect(self._delete_service)
        layout.addWidget(delete_btn)

        layout.addStretch()

        # Start auto-refresh timer (every 10 seconds while panel is visible)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(10_000)
        self._refresh_timer.timeout.connect(self._update_usage)
        self._refresh_timer.start()
        self._update_usage()

    def _update_usage(self) -> None:
        """Refresh the displayed disk-usage figure from rclone."""
        usage = get_disk_usage(self.service.remote_name, self.service.remote_path)
        self.usage_label.setText(usage)

    def _free_space(self) -> None:
        """Mark all locally cached files as cloud-only via rclone vfs/forget."""
        QMessageBox.information(
            self,
            "Liberar espacio",
            "Para liberar el espacio del caché de VFS, reinicia el servicio o "
            "usa el comando:\n\nrclone rc vfs/forget",
        )

    def _delete_service(self) -> None:
        """Ask for confirmation and then delete the service and its rclone remote."""
        answer = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Estás seguro de que deseas eliminar el servicio «{self.service.name}»?\n"
            "Esta acción eliminará la configuración y el acceso a la cuenta en la nube.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            delete_remote(self.service.remote_name)
            self.config.remove_service(self.service.id)
            if self.on_delete_callback:
                self.on_delete_callback()

    def apply_changes(self) -> None:
        """No persistent changes for this panel."""

    def closeEvent(self, event) -> None:
        """Stop the refresh timer when the panel is closed."""
        if self._refresh_timer:
            self._refresh_timer.stop()
        super().closeEvent(event)


class _ServiceInfoPanel(QWidget):
    """Option 7 – Read-only summary of the service configuration."""

    def __init__(self, service: ServiceConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the service-info panel."""
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        layout.addWidget(_section_title("Información del servicio"))
        layout.addWidget(_separator())

        # Build info rows
        rows = [
            ("Nombre del servicio:", service.name),
            ("Plataforma:", service.platform),
            ("Cuenta remota:", f"{service.remote_name}:{service.remote_path}"),
            ("Carpeta local:", service.local_path),
            ("Intervalo de sincronización:", f"{service.sync_interval} minutos"),
            (
                "Sincronizando activamente:",
                "Sí" if service.is_syncing else "No",
            ),
            (
                "Última sincronización:",
                service.last_sync or "Nunca",
            ),
            ("Versión de rclone:", get_rclone_version()),
        ]

        for label_text, value_text in rows:
            row = QHBoxLayout()
            lbl = QLabel(f"<b>{label_text}</b>")
            lbl.setFixedWidth(230)
            row.addWidget(lbl)
            val = QLabel(str(value_text))
            val.setWordWrap(True)
            row.addWidget(val)
            layout.addLayout(row)

        layout.addStretch()

    def apply_changes(self) -> None:
        """No changes to apply from an info-only panel."""


# ---------------------------------------------------------------------------
# Config window
# ---------------------------------------------------------------------------

class ConfigWindow(QDialog):
    """Configuration window for a single service (60 % height × 70 % width)."""

    def __init__(
        self,
        service_id: str,
        config: AppConfig,
        on_delete_callback=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialise and build the configuration window."""
        super().__init__(parent)
        self.config = config
        self.on_delete_callback = on_delete_callback

        service = config.get_service(service_id)
        if not service:
            raise ValueError(f"Service not found: {service_id}")
        self.service = service

        self.setWindowTitle(f"Configuración – {service.name}")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint
        )
        self._resize_to_screen()
        self._build_ui()

    # ------------------------------------------------------------------

    def _resize_to_screen(self) -> None:
        """Resize to 60 % height × 70 % width of the primary screen."""
        screen = QApplication.primaryScreen().availableGeometry()
        w = int(screen.width() * 0.70)
        h = int(screen.height() * 0.60)
        self.resize(w, h)
        self.move(
            screen.x() + (screen.width() - w) // 2,
            screen.y() + (screen.height() - h) // 2,
        )

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the two-column layout (nav list | content stack)."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ── Two-column body ────────────────────────────────────────────
        body = QHBoxLayout()

        # Left nav list
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        nav_items = [
            "1. Configuración por defecto",
            "2. Directorios",
            "3. Excepciones",
            "4. Árbol de carpetas",
            "5. Intervalo de sincronización",
            "6. Espacio en disco",
            "7. Información",
        ]
        for item in nav_items:
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._switch_panel)
        body.addWidget(self.nav_list)

        # Right stacked panels
        self.stack = QStackedWidget()
        self._panel1 = _DefaultConfigPanel(self.service)
        self._panel2 = _DirectoriesPanel(self.service)
        self._panel3 = _ExceptionsPanel(self.service)
        self._panel4 = _FolderTreePanel(self.service)
        self._panel5 = _SyncIntervalPanel(self.service)
        self._panel6 = _DiskSpacePanel(
            self.service, self.config, self._handle_delete
        )
        self._panel7 = _ServiceInfoPanel(self.service)

        self._panels = [
            self._panel1,
            self._panel2,
            self._panel3,
            self._panel4,
            self._panel5,
            self._panel6,
            self._panel7,
        ]
        for panel in self._panels:
            self.stack.addWidget(panel)
        body.addWidget(self.stack, stretch=1)

        main_layout.addLayout(body, stretch=1)

        # ── Save button ────────────────────────────────────────────────
        main_layout.addWidget(_separator())
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("💾  Guardar cambios")
        save_btn.setMinimumHeight(36)
        save_btn.setMinimumWidth(160)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        main_layout.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _switch_panel(self, row: int) -> None:
        """Switch the right panel when the user clicks a nav item."""
        self.stack.setCurrentIndex(row)

    def _save(self) -> None:
        """Apply changes from all panels and persist the service config."""
        for panel in self._panels:
            panel.apply_changes()
        self.config.update_service(self.service)
        QMessageBox.information(self, "Guardado", "✅ Configuración guardada correctamente.")

    def _handle_delete(self) -> None:
        """Called when the service is deleted from the disk-space panel."""
        if self.on_delete_callback:
            self.on_delete_callback()
        self.accept()
