"""
main_window.py — Ventana principal de la aplicación.
Muestra las pestañas de servicios configurados con su información y
lista de archivos sincronizados. Al minimizar, se envía al área de notificaciones.
Tamaño: 60% de alto, 20% de ancho.
"""

from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QSystemTrayIcon,
    QMenu,
    QAction,
    QApplication,
    QSizePolicy,
    QFrame,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QColor, QPixmap

import os
from typing import Dict

import config as cfg
from rclone_manager import SyncWorker


# Número máximo de entradas en la lista de archivos modificados
MAX_LOG_ENTRIES = 50


def _build_tray_icon() -> QIcon:
    """
    Construye un icono simple para la bandeja del sistema.
    Usa un pixmap de 16×16 en color verde.
    """
    pix = QPixmap(16, 16)
    pix.fill(QColor("#4CAF50"))
    return QIcon(pix)


class ServiceTab(QWidget):
    """
    Widget que representa la pestaña de un servicio individual.
    Muestra el estado, plataforma, intervalo y lista de archivos modificados.
    """

    def __init__(self, service: dict, parent=None):
        """
        Inicializa la pestaña con la información del servicio.

        :param service: Diccionario con la configuración del servicio.
        """
        super().__init__(parent)
        self.service = service
        self._build_ui()

    def _build_ui(self):
        """Construye los componentes visuales de la pestaña."""
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 10, 12, 10)

        # ── Barra de información del servicio ──────────────────────────────
        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.StyledPanel)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(10, 6, 10, 6)

        # 1. Nombre del servicio
        self.lbl_name = QLabel()
        self.lbl_name.setFont(QFont("", 10, QFont.Bold))
        info_layout.addWidget(self.lbl_name)

        info_layout.addStretch()

        # 2. Estado de sincronización
        self.lbl_status = QLabel()
        self.lbl_status.setFont(QFont("", 10))
        info_layout.addWidget(self.lbl_status)

        info_layout.addStretch()

        # 3. Intervalo de sincronización
        self.lbl_interval = QLabel()
        self.lbl_interval.setFont(QFont("", 10))
        info_layout.addWidget(self.lbl_interval)

        info_layout.addStretch()

        # 4. Plataforma vinculada
        self.lbl_platform = QLabel()
        self.lbl_platform.setFont(QFont("", 10))
        info_layout.addWidget(self.lbl_platform)

        layout.addWidget(info_frame)

        # ── Lista de archivos modificados (100% horizontal, 60% vertical) ──
        self.file_list = QListWidget()
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # La lista ocupa el 60% del espacio vertical mediante stretch factor
        layout.addWidget(self.file_list, stretch=6)

        # Relleno inferior para equilibrar el 40% restante
        bottom_spacer = QWidget()
        bottom_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(bottom_spacer, stretch=0)

        # Actualizar las etiquetas con los datos del servicio
        self.update_service_info(self.service)

    def update_service_info(self, service: dict):
        """
        Actualiza las etiquetas de información del servicio.

        :param service: Diccionario actualizado del servicio.
        """
        self.service = service

        # Nombre del servicio
        self.lbl_name.setText(f"📁 {service.get('name', '-')}")

        # Estado de sincronización con color
        status = service.get("status", "idle")
        status_map = {
            "syncing": ("🔄 Sincronizando", "#2196F3"),
            "idle":    ("✔ Actualizado",   "#4CAF50"),
            "error":   ("✖ Error",         "#F44336"),
            "paused":  ("⏸ Pausado",       "#FF9800"),
        }
        text, color = status_map.get(status, ("— Desconocido", "#9E9E9E"))
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {color}; font-weight: bold;")

        # Intervalo de sincronización
        interval = service.get("sync_interval", 5)
        self.lbl_interval.setText(f"🕐 Cada {interval} min")

        # Plataforma vinculada
        platform = service.get("platform", "-")
        self.lbl_platform.setText(f"☁ {platform}")

    def add_log_entry(self, filename: str, synced: bool):
        """
        Agrega una entrada a la lista de archivos modificados.
        Limita la lista a MAX_LOG_ENTRIES entradas.

        :param filename: Nombre o descripción del archivo modificado.
        :param synced: True si el archivo ya fue sincronizado.
        """
        # Crear el elemento de lista
        icon = "✔" if synced else "🔄"
        item = QListWidgetItem(f"{icon}  {filename}")

        # Colorear según el estado
        if synced:
            item.setForeground(QColor("#4CAF50"))
        else:
            item.setForeground(QColor("#FF9800"))

        # Insertar al principio para mostrar los más recientes arriba
        self.file_list.insertItem(0, item)

        # Eliminar entradas antiguas si se supera el límite
        while self.file_list.count() > MAX_LOG_ENTRIES:
            self.file_list.takeItem(self.file_list.count() - 1)


