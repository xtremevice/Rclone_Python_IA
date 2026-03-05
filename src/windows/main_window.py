"""
src/windows/main_window.py

Primary application window.  Each configured service appears as its own tab.

Layout (60 % height × 20 % width of the primary screen):
  ┌──────────────────────────────────────────────────────┐
  │  [Tab 1]  [Tab 2]  [Tab 3] …                         │
  │─────────────────────────────────────────────────────│
  │  Service name  │  Status  │  Interval  │  Platform  │
  │─────────────────────────────────────────────────────│
  │  File-change list (60 % of vertical space, 100 % H) │
  │─────────────────────────────────────────────────────│
  │  [Open Folder]  [Stop/Start Sync]  [Configuration]  │  ← 5 % V
  └──────────────────────────────────────────────────────┘

Behaviour:
  • Minimising the window sends it to the system tray.
  • The "×" close button exits the application.
  • The maximise button is hidden.
  • Clicking the tray icon restores the window.
"""
import os
import platform
import subprocess
from typing import Optional, Dict

from PyQt5.QtCore import Qt, QSize, pyqtSlot
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QFrame,
)

from src.core.config import AppConfig, ServiceConfig
from src.core.service_manager import ServiceManager


# ---------------------------------------------------------------------------
# Icon factory
# ---------------------------------------------------------------------------

