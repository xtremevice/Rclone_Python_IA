"""
settings_window.py — Ventana de configuración de un servicio.
Presenta un menú lateral con 7 opciones de configuración.
Tamaño: 60% de alto, 70% de ancho.
"""

from PyQt5.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QSpinBox,
    QStackedWidget,
    QFileDialog,
    QMessageBox,
    QFrame,
    QScrollArea,
    QApplication,
    QSizePolicy,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

import os

import config as cfg
from rclone_manager import (
    delete_remote,
    list_remote_folders,
    get_disk_usage,
    free_disk_space,
    get_rclone_version,
    PLATFORM_TYPES,
)


def _apply_window_size(window: QDialog) -> None:
    """
    Ajusta el tamaño de la ventana al 60% de alto y 70% de ancho
    de la pantalla disponible y la centra.
    """
    screen = QApplication.primaryScreen().availableGeometry()
    w = int(screen.width() * 0.70)
    h = int(screen.height() * 0.60)
    window.resize(w, h)
    # Centrar la ventana en la pantalla
    x = screen.x() + (screen.width() - w) // 2
    y = screen.y() + (screen.height() - h) // 2
    window.move(x, y)


def _section_title(text: str) -> QLabel:
    """Crea un QLabel con estilo de título de sección."""
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(12)
    font.setBold(True)
    lbl.setFont(font)
    return lbl


def _separator() -> QFrame:
    """Crea una línea separadora horizontal."""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


