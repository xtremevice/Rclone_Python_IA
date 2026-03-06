"""
Ventana principal de Rclone Python IA.
Implementa la interfaz principal con:
  - Pestañas por servicio en la parte superior
  - Información del servicio (nombre, estado, intervalo, plataforma)
  - Lista de cambios recientes (últimos 50 archivos)
  - 3 botones inferiores: abrir carpeta, pausar/reanudar, configuración
  - Ícono en la bandeja del sistema
  - Sin botón de maximizar, minimiza a bandeja
"""

import sys
import threading

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import ConfigManager
from core.rclone import SyncManager, get_remote_storage_info, open_folder
from core.service import Service


def _make_icon() -> QIcon:
    """
    Crea el ícono de la aplicación desde el SVG de resources.
    Retorna un QIcon con el ícono cargado.
    """
    from resources import get_icon_bytes
    pixmap = QPixmap()
    pixmap.loadFromData(get_icon_bytes(), "SVG")
    return QIcon(pixmap)


# Hoja de estilos principal de la ventana
MAIN_STYLESHEET = """
    QMainWindow, QWidget#central {
        background-color: #FAFAFA;
    }
    QTabWidget::pane {
        border: none;
        background-color: #FAFAFA;
    }
    QTabBar::tab {
        background-color: #E0E0E0;
        color: #424242;
        padding: 8px 16px;
        border: none;
        border-radius: 4px 4px 0 0;
        margin-right: 2px;
        font-size: 13px;
    }
    QTabBar::tab:selected {
        background-color: #2196F3;
        color: white;
        font-weight: bold;
    }
    QTabBar::tab:hover {
        background-color: #BBDEFB;
    }
    QLabel#service_name {
        font-size: 16px;
        font-weight: bold;
        color: #212121;
    }
    QLabel#service_info {
        font-size: 12px;
        color: #757575;
    }
    QLabel#status_syncing {
        color: #2196F3;
        font-size: 13px;
        font-weight: bold;
    }
    QLabel#status_updated {
        color: #4CAF50;
        font-size: 13px;
        font-weight: bold;
    }
    QLabel#status_paused {
        color: #FF9800;
        font-size: 13px;
        font-weight: bold;
    }
    QListWidget#files_list {
        border: 1px solid #E0E0E0;
        background-color: white;
        font-size: 12px;
        border-radius: 4px;
    }
    QListWidget#files_list::item {
        padding: 6px 8px;
        border-bottom: 1px solid #F5F5F5;
    }
    QListWidget#files_list::item:alternate {
        background-color: #FAFAFA;
    }
    QPushButton#bottom_btn {
        background-color: #37474F;
        color: white;
        border: none;
        font-size: 13px;
        border-radius: 0;
    }
    QPushButton#bottom_btn:hover {
        background-color: #455A64;
    }
    QPushButton#bottom_btn_pause {
        background-color: #FF9800;
        color: white;
        border: none;
        font-size: 13px;
        border-radius: 0;
    }
    QPushButton#bottom_btn_pause:hover {
        background-color: #F57C00;
    }
"""