class MainWindow(QMainWindow):
    """
    Ventana principal de la aplicación.
    Muestra pestañas para cada servicio configurado.
    Se envía al área de notificaciones al minimizar.
    Tamaño: 60% de alto, 20% de ancho de la pantalla.
    """

    def __init__(self):
        """Inicializa la ventana principal."""
        super().__init__()
        self.setWindowTitle("Rclone Python IA")
        # Deshabilitar el botón de maximizar
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowMaximizeButtonHint
        )

        # Diccionario de workers de sincronización: nombre_servicio → SyncWorker
        self._workers: Dict[str, SyncWorker] = {}
        # Referencia a las pestañas: nombre_servicio → ServiceTab
        self._tabs: Dict[str, ServiceTab] = {}

        # Aplicar tamaño de ventana
        self._apply_window_size()

        # Construir la interfaz
        self._build_ui()

        # Configurar el icono de bandeja del sistema
        self._setup_tray()

        # Cargar y mostrar los servicios configurados
        self._load_services()

        # Temporizador para actualizar el estado de los servicios
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(5000)  # Actualizar cada 5 segundos

    def _apply_window_size(self):
        """Ajusta el tamaño de la ventana al 60% de alto y 20% de ancho."""
        screen = QApplication.primaryScreen().availableGeometry()
        w = int(screen.width() * 0.20)
        h = int(screen.height() * 0.60)
        self.resize(w, h)
        # Posicionar en el borde derecho de la pantalla
        x = screen.x() + screen.width() - w - 20
        y = screen.y() + (screen.height() - h) // 2
        self.move(x, y)

    def _build_ui(self):
        """Construye los componentes visuales de la ventana principal."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ── Pestañas de servicios ───────────────────────────────────────────
        self.tab_widget = QTabWidget()
        self.tab_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.tab_widget, stretch=1)

        # ── Barra de botones inferior (5% del vertical) ─────────────────────
        btn_bar = QWidget()
        btn_bar.setStyleSheet("background-color: #F5F5F5; border-top: 1px solid #BDBDBD;")
        # La altura de la barra será el 5% de la altura de la ventana
        btn_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(4, 4, 4, 4)
        btn_layout.setSpacing(4)

        # Botón 1: Abrir carpeta del servicio actual
        self.btn_open_folder = QPushButton("📂 Abrir Carpeta")
        self.btn_open_folder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_open_folder.clicked.connect(self._open_service_folder)
        btn_layout.addWidget(self.btn_open_folder)

        # Botón 2: Pausar/Reanudar sincronización
        self.btn_toggle_sync = QPushButton("⏸ Pausar Sync")
        self.btn_toggle_sync.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_toggle_sync.clicked.connect(self._toggle_sync)
        btn_layout.addWidget(self.btn_toggle_sync)

        # Botón 3: Configuración del servicio actual
        self.btn_settings = QPushButton("⚙ Configuración")
        self.btn_settings.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_settings.clicked.connect(self._open_settings)
        btn_layout.addWidget(self.btn_settings)

        main_layout.addWidget(btn_bar)

    def _setup_tray(self):
        """Configura el icono de la bandeja del sistema."""
        # Crear el icono de bandeja
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_build_tray_icon())
        self._tray.setToolTip("Rclone Python IA")

        # Menú contextual del icono de bandeja
        tray_menu = QMenu()

        # Acción para mostrar la ventana principal
        act_show = QAction("Mostrar", self)
        act_show.triggered.connect(self._show_from_tray)
        tray_menu.addAction(act_show)

        tray_menu.addSeparator()

        # Acción para salir de la aplicación
        act_quit = QAction("Salir", self)
        act_quit.triggered.connect(QApplication.quit)
        tray_menu.addAction(act_quit)

        self._tray.setContextMenu(tray_menu)

        # Al hacer clic simple en el icono, mostrar la ventana
        self._tray.activated.connect(self._on_tray_activated)

        # Mostrar el icono de bandeja
        self._tray.show()

    def _on_tray_activated(self, reason):
        """Responde a la activación del icono de bandeja."""
        # Al hacer doble clic o clic simple, mostrar la ventana
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        """Restaura la ventana desde la bandeja del sistema."""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def changeEvent(self, event):
        """
        Intercepta eventos de cambio de estado de la ventana.
        Al minimizar, envía la ventana a la bandeja del sistema.
        """
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                # Ocultar la ventana y notificar al usuario
                QTimer.singleShot(0, self.hide)
                self._tray.showMessage(
                    "Rclone Python IA",
                    "La aplicación sigue ejecutándose en la bandeja.",
                    QSystemTrayIcon.Information,
                    2000,
                )
        super().changeEvent(event)

    def closeEvent(self, event):
        """
        Al cerrar la ventana, detiene todos los workers y oculta el trayicon.
        """
        # Detener todos los workers de sincronización
        for worker in self._workers.values():
            worker.stop()
        # Ocultar el icono de bandeja
        self._tray.hide()
        event.accept()

    def _load_services(self):
        """
        Carga los servicios configurados y crea una pestaña para cada uno.
        Si no hay servicios, muestra la pantalla de bienvenida.
        """
        services = cfg.get_services()

        if not services:
            # Mostrar mensaje de bienvenida si no hay servicios
            self._show_welcome_tab()
            return

        # Crear una pestaña por cada servicio
        for service in services:
            self._add_service_tab(service)

    def _show_welcome_tab(self):
        """Muestra una pestaña de bienvenida cuando no hay servicios configurados."""
        welcome = QWidget()
        layout = QVBoxLayout(welcome)
        layout.setAlignment(Qt.AlignCenter)

        lbl = QLabel("No hay servicios configurados.\nUsa el botón ⚙ para agregar uno.")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFont(QFont("", 12))
        layout.addWidget(lbl)

        btn_add = QPushButton("➕ Agregar servicio")
        btn_add.setMinimumSize(200, 40)
        btn_add.clicked.connect(self._add_new_service)
        layout.addWidget(btn_add, 0, Qt.AlignCenter)

        self.tab_widget.addTab(welcome, "Bienvenido")

    def _add_service_tab(self, service: dict):
        """
        Agrega una pestaña para el servicio dado y arranca su worker de sync.

        :param service: Diccionario de configuración del servicio.
        """
        name = service.get("name", "Servicio")
        tab = ServiceTab(service)
        self._tabs[name] = tab
        self.tab_widget.addTab(tab, name)

        # Crear y arrancar el worker de sincronización si está activo
        if service.get("active", True):
            worker = SyncWorker(
                service,
                log_callback=lambda fname, synced, n=name: self._on_sync_log(n, fname, synced),
            )
            self._workers[name] = worker
            worker.start()

    def _on_sync_log(self, service_name: str, filename: str, synced: bool):
        """
        Callback llamado por el SyncWorker cuando hay actividad de sincronización.
        Actualiza la lista de archivos de la pestaña correspondiente.

        :param service_name: Nombre del servicio.
        :param filename: Descripción del archivo o mensaje de rclone.
        :param synced: True si el archivo fue sincronizado.
        """
        if service_name in self._tabs:
            tab = self._tabs[service_name]
            # Usar QTimer para actualizar la UI desde el hilo principal
            QTimer.singleShot(
                0, lambda: tab.add_log_entry(filename, synced)
            )

    def _refresh_status(self):
        """
        Actualiza las etiquetas de estado de todas las pestañas
        leyendo la configuración actual.
        """
        services = cfg.get_services()
        for service in services:
            name = service.get("name")
            if name and name in self._tabs:
                # Determinar el estado actual del worker
                if name in self._workers:
                    worker = self._workers[name]
                    status = "syncing" if worker.is_running else "paused"
                    service["status"] = status
                self._tabs[name].update_service_info(service)

    def _get_current_service(self) -> dict | None:
        """
        Devuelve la configuración del servicio de la pestaña actualmente activa.
        Devuelve None si no hay servicios.
        """
        index = self.tab_widget.currentIndex()
        if index < 0:
            return None
        tab = self.tab_widget.widget(index)
        if isinstance(tab, ServiceTab):
            return tab.service
        return None

    def _open_service_folder(self):
        """Abre en el explorador de archivos la carpeta del servicio actual."""
        service = self._get_current_service()
        if not service:
            return

        folder = service.get("local_folder", "")
        if folder and os.path.exists(folder):
            import subprocess, platform
            system = platform.system()
            if system == "Windows":
                os.startfile(folder)
            elif system == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])

    def _toggle_sync(self):
        """
        Pausa o reanuda la sincronización del servicio actualmente visible.
        Actualiza el texto del botón y el estado del servicio.
        """
        service = self._get_current_service()
        if not service:
            return

        name = service.get("name")
        if not name:
            return

        worker = self._workers.get(name)

        if worker and worker.is_running:
            # Pausar la sincronización
            worker.stop()
            service["status"] = "paused"
            self.btn_toggle_sync.setText("▶ Reanudar Sync")
        else:
            # Reanudar la sincronización
            if not worker:
                worker = SyncWorker(
                    service,
                    log_callback=lambda fname, synced, n=name: self._on_sync_log(n, fname, synced),
                )
                self._workers[name] = worker
            service["status"] = "syncing"
            worker.start()
            self.btn_toggle_sync.setText("⏸ Pausar Sync")

        # Actualizar el estado en la pestaña
        index = self.tab_widget.currentIndex()
        if index >= 0:
            tab = self.tab_widget.widget(index)
            if isinstance(tab, ServiceTab):
                tab.update_service_info(service)

    def _open_settings(self):
        """Abre la ventana de configuración del servicio actual."""
        service = self._get_current_service()
        if not service:
            # Si no hay servicio, ofrecer agregar uno nuevo
            self._add_new_service()
            return

        from windows.settings_window import SettingsWindow
        win = SettingsWindow(service, parent=self)
        if win.exec_():
            # Si se guardaron cambios, recargar las pestañas
            self._reload_tabs()

    def _add_new_service(self):
        """Lanza el asistente para agregar un nuevo servicio."""
        from windows.wizard import run_wizard
        added = run_wizard(self)
        if added:
            # Recargar las pestañas para mostrar el nuevo servicio
            self._reload_tabs()

    def _reload_tabs(self):
        """
        Recarga todas las pestañas deteniéndose los workers actuales.
        Se llama cuando la configuración de servicios cambia.
        """
        # Detener todos los workers
        for worker in self._workers.values():
            worker.stop()
        self._workers.clear()
        self._tabs.clear()

        # Eliminar todas las pestañas actuales
        while self.tab_widget.count() > 0:
            self.tab_widget.removeTab(0)

        # Recargar desde la configuración
        self._load_services()

    def add_service_tab_external(self, service: dict):
        """
        Agrega una pestaña de servicio desde fuera de esta clase.
        Se usa cuando se completa el wizard.

        :param service: Diccionario de configuración del nuevo servicio.
        """
        # Eliminar pestaña de bienvenida si existe
        if self.tab_widget.count() == 1:
            first_tab = self.tab_widget.widget(0)
            if not isinstance(first_tab, ServiceTab):
                self.tab_widget.removeTab(0)
        self._add_service_tab(service)