class SettingsWindow(QDialog):
    """
    Ventana de configuración de un servicio.
    Tiene un menú lateral con 7 opciones y un panel de contenido que cambia.
    """

    def __init__(self, service: dict, parent=None):
        """
        Inicializa la ventana de configuración.

        :param service: Diccionario con la configuración actual del servicio.
        """
        super().__init__(parent)
        self.setWindowTitle(f"Configuración — {service.get('name', 'Servicio')}")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowMaximizeButtonHint
        )
        _apply_window_size(self)

        # Copia de trabajo del servicio (para no modificar el original hasta guardar)
        self.service = dict(service)
        self._original_name = service.get("name", "")
        self._deleted = False

        self._build_ui()
        # Poblar los campos con los valores actuales
        self._populate_fields()

    def _build_ui(self):
        """Construye la interfaz principal: menú lateral + panel de contenido."""
        outer = QHBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        # ── Menú lateral ────────────────────────────────────────────────────
        self.menu_list = QListWidget()
        self.menu_list.setFixedWidth(200)
        self.menu_list.setStyleSheet(
            "QListWidget { background: #263238; color: #ECEFF1; font-size: 13px; }"
            "QListWidget::item { padding: 14px 16px; }"
            "QListWidget::item:selected { background: #37474F; }"
            "QListWidget::item:hover { background: #455A64; }"
        )

        # Items del menú lateral
        menu_items = [
            "1. Configuración por defecto",
            "2. Cambiar directorio",
            "3. Carpetas excluidas",
            "4. Vista de árbol",
            "5. Intervalo y arranque",
            "6. Espacio en disco",
            "7. Información",
        ]
        for item_text in menu_items:
            self.menu_list.addItem(item_text)
        self.menu_list.setCurrentRow(0)
        self.menu_list.currentRowChanged.connect(self._switch_panel)
        outer.addWidget(self.menu_list)

        # ── Panel derecho (contenido + botón guardar) ───────────────────────
        right_panel = QVBoxLayout()
        right_panel.setSpacing(0)
        right_panel.setContentsMargins(0, 0, 0, 0)

        # Área de contenido con pilas de paneles
        self.stack = QStackedWidget()
        right_panel.addWidget(self.stack, stretch=1)

        # Construir los 7 paneles de configuración
        self.stack.addWidget(self._build_panel_1_defaults())
        self.stack.addWidget(self._build_panel_2_directory())
        self.stack.addWidget(self._build_panel_3_exclusions())
        self.stack.addWidget(self._build_panel_4_tree())
        self.stack.addWidget(self._build_panel_5_interval())
        self.stack.addWidget(self._build_panel_6_disk())
        self.stack.addWidget(self._build_panel_7_info())

        # ── Botón guardar ────────────────────────────────────────────────────
        save_bar = QWidget()
        save_bar.setStyleSheet("background: #EEEEEE; border-top: 1px solid #BDBDBD;")
        save_layout = QHBoxLayout(save_bar)
        save_layout.setContentsMargins(12, 8, 12, 8)
        save_layout.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setMinimumSize(100, 34)
        btn_cancel.clicked.connect(self.reject)
        save_layout.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Guardar cambios")
        btn_save.setMinimumSize(160, 34)
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save_changes)
        save_layout.addWidget(btn_save)

        right_panel.addWidget(save_bar)

        outer_right = QWidget()
        outer_right.setLayout(right_panel)
        outer.addWidget(outer_right, stretch=1)

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 1: Configuración por defecto
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_1_defaults(self) -> QWidget:
        """
        Panel 1 — Configuración por defecto de rclone.
        Permite habilitar/deshabilitar las opciones de sync por defecto.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Configuración por defecto de rclone"))
        layout.addWidget(_separator())

        # Opción: sincronizar desde el directorio raíz
        self.chk_root = QCheckBox("Sincronizar desde el directorio raíz del servicio (/)")
        layout.addWidget(self.chk_root)

        # Opción: modo on-demand (solo descargar al usar)
        self.chk_on_demand = QCheckBox("Descargar solo los datos que se usen (on-demand)")
        layout.addWidget(self.chk_on_demand)

        # Opción: bisync (sincronizar en ambas direcciones)
        self.chk_bisync = QCheckBox("Sincronización bidireccional (resync)")
        layout.addWidget(self.chk_bisync)

        # Opción: excluir archivos personales de OneDrive
        self.chk_exclude_personal = QCheckBox(
            "Excluir 'Personal Vault' de OneDrive (recomendado)"
        )
        layout.addWidget(self.chk_exclude_personal)

        layout.addStretch()
        return panel

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 2: Cambiar directorio
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_2_directory(self) -> QWidget:
        """
        Panel 2 — Cambiar el directorio local y/o el directorio remoto.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Cambiar directorios"))
        layout.addWidget(_separator())

        # Directorio local
        lbl_local = QLabel("Carpeta local de sincronización:")
        lbl_local.setFont(QFont("", 11))
        layout.addWidget(lbl_local)

        local_row = QHBoxLayout()
        self.local_folder_input = QLineEdit()
        self.local_folder_input.setMinimumHeight(34)
        local_row.addWidget(self.local_folder_input)

        btn_local = QPushButton("Examinar…")
        btn_local.setMinimumHeight(34)
        btn_local.clicked.connect(self._browse_local_folder)
        local_row.addWidget(btn_local)
        layout.addLayout(local_row)

        # Directorio remoto
        lbl_remote = QLabel("Ruta remota (dentro del servicio de nube):")
        lbl_remote.setFont(QFont("", 11))
        layout.addWidget(lbl_remote)

        self.remote_path_input = QLineEdit()
        self.remote_path_input.setPlaceholderText("Ej: /Documents o /")
        self.remote_path_input.setMinimumHeight(34)
        layout.addWidget(self.remote_path_input)

        layout.addStretch()
        return panel

    def _browse_local_folder(self):
        """Abre el diálogo para seleccionar la carpeta local."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta local",
            self.local_folder_input.text() or os.path.expanduser("~"),
        )
        if folder:
            self.local_folder_input.setText(folder)

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 3: Carpetas excluidas
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_3_exclusions(self) -> QWidget:
        """
        Panel 3 — Agregar o quitar carpetas excluidas de la sincronización.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Carpetas excluidas"))
        layout.addWidget(_separator())

        lbl = QLabel("Carpetas que no serán sincronizadas:")
        lbl.setFont(QFont("", 11))
        layout.addWidget(lbl)

        # Lista de carpetas excluidas
        self.exclusion_list = QListWidget()
        self.exclusion_list.setMinimumHeight(150)
        layout.addWidget(self.exclusion_list)

        # Controles para agregar/quitar
        ctrl_row = QHBoxLayout()
        self.exclusion_input = QLineEdit()
        self.exclusion_input.setPlaceholderText("Nombre de carpeta a excluir")
        self.exclusion_input.setMinimumHeight(34)
        ctrl_row.addWidget(self.exclusion_input)

        btn_add = QPushButton("Agregar")
        btn_add.setMinimumHeight(34)
        btn_add.clicked.connect(self._add_exclusion)
        ctrl_row.addWidget(btn_add)

        btn_remove = QPushButton("Quitar")
        btn_remove.setMinimumHeight(34)
        btn_remove.clicked.connect(self._remove_exclusion)
        ctrl_row.addWidget(btn_remove)
        layout.addLayout(ctrl_row)

        layout.addStretch()
        return panel

    def _add_exclusion(self):
        """Agrega la carpeta escrita al campo de entrada a la lista de exclusiones."""
        text = self.exclusion_input.text().strip()
        if text and not self._exclusion_exists(text):
            self.exclusion_list.addItem(text)
            self.exclusion_input.clear()

    def _remove_exclusion(self):
        """Elimina la carpeta seleccionada de la lista de exclusiones."""
        row = self.exclusion_list.currentRow()
        if row >= 0:
            self.exclusion_list.takeItem(row)

    def _exclusion_exists(self, text: str) -> bool:
        """Verifica si la carpeta ya está en la lista de exclusiones."""
        for i in range(self.exclusion_list.count()):
            if self.exclusion_list.item(i).text() == text:
                return True
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 4: Vista de árbol con checkboxes
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_4_tree(self) -> QWidget:
        """
        Panel 4 — Vista de árbol de carpetas del servicio con checkboxes.
        Permite seleccionar qué carpetas se sincronizan.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Carpetas del servicio"))
        layout.addWidget(_separator())

        lbl = QLabel("Selecciona las carpetas que deseas sincronizar:")
        lbl.setFont(QFont("", 11))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # Árbol de carpetas con checkboxes
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Carpetas remotas")
        self.folder_tree.setColumnCount(1)
        layout.addWidget(self.folder_tree, stretch=1)

        # Botón para recargar el árbol de carpetas
        btn_refresh = QPushButton("🔄 Cargar carpetas del servicio")
        btn_refresh.setMinimumHeight(36)
        btn_refresh.clicked.connect(self._load_remote_tree)
        layout.addWidget(btn_refresh)

        return panel

    def _load_remote_tree(self):
        """
        Carga las carpetas del servicio remoto y las muestra en el árbol.
        Usa rclone lsd para listar carpetas.
        """
        remote = self.service.get("rclone_remote", "")
        if not remote:
            QMessageBox.warning(self, "Sin remote", "Este servicio no tiene un remote configurado.")
            return

        self.folder_tree.clear()

        # Obtener carpetas seleccionadas previamente
        selective = self.service.get("selective_folders", [])

        # Cargar las carpetas en un hilo separado
        list_remote_folders(
            remote,
            path="",
            callback=lambda folders: self._populate_tree(folders, selective),
        )

    def _populate_tree(self, folders: list, selective: list):
        """
        Llena el árbol con las carpetas obtenidas del servicio remoto.
        Marca con checkbox aquellas que están habilitadas para sincronizar.

        :param folders: Lista de nombres de carpetas.
        :param selective: Lista de carpetas habilitadas para sincronizar.
        """
        # Limpiar el árbol antes de llenarlo
        self.folder_tree.clear()

        for folder_name in folders:
            item = QTreeWidgetItem(self.folder_tree, [folder_name])
            # Checkbox para indicar si se sincroniza esta carpeta
            item.setCheckState(
                0,
                Qt.Checked if (not selective or folder_name in selective) else Qt.Unchecked,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 5: Intervalo de sincronización y arranque
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_5_interval(self) -> QWidget:
        """
        Panel 5 — Configuración del intervalo de sincronización
        y opciones de inicio automático.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Intervalo de sincronización y arranque"))
        layout.addWidget(_separator())

        # ── Intervalo de sincronización ─────────────────────────────────────
        lbl_interval = QLabel("Sincronizar cada:")
        lbl_interval.setFont(QFont("", 11))
        layout.addWidget(lbl_interval)

        self.interval_combo = QComboBox()
        self.interval_combo.setMinimumHeight(34)
        # Opciones de intervalo en minutos
        interval_options = [
            ("1 minuto", 1),
            ("5 minutos", 5),
            ("15 minutos", 15),
            ("30 minutos", 30),
            ("60 minutos (1 hora)", 60),
            ("2 horas", 120),
            ("3 horas", 180),
            ("6 horas", 360),
            ("12 horas", 720),
            ("24 horas", 1440),
        ]
        for label, minutes in interval_options:
            self.interval_combo.addItem(label, userData=minutes)
        layout.addWidget(self.interval_combo)

        layout.addWidget(_separator())

        # ── Inicio automático con el sistema ────────────────────────────────
        self.chk_autostart = QCheckBox("Iniciar con el sistema operativo")
        layout.addWidget(self.chk_autostart)

        # Retraso al iniciar
        delay_row = QHBoxLayout()
        lbl_delay = QLabel("Retraso al iniciar (segundos):")
        lbl_delay.setFont(QFont("", 11))
        delay_row.addWidget(lbl_delay)

        self.startup_delay_spin = QSpinBox()
        self.startup_delay_spin.setRange(0, 3600)
        self.startup_delay_spin.setSingleStep(5)
        self.startup_delay_spin.setSuffix(" s")
        self.startup_delay_spin.setMinimumHeight(34)
        delay_row.addWidget(self.startup_delay_spin)
        delay_row.addStretch()
        layout.addLayout(delay_row)

        layout.addStretch()
        return panel

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 6: Espacio en disco
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_6_disk(self) -> QWidget:
        """
        Panel 6 — Gestión del espacio en disco y eliminación del servicio.
        Muestra el espacio utilizado y permite liberarlo o eliminar el servicio.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        layout.addWidget(_section_title("Espacio en disco"))
        layout.addWidget(_separator())

        # Etiqueta con el espacio en uso
        self.lbl_disk_usage = QLabel("Espacio en disco: calculando...")
        self.lbl_disk_usage.setFont(QFont("", 11))
        layout.addWidget(self.lbl_disk_usage)

        # Botón para refrescar el espacio
        btn_refresh_disk = QPushButton("🔄 Actualizar espacio")
        btn_refresh_disk.setMinimumHeight(36)
        btn_refresh_disk.clicked.connect(self._refresh_disk_usage)
        layout.addWidget(btn_refresh_disk)

        # Botón para liberar espacio
        btn_free = QPushButton("☁ Liberar espacio (mover todo a la nube)")
        btn_free.setMinimumHeight(36)
        btn_free.clicked.connect(self._free_disk_space)
        layout.addWidget(btn_free)

        layout.addWidget(_separator())

        # Zona de eliminación del servicio
        lbl_danger = QLabel("⚠ Zona de peligro")
        lbl_danger.setFont(QFont("", 11, QFont.Bold))
        lbl_danger.setStyleSheet("color: #F44336;")
        layout.addWidget(lbl_danger)

        btn_delete = QPushButton("🗑 Eliminar este servicio")
        btn_delete.setMinimumHeight(36)
        btn_delete.setStyleSheet(
            "QPushButton { background: #F44336; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #D32F2F; }"
        )
        btn_delete.clicked.connect(self._delete_service)
        layout.addWidget(btn_delete)

        layout.addStretch()

        # Temporizador para actualizar el uso de disco cada 10 segundos
        # El temporizador se activa/desactiva en _switch_panel al mostrar/ocultar el panel
        self._disk_timer = QTimer(self)
        self._disk_timer.timeout.connect(self._refresh_disk_usage)

        return panel

    def _refresh_disk_usage(self):
        """Calcula y muestra el espacio en disco usado por el servicio."""
        folder = self.service.get("local_folder", "")
        if folder:
            bytes_used = get_disk_usage(folder)
            # Convertir bytes a MB o GB para mostrar
            if bytes_used < 1024 ** 2:
                usage_str = f"{bytes_used / 1024:.1f} KB"
            elif bytes_used < 1024 ** 3:
                usage_str = f"{bytes_used / (1024 ** 2):.1f} MB"
            else:
                usage_str = f"{bytes_used / (1024 ** 3):.2f} GB"
            self.lbl_disk_usage.setText(f"Espacio en disco: {usage_str}")
        else:
            self.lbl_disk_usage.setText("Espacio en disco: carpeta no configurada")

    def _free_disk_space(self):
        """Inicia el proceso de liberación de espacio."""
        reply = QMessageBox.question(
            self,
            "Confirmar",
            "¿Deseas mover todos los archivos locales a la nube y liberar el espacio?\n"
            "Los archivos seguirán siendo accesibles desde la nube.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            remote = self.service.get("rclone_remote", "")
            folder = self.service.get("local_folder", "")
            self.lbl_disk_usage.setText("Liberando espacio...")
            free_disk_space(
                remote,
                folder,
                callback=lambda ok, msg: QMessageBox.information(self, "Resultado", msg),
            )

    def _delete_service(self):
        """
        Pide confirmación y elimina el servicio de la configuración.
        También elimina el remote de rclone.
        """
        name = self.service.get("name", "este servicio")
        reply = QMessageBox.question(
            self,
            "Eliminar servicio",
            f"¿Estás seguro de que deseas eliminar el servicio '{name}'?\n"
            "Esta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            # Eliminar el remote de rclone
            remote = self.service.get("rclone_remote", "")
            if remote:
                delete_remote(remote)
            # Eliminar el servicio de la configuración
            cfg.remove_service(self._original_name)
            self._deleted = True
            # Cerrar la ventana de configuración
            self.accept()

    # ──────────────────────────────────────────────────────────────────────────
    # Panel 7: Información del servicio
    # ──────────────────────────────────────────────────────────────────────────

    def _build_panel_7_info(self) -> QWidget:
        """
        Panel 7 — Información del servicio y del sistema.
        Muestra datos de la cuenta sincronizada y la versión de rclone.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Información del servicio"))
        layout.addWidget(_separator())

        # Información del servicio
        self.lbl_info_name = QLabel()
        self.lbl_info_name.setFont(QFont("", 11))
        self.lbl_info_name.setWordWrap(True)
        layout.addWidget(self.lbl_info_name)

        self.lbl_info_platform = QLabel()
        self.lbl_info_platform.setFont(QFont("", 11))
        layout.addWidget(self.lbl_info_platform)

        self.lbl_info_folder = QLabel()
        self.lbl_info_folder.setFont(QFont("", 11))
        self.lbl_info_folder.setWordWrap(True)
        layout.addWidget(self.lbl_info_folder)

        self.lbl_info_interval = QLabel()
        self.lbl_info_interval.setFont(QFont("", 11))
        layout.addWidget(self.lbl_info_interval)

        self.lbl_info_status = QLabel()
        self.lbl_info_status.setFont(QFont("", 11))
        layout.addWidget(self.lbl_info_status)

        layout.addWidget(_separator())

        self.lbl_rclone_version = QLabel()
        self.lbl_rclone_version.setFont(QFont("", 11))
        layout.addWidget(self.lbl_rclone_version)

        layout.addStretch()
        return panel

    # ──────────────────────────────────────────────────────────────────────────
    # Navegación entre paneles
    # ──────────────────────────────────────────────────────────────────────────

    def _switch_panel(self, index: int):
        """
        Cambia el panel de contenido según el índice del menú lateral.

        :param index: Índice del panel a mostrar (0-6).
        """
        self.stack.setCurrentIndex(index)

        # Acciones especiales al mostrar ciertos paneles
        if index == 5:
            # Panel de disco: iniciar temporizador y actualizar inmediatamente
            self._refresh_disk_usage()
            if hasattr(self, "_disk_timer"):
                self._disk_timer.start(10000)
        else:
            # Detener el temporizador de disco si no estamos en ese panel
            if hasattr(self, "_disk_timer"):
                self._disk_timer.stop()

        if index == 6:
            # Panel de información: actualizar datos
            self._update_info_panel()

    def _update_info_panel(self):
        """Actualiza las etiquetas del panel de información con datos actuales."""
        s = self.service
        self.lbl_info_name.setText(f"<b>Nombre:</b> {s.get('name', '-')}")
        self.lbl_info_platform.setText(f"<b>Plataforma:</b> {s.get('platform', '-')}")
        self.lbl_info_folder.setText(f"<b>Carpeta local:</b> {s.get('local_folder', '-')}")
        interval = s.get("sync_interval", 5)
        self.lbl_info_interval.setText(f"<b>Sincronización:</b> cada {interval} minutos")
        status = s.get("status", "idle")
        self.lbl_info_status.setText(f"<b>Estado:</b> {status}")
        # Versión de rclone instalada
        version = get_rclone_version() or "rclone no instalado"
        self.lbl_rclone_version.setText(f"<b>Versión de rclone:</b> {version}")

    # ──────────────────────────────────────────────────────────────────────────
    # Poblar campos con valores actuales del servicio
    # ──────────────────────────────────────────────────────────────────────────

    def _populate_fields(self):
        """
        Rellena todos los campos de configuración con los valores actuales del servicio.
        """
        s = self.service

        # ── Panel 1: Defaults ────────────────────────────────────────────────
        self.chk_root.setChecked(s.get("remote_path", "/") == "/")
        self.chk_on_demand.setChecked(True)   # Siempre habilitado por defecto
        self.chk_bisync.setChecked(True)       # Siempre habilitado por defecto
        excluded = s.get("excluded_folders", [])
        self.chk_exclude_personal.setChecked("Personal Vault" in excluded)

        # ── Panel 2: Directorios ─────────────────────────────────────────────
        self.local_folder_input.setText(s.get("local_folder", ""))
        self.remote_path_input.setText(s.get("remote_path", "/"))

        # ── Panel 3: Exclusiones ─────────────────────────────────────────────
        self.exclusion_list.clear()
        for folder in excluded:
            self.exclusion_list.addItem(folder)

        # ── Panel 5: Intervalo ───────────────────────────────────────────────
        interval = s.get("sync_interval", 5)
        # Encontrar el índice correspondiente al intervalo guardado
        for i in range(self.interval_combo.count()):
            if self.interval_combo.itemData(i) == interval:
                self.interval_combo.setCurrentIndex(i)
                break

        self.chk_autostart.setChecked(s.get("start_with_system", False))
        self.startup_delay_spin.setValue(s.get("startup_delay", 0))

    # ──────────────────────────────────────────────────────────────────────────
    # Guardar todos los cambios
    # ──────────────────────────────────────────────────────────────────────────

    def _save_changes(self):
        """
        Recopila todos los valores de los campos y guarda la configuración.
        Actualiza el servicio en el archivo de configuración.
        """
        s = self.service

        # ── Panel 1: Defaults ────────────────────────────────────────────────
        if self.chk_root.isChecked():
            s["remote_path"] = "/"

        # ── Panel 2: Directorios ─────────────────────────────────────────────
        local_folder = self.local_folder_input.text().strip()
        if local_folder:
            s["local_folder"] = local_folder
        remote_path = self.remote_path_input.text().strip()
        if remote_path:
            s["remote_path"] = remote_path

        # ── Panel 3: Exclusiones ─────────────────────────────────────────────
        exclusions = []
        for i in range(self.exclusion_list.count()):
            exclusions.append(self.exclusion_list.item(i).text())
        # Agregar Personal Vault si está marcado
        if self.chk_exclude_personal.isChecked() and "Personal Vault" not in exclusions:
            exclusions.append("Personal Vault")
        elif not self.chk_exclude_personal.isChecked() and "Personal Vault" in exclusions:
            exclusions.remove("Personal Vault")
        s["excluded_folders"] = exclusions

        # ── Panel 4: Carpetas selectivas ─────────────────────────────────────
        selective = []
        for i in range(self.folder_tree.topLevelItemCount()):
            item = self.folder_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                selective.append(item.text(0))
        if selective:
            s["selective_folders"] = selective

        # ── Panel 5: Intervalo y arranque ────────────────────────────────────
        s["sync_interval"] = self.interval_combo.currentData()
        s["start_with_system"] = self.chk_autostart.isChecked()
        s["startup_delay"] = self.startup_delay_spin.value()

        # ── Guardar en el archivo de configuración ───────────────────────────
        cfg.update_service(self._original_name, s)

        QMessageBox.information(self, "Guardado", "Configuración guardada correctamente.")
        self.accept()