class ServiceTab(QWidget):
    """
    Pestaña de un servicio en la ventana principal.
    Muestra información del servicio y lista de archivos recientes.
    """

    def __init__(self, service: Service, config_manager: ConfigManager,
                 parent=None):
        """Inicializa la pestaña con el servicio a mostrar."""
        super().__init__(parent)
        # Referencia al servicio
        self.service = service
        # Referencia al gestor de configuración
        self.config_manager = config_manager
        # Gestor de sincronización para este servicio
        self.sync_manager = SyncManager(service, config_manager)
        # Conectar callbacks del gestor de sincronización
        self.sync_manager.status_callback = self._on_status_change
        self.sync_manager.file_callback = self._on_new_file

        self._build_ui()
        self._load_recent_files()
        # Counter used to discard storage-info results from stale fetches
        self._storage_fetch_gen = 0
        # Fetch cloud storage quota asynchronously
        self._fetch_storage_info()

    def _build_ui(self):
        """Construye la interfaz de la pestaña del servicio."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 0)
        layout.setSpacing(8)

        # Panel de información del servicio (nombre, estado, intervalo, plataforma)
        info_frame = QFrame()
        info_frame.setStyleSheet(
            "background-color: white; border: 1px solid #E0E0E0; "
            "border-radius: 6px; padding: 12px;"
        )
        info_layout = QHBoxLayout(info_frame)
        info_layout.setSpacing(24)

        # Información izquierda: nombre y plataforma
        left_info = QVBoxLayout()
        self.name_label = QLabel(self.service.get_display_name())
        self.name_label.setObjectName("service_name")
        left_info.addWidget(self.name_label)

        platform_label = QLabel(
            f"🌐 {self.service.get_platform_display_name()}"
        )
        platform_label.setObjectName("service_info")
        left_info.addWidget(platform_label)

        # Storage quota label — loaded asynchronously via rclone about
        self.storage_label = QLabel("💾 Consultando almacenamiento…")
        self.storage_label.setObjectName("service_info")
        left_info.addWidget(self.storage_label)
        info_layout.addLayout(left_info)

        # Separador vertical
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #E0E0E0;")
        info_layout.addWidget(sep)

        # Estado de sincronización
        center_info = QVBoxLayout()
        status_header = QLabel("Estado:")
        status_header.setObjectName("service_info")
        center_info.addWidget(status_header)

        self.status_label = QLabel(self.service.get_status_display())
        self._update_status_label_style()
        center_info.addWidget(self.status_label)
        info_layout.addLayout(center_info)

        # Separador vertical
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet("color: #E0E0E0;")
        info_layout.addWidget(sep2)

        # Intervalo de sincronización
        right_info = QVBoxLayout()
        interval_header = QLabel("Sincroniza cada:")
        interval_header.setObjectName("service_info")
        right_info.addWidget(interval_header)

        self.interval_label = QLabel(self.service.get_sync_interval_display())
        self.interval_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #424242;"
        )
        right_info.addWidget(self.interval_label)
        info_layout.addLayout(right_info)

        info_layout.addStretch()
        layout.addWidget(info_frame)

        # Encabezado de la lista de archivos
        files_header = QLabel(
            "📋 Últimos archivos modificados (máximo 50):"
        )
        files_header.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #424242; "
            "padding: 4px 0;"
        )
        layout.addWidget(files_header)

        # Lista de archivos recientes
        self.files_list = QListWidget()
        self.files_list.setObjectName("files_list")
        self.files_list.setAlternatingRowColors(True)
        # La lista ocupa 100% horizontal y 60% vertical (se controla con stretch)
        layout.addWidget(self.files_list, stretch=1)

    def _update_status_label_style(self):
        """Actualiza el estilo del label de estado según el estado actual."""
        status = self.service.get_status_display()
        if "Sincronizando" in status:
            self.status_label.setObjectName("status_syncing")
        elif "Actualizado" in status:
            self.status_label.setObjectName("status_updated")
        else:
            self.status_label.setObjectName("status_paused")
        # Forzar actualización de estilo
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _load_recent_files(self):
        """Carga la lista de archivos recientes desde el servicio."""
        self.files_list.clear()
        for file_entry in self.service.recent_files[:50]:
            self._add_file_item(file_entry)

    def _add_file_item(self, file_entry: dict):
        """
        Agrega un ítem de archivo a la lista de recientes.
        Muestra el nombre del archivo, estado y hora.
        """
        # Crear ítem con información del archivo
        status = file_entry.get("status", "")
        file_name = file_entry.get("file", "")
        time_str = file_entry.get("time", "")
        synced = file_entry.get("synced", True)

        # Icono según el estado del archivo
        status_icons = {
            "Copied": "✅",
            "Updated": "🔄",
            "Deleted": "🗑️",
            "Moved": "📦",
        }
        icon = status_icons.get(status, "📄")
        sync_indicator = "✅" if synced else "⏳"

        item_text = f"{icon} {file_name}  [{time_str}]  {sync_indicator}"
        item = QListWidgetItem(item_text)

        # Color según el estado
        if status == "Deleted":
            item.setForeground(QColor("#D32F2F"))
        elif status in ("Copied", "Updated"):
            item.setForeground(QColor("#1B5E20"))

        # Insertar al inicio de la lista
        self.files_list.insertItem(0, item)

        # Mantener límite de 50 ítems
        while self.files_list.count() > 50:
            self.files_list.takeItem(self.files_list.count() - 1)

    def _on_status_change(self, status: str):
        """
        Callback llamado desde el SyncManager cuando cambia el estado.
        Actualiza el label de estado en el hilo principal.

        Cuando el servicio está pausado y el SyncManager reporta "Detenido"
        (porque terminó el proceso), se muestra "Pausado" para que el estado
        sea coherente con el botón "▶️ Reanudar sync".
        """
        if self.service.is_paused and status == "Detenido":
            status = "Pausado"
        QTimer.singleShot(0, lambda: self._update_status(status))

    def _update_status(self, status: str):
        """Actualiza el label de estado en el hilo principal."""
        if hasattr(self, "status_label"):
            self.status_label.setText(status)
            self._update_status_label_style()

    def _on_new_file(self, file_entry: dict):
        """
        Callback llamado cuando se procesa un nuevo archivo.
        Agrega el archivo a la lista en el hilo principal.
        """
        QTimer.singleShot(0, lambda: self._add_file_item(file_entry))

    def _fetch_storage_info(self):
        """
        Consulta el espacio de almacenamiento remoto con ``rclone about``
        en un hilo secundario para no bloquear la interfaz gráfica.
        El resultado actualiza ``storage_label`` en el hilo principal.

        Un contador de generación descarta resultados de solicitudes antiguas
        cuando varias llamadas se solapan (p.ej. al refrescar la configuración).
        """
        self._storage_fetch_gen += 1
        gen = self._storage_fetch_gen

        def _worker():
            info = get_remote_storage_info(self.service)
            # Only update the label if this is still the latest request
            if gen == self._storage_fetch_gen:
                QTimer.singleShot(0, lambda: self._update_storage_label(info))

        threading.Thread(target=_worker, daemon=True, name=f"about-{self.service.service_id}").start()

    def _update_storage_label(self, info):
        """Actualiza el label de almacenamiento en el hilo principal."""
        if hasattr(self, "storage_label"):
            if info:
                self.storage_label.setText(f"💾 {info}")
            else:
                self.storage_label.setText("💾 Info de almacenamiento no disponible")

    def refresh_service_data(self):
        """Refresca los datos del servicio desde el gestor de configuración."""
        updated = self.config_manager.get_service(self.service.service_id)
        if updated:
            self.service = updated
            self.sync_manager.service = updated
            if hasattr(self, "name_label"):
                self.name_label.setText(self.service.get_display_name())
            if hasattr(self, "status_label"):
                self.status_label.setText(self.service.get_status_display())
                self._update_status_label_style()
            if hasattr(self, "interval_label"):
                self.interval_label.setText(self.service.get_sync_interval_display())
            self._load_recent_files()
            # Refresh cloud quota in the background
            self._fetch_storage_info()

    def toggle_sync(self):
        """Alterna el estado de sincronización (pausar/reanudar)."""
        if self.service.is_paused:
            # Reanudar sincronización
            self.service.is_paused = False
            self.config_manager.update_service(self.service)
            self.sync_manager.start()
        else:
            # Pausar sincronización
            self.service.is_paused = True
            self.config_manager.update_service(self.service)
            self.sync_manager.stop()

        self._update_status(self.service.get_status_display())
        return self.service.is_paused

    def start_sync(self):
        """Inicia la sincronización del servicio si no está pausado."""
        if not self.service.is_paused:
            self.sync_manager.start()

    def stop_sync(self):
        """Detiene la sincronización del servicio."""
        self.sync_manager.stop()


class MainWindow(QMainWindow):
    """
    Ventana principal de Rclone Python IA.
    Contiene pestañas para cada servicio y controles en la parte inferior.
    Se minimiza a la bandeja del sistema al cerrar.
    """

    def __init__(self, config_manager: ConfigManager, parent=None):
        """Inicializa la ventana principal con el gestor de configuración."""
        super().__init__(parent)
        # Referencia al gestor de configuración
        self.config_manager = config_manager
        # Diccionario de pestañas por service_id
        self.service_tabs: dict = {}
        # Ícono de la bandeja del sistema
        self.tray_icon = None
        # Flag para evitar cierre real (solo minimizar a bandeja)
        self._force_close = False

        self._setup_window()
        self._build_ui()
        self._setup_tray_icon()
        self._load_services()

    def _setup_window(self):
        """Configura las propiedades de la ventana principal."""
        self.setWindowTitle("Rclone Python IA")

        # Obtener dimensiones de pantalla
        screen = QApplication.primaryScreen().geometry()
        screen_w = screen.width()
        screen_h = screen.height()

        # Tamaño: 60% alto, 20% ancho (mínimo usable)
        win_w = max(int(screen_w * 0.20), 480)
        win_h = int(screen_h * 0.60)

        self.setFixedWidth(win_w)
        self.resize(win_w, win_h)

        # Centrar en pantalla
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.move(x, y)

        # Sin botón de maximizar, solo minimizar y cerrar
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        # Aplicar hoja de estilos
        self.setStyleSheet(MAIN_STYLESHEET)

        # Ícono de la ventana
        self.setWindowIcon(_make_icon())

    def _build_ui(self):
        """Construye la interfaz principal de la ventana."""
        # Widget central
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        # Layout principal vertical
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Widget de pestañas para los servicios
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(False)
        main_layout.addWidget(self.tab_widget, stretch=1)

        # Panel inferior con los 3 botones de control
        buttons_panel = self._build_buttons_panel()
        main_layout.addWidget(buttons_panel)

    def _build_buttons_panel(self) -> QWidget:
        """
        Construye el panel inferior con los 3 botones de control.
        Los botones cubren el ancho completo y el 5% de la altura.
        """
        panel = QWidget()
        panel.setStyleSheet("background-color: #263238;")

        # Calcular altura del panel (5% de la altura de pantalla)
        screen_h = QApplication.primaryScreen().geometry().height()
        panel_h = max(int(screen_h * 0.05), 40)
        panel.setFixedHeight(panel_h)

        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        # Botón 1: Abrir carpeta del servicio actual
        self.btn_open_folder = QPushButton("📂 Abrir carpeta")
        self.btn_open_folder.setObjectName("bottom_btn")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        layout.addWidget(self.btn_open_folder)

        # Botón 2: Pausar/Reanudar sincronización
        self.btn_toggle_sync = QPushButton("⏸️ Pausar sync")
        self.btn_toggle_sync.setObjectName("bottom_btn_pause")
        self.btn_toggle_sync.clicked.connect(self._on_toggle_sync)
        layout.addWidget(self.btn_toggle_sync)

        # Botón 3: Configuración del servicio actual
        self.btn_config = QPushButton("⚙️ Configuración")
        self.btn_config.setObjectName("bottom_btn")
        self.btn_config.clicked.connect(self._on_open_config)
        layout.addWidget(self.btn_config)

        return panel

    def _setup_tray_icon(self):
        """
        Configura el ícono en la bandeja del sistema.
        Al hacer clic en el ícono se muestra la ventana principal.
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(_make_icon())
        self.tray_icon.setToolTip("Rclone Python IA")

        # Menú contextual de la bandeja
        tray_menu = QMenu()

        show_action = QAction("Mostrar ventana", self)
        show_action.triggered.connect(self._show_from_tray)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = QAction("Salir", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)

        # Mostrar ventana al hacer doble clic o clic en la bandeja
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        """Maneja la activación del ícono de bandeja."""
        if reason in (
            QSystemTrayIcon.DoubleClick,
            QSystemTrayIcon.Trigger,
        ):
            self._show_from_tray()

    def _show_from_tray(self):
        """Restaura la ventana desde la bandeja del sistema."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        """Cierra la aplicación completamente."""
        self._force_close = True
        # Detener todos los servicios de sincronización
        for tab in self.service_tabs.values():
            tab.stop_sync()
        QApplication.quit()

    def _load_services(self):
        """Carga todos los servicios configurados y crea sus pestañas."""
        for service in self.config_manager.services:
            self._add_service_tab(service)

        # Actualizar estado del botón de toggle según el servicio activo
        self._update_toggle_btn_text()

    def _add_service_tab(self, service: Service):
        """
        Agrega una nueva pestaña para el servicio indicado.
        Crea el widget de pestaña y lo registra.
        """
        tab = ServiceTab(service, self.config_manager, self)
        self.service_tabs[service.service_id] = tab
        self.tab_widget.addTab(tab, service.get_display_name())

    def _get_current_tab(self) -> "ServiceTab | None":
        """Retorna la pestaña actualmente visible."""
        current_widget = self.tab_widget.currentWidget()
        if isinstance(current_widget, ServiceTab):
            return current_widget
        return None

    def _update_toggle_btn_text(self):
        """Actualiza el texto del botón de toggle según el estado del servicio."""
        tab = self._get_current_tab()
        if tab and tab.service.is_paused:
            self.btn_toggle_sync.setText("▶️ Reanudar sync")
            self.btn_toggle_sync.setObjectName("bottom_btn")
        else:
            self.btn_toggle_sync.setText("⏸️ Pausar sync")
            self.btn_toggle_sync.setObjectName("bottom_btn_pause")
        # Refrescar estilos
        self.btn_toggle_sync.style().unpolish(self.btn_toggle_sync)
        self.btn_toggle_sync.style().polish(self.btn_toggle_sync)

    def _on_open_folder(self):
        """Abre la carpeta del servicio activo en el explorador de archivos."""
        tab = self._get_current_tab()
        if tab:
            open_folder(tab.service.local_path)

    def _on_toggle_sync(self):
        """Pausa o reanuda la sincronización del servicio activo."""
        tab = self._get_current_tab()
        if tab:
            tab.toggle_sync()
            self._update_toggle_btn_text()

    def _on_open_config(self):
        """Abre la ventana de configuración del servicio activo."""
        tab = self._get_current_tab()
        if not tab:
            return

        from ui.config_window import ConfigWindow
        config_win = ConfigWindow(tab.service, self.config_manager, self)
        config_win.service_updated.connect(self._on_service_updated)
        config_win.service_deleted.connect(self._on_service_deleted)
        config_win.exec_()

    def _on_service_updated(self, service: Service):
        """Actualiza la pestaña del servicio cuando su configuración cambia."""
        tab = self.service_tabs.get(service.service_id)
        if tab:
            tab.refresh_service_data()
            # Actualizar título de la pestaña
            idx = self.tab_widget.indexOf(tab)
            if idx >= 0:
                self.tab_widget.setTabText(idx, service.get_display_name())

    def _on_service_deleted(self, service_id: str):
        """Elimina la pestaña y el servicio cuando es borrado desde configuración."""
        tab = self.service_tabs.get(service_id)
        if tab:
            tab.stop_sync()
            idx = self.tab_widget.indexOf(tab)
            if idx >= 0:
                self.tab_widget.removeTab(idx)
            del self.service_tabs[service_id]

        # Eliminar del gestor de configuración
        self.config_manager.remove_service(service_id)

    def add_service(self, service: Service):
        """
        Agrega un nuevo servicio a la ventana principal.
        Guarda el servicio y crea su pestaña.
        """
        # Guardar en la configuración
        self.config_manager.add_service(service)
        # Crear pestaña
        self._add_service_tab(service)
        # Activar la nueva pestaña
        self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)

    def changeEvent(self, event):
        """
        Intercepta el evento de cambio de estado de la ventana.
        Al minimizar, oculta la ventana y la envía a la bandeja.
        """
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                # Ocultar ventana y mostrar en bandeja
                QTimer.singleShot(100, self.hide)
                if self.tray_icon:
                    self.tray_icon.showMessage(
                        "Rclone Python IA",
                        "La aplicación continúa en la bandeja del sistema.",
                        QSystemTrayIcon.Information,
                        2000,
                    )
        super().changeEvent(event)

    def closeEvent(self, event):
        """
        Intercepta el evento de cierre.
        Solo permite cerrar completamente cuando _force_close es True.
        """
        if self._force_close:
            event.accept()
        else:
            # Minimizar a bandeja en lugar de cerrar
            event.ignore()
            self.hide()
            if self.tray_icon:
                self.tray_icon.showMessage(
                    "Rclone Python IA",
                    "La aplicación sigue ejecutándose en la bandeja del sistema. "
                    "Haga clic en el ícono para restaurar.",
                    QSystemTrayIcon.Information,
                    3000,
                )