def _make_tray_icon() -> QIcon:
    """Create a simple cloud-shaped tray icon programmatically."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#4A90E2"))
    painter.setPen(Qt.NoPen)
    # Draw a cloud-like shape using overlapping ellipses
    painter.drawEllipse(8, 24, 32, 28)
    painter.drawEllipse(20, 18, 28, 28)
    painter.drawEllipse(36, 22, 24, 24)
    painter.drawRect(12, 36, 44, 12)
    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# Per-service tab widget
# ---------------------------------------------------------------------------

class _ServiceTab(QWidget):
    """Content widget for a single service tab.

    Shows service metadata, a file-change list, and three action buttons.
    """

    def __init__(
        self,
        service: ServiceConfig,
        manager: ServiceManager,
        on_open_config,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Build the tab for the given service."""
        super().__init__(parent)
        self.service = service
        self.manager = manager
        self.on_open_config = on_open_config
        self._build_ui()
        # Listen for sync events from the manager
        manager.sync_started.connect(self._on_sync_started)
        manager.sync_finished.connect(self._on_sync_finished)
        manager.sync_file.connect(self._on_file_synced)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the layout for this tab."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Service metadata bar ──────────────────────────────────────
        meta_row = QHBoxLayout()

        self.name_lbl = QLabel(f"<b>{self.service.name}</b>")
        meta_row.addWidget(self.name_lbl)

        meta_row.addWidget(QLabel("│"))

        self.status_lbl = QLabel(self._status_text())
        meta_row.addWidget(self.status_lbl)

        meta_row.addWidget(QLabel("│"))

        self.interval_lbl = QLabel(f"Cada {self.service.sync_interval} min")
        meta_row.addWidget(self.interval_lbl)

        meta_row.addWidget(QLabel("│"))

        self.platform_lbl = QLabel(f"🌐 {self.service.platform}")
        meta_row.addWidget(self.platform_lbl)

        meta_row.addStretch()
        layout.addLayout(meta_row)

        # Horizontal separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # ── File-change list (fills 60 % of vertical space) ──────────
        self.file_table = QTableWidget(0, 3)
        self.file_table.setHorizontalHeaderLabels(["Archivo", "Estado", "Hora"])
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.file_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_table.verticalHeader().setVisible(False)
        # 60 % of vertical space
        self.file_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.file_table, stretch=6)

        # Populate with existing recent-files history
        for entry in self.service.recent_files:
            self._add_row(
                entry.get("filename", ""),
                entry.get("synced", False),
                entry.get("timestamp", ""),
            )

        # ── Bottom buttons (5 % of vertical space) ───────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(0)

        open_btn = QPushButton("📂 Abrir carpeta")
        open_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        open_btn.setMinimumHeight(36)
        open_btn.clicked.connect(self._open_folder)
        btn_row.addWidget(open_btn)

        self.toggle_btn = QPushButton(self._toggle_label())
        self.toggle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toggle_btn.setMinimumHeight(36)
        self.toggle_btn.clicked.connect(self._toggle_sync)
        btn_row.addWidget(self.toggle_btn)

        config_btn = QPushButton("⚙️ Configuración")
        config_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        config_btn.setMinimumHeight(36)
        config_btn.clicked.connect(lambda: self.on_open_config(self.service.id))
        btn_row.addWidget(config_btn)

        layout.addLayout(btn_row, stretch=1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _status_text(self) -> str:
        """Return a human-readable sync-status string for the service."""
        if self.service.is_syncing:
            return "🔄 Sincronizando …"
        if not self.service.sync_active:
            return "⏸ Pausado"
        if self.service.last_sync:
            return f"✅ Actualizado  ({self.service.last_sync})"
        return "⏳ Esperando primera sincronización"

    def _toggle_label(self) -> str:
        """Return the label for the start/stop button based on current state."""
        return "⏹ Detener sync" if self.service.sync_active else "▶ Reanudar sync"

    def _add_row(self, filename: str, synced: bool, timestamp: str) -> None:
        """Insert a new row at the top of the file-change table."""
        row = 0
        self.file_table.insertRow(row)
        self.file_table.setItem(row, 0, QTableWidgetItem(filename))
        status_item = QTableWidgetItem("✅ Sincronizado" if synced else "⏳ Pendiente")
        status_item.setTextAlignment(Qt.AlignCenter)
        self.file_table.setItem(row, 1, status_item)
        ts_item = QTableWidgetItem(timestamp)
        ts_item.setTextAlignment(Qt.AlignCenter)
        self.file_table.setItem(row, 2, ts_item)
        # Keep at most 50 rows
        while self.file_table.rowCount() > 50:
            self.file_table.removeRow(self.file_table.rowCount() - 1)

    # ------------------------------------------------------------------
    # Slots / event handlers
    # ------------------------------------------------------------------

    def _open_folder(self) -> None:
        """Open the service's local directory in the native file manager."""
        path = self.service.local_path
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Advertencia", f"La carpeta no existe:\n{path}")
            return
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))

    def _toggle_sync(self) -> None:
        """Start or stop the automatic sync for this service."""
        self.manager.toggle_sync(self.service.id)
        # Reload service from config (toggle_sync may have updated it)
        updated = self.manager.config.get_service(self.service.id)
        if updated:
            self.service = updated
        self.toggle_btn.setText(self._toggle_label())
        self.status_lbl.setText(self._status_text())

    @pyqtSlot(str)
    def _on_sync_started(self, service_id: str) -> None:
        """Update status label when a sync starts for this service."""
        if service_id == self.service.id:
            self.service.is_syncing = True
            self.status_lbl.setText("🔄 Sincronizando …")

    @pyqtSlot(str, bool, str)
    def _on_sync_finished(self, service_id: str, success: bool, message: str) -> None:
        """Update status label when a sync finishes for this service."""
        if service_id == self.service.id:
            self.service.is_syncing = False
            updated = self.manager.config.get_service(service_id)
            if updated:
                self.service = updated
            self.status_lbl.setText(self._status_text())

    @pyqtSlot(str, str, bool)
    def _on_file_synced(self, service_id: str, filename: str, ok: bool) -> None:
        """Add a file-change row when a file is transferred for this service."""
        if service_id == self.service.id:
            from datetime import datetime
            self._add_row(filename, ok, datetime.now().strftime("%H:%M:%S"))

    def refresh(self) -> None:
        """Reload service data from config and refresh all displayed values."""
        updated = self.manager.config.get_service(self.service.id)
        if updated:
            self.service = updated
        self.name_lbl.setText(f"<b>{self.service.name}</b>")
        self.status_lbl.setText(self._status_text())
        self.interval_lbl.setText(f"Cada {self.service.sync_interval} min")
        self.platform_lbl.setText(f"🌐 {self.service.platform}")
        self.toggle_btn.setText(self._toggle_label())


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Main application window (60 % height × 20 % width).

    Provides a tabbed interface, system-tray integration, and navigation to
    per-service configuration windows.
    """

    def __init__(
        self,
        config: AppConfig,
        manager: ServiceManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialise the main window."""
        super().__init__(parent)
        self.config = config
        self.manager = manager
        self._tab_widgets: Dict[str, _ServiceTab] = {}

        self.setWindowTitle("RclonePyIA")
        # Hide the maximise button while keeping minimise and close
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self._resize_to_screen()
        self._build_ui()
        self._setup_tray()

        # React to external service changes
        self.manager.services_changed.connect(self._reload_tabs)

    # ------------------------------------------------------------------

    def _resize_to_screen(self) -> None:
        """Resize to 60 % height × 20 % width of the primary screen."""
        screen = QApplication.primaryScreen().availableGeometry()
        w = int(screen.width() * 0.20)
        h = int(screen.height() * 0.60)
        self.resize(w, h)
        # Position in the top-right corner
        self.move(
            screen.x() + screen.width() - w - 20,
            screen.y() + (screen.height() - h) // 2,
        )

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the central widget with a QTabWidget."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)
        layout.addWidget(self.tabs)

        self._populate_tabs()

    def _populate_tabs(self) -> None:
        """Create one tab for each configured service."""
        self.tabs.clear()
        self._tab_widgets.clear()

        for service in self.config.services:
            tab = _ServiceTab(
                service=service,
                manager=self.manager,
                on_open_config=self._open_config_window,
            )
            self._tab_widgets[service.id] = tab
            self.tabs.addTab(tab, service.name)

        if not self.config.services:
            # Show a placeholder when no services are configured
            placeholder = QLabel("No hay servicios configurados.\nUsa 'Agregar servicio' para comenzar.")
            placeholder.setAlignment(Qt.AlignCenter)
            self.tabs.addTab(placeholder, "Sin servicios")

    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        """Configure the system-tray icon and its context menu."""
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_make_tray_icon())
        self._tray.setToolTip("RclonePyIA")

        tray_menu = QMenu()
        show_action = QAction("Mostrar ventana", self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        add_action = QAction("Agregar servicio", self)
        add_action.triggered.connect(self._add_service)
        tray_menu.addAction(add_action)

        tray_menu.addSeparator()

        quit_action = QAction("Salir", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    # ------------------------------------------------------------------

    def _show_window(self) -> None:
        """Restore and bring the main window to the foreground."""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Show the window when the tray icon is clicked."""
        if reason in (
            QSystemTrayIcon.Trigger,
            QSystemTrayIcon.DoubleClick,
        ):
            self._show_window()

    def changeEvent(self, event) -> None:
        """Intercept window-state changes to minimise to the tray."""
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                # Hide the window and keep it alive in the tray
                self.hide()
                self._tray.showMessage(
                    "RclonePyIA",
                    "La aplicación sigue ejecutándose en la bandeja del sistema.",
                    QSystemTrayIcon.Information,
                    2000,
                )
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        """Exit the application when the user closes the window."""
        self._tray.hide()
        QApplication.quit()
        event.accept()

    # ------------------------------------------------------------------

    def _open_config_window(self, service_id: str) -> None:
        """Open the configuration dialog for the given service."""
        from src.windows.config_window import ConfigWindow
        dlg = ConfigWindow(
            service_id=service_id,
            config=self.config,
            on_delete_callback=self._on_service_deleted,
            parent=self,
        )
        dlg.exec_()
        # Refresh the tab after the dialog closes (settings may have changed)
        tab = self._tab_widgets.get(service_id)
        if tab:
            tab.refresh()
        # Update the sync schedule in case the interval changed
        self.manager.update_schedule(service_id)

    def _on_service_deleted(self) -> None:
        """Handle deletion of a service (rebuild tabs)."""
        self._reload_tabs()

    def _add_service(self) -> None:
        """Open the setup wizard to add a new service."""
        from src.windows.setup_wizard import SetupWizard
        wizard = SetupWizard(config=self.config, parent=self)
        wizard.service_created.connect(self._on_service_created)
        wizard.exec_()

    def _on_service_created(self, service: ServiceConfig) -> None:
        """React to a newly created service: add a tab and start its scheduler."""
        self._reload_tabs()
        self.manager.update_schedule(service.id)
        # Start an immediate sync so the user sees activity right away
        self.manager.trigger_sync_now(service.id)

    # ------------------------------------------------------------------

    def _reload_tabs(self) -> None:
        """Rebuild all service tabs from the current configuration."""
        self._populate_tabs()
