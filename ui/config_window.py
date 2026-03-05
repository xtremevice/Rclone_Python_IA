"""
Ventana de configuración de servicio para Rclone Python IA.
Implementa las 7 secciones de configuración de un servicio:
  1. Configuración por defecto
  2. Cambiar directorio
  3. Carpetas excluidas
  4. Árbol de carpetas con checkboxes
  5. Intervalo de sincronización
  6. Liberar espacio / eliminar servicio
  7. Información del servicio
"""

import os
import threading

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import ConfigManager
from core.rclone import (
    format_bytes,
    free_disk_space,
    get_disk_usage,
    get_remote_folders,
    get_rclone_version,
)
from core.service import PLATFORM_DISPLAY_NAMES, SYNC_INTERVALS, Service


# Estilos comunes para la ventana de configuración
CONFIG_STYLESHEET = """
    QDialog {
        background-color: #FAFAFA;
    }
    QListWidget#menu_list {
        background-color: #263238;
        color: white;
        border: none;
        font-size: 13px;
        outline: none;
    }
    QListWidget#menu_list::item {
        padding: 14px 16px;
        border-bottom: 1px solid #37474F;
    }
    QListWidget#menu_list::item:selected {
        background-color: #2196F3;
        color: white;
    }
    QListWidget#menu_list::item:hover {
        background-color: #37474F;
    }
    QGroupBox {
        font-size: 13px;
        font-weight: bold;
        color: #424242;
        border: 1px solid #E0E0E0;
        border-radius: 6px;
        margin-top: 12px;
        padding-top: 8px;
        background-color: white;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
    }
    QPushButton#action_btn {
        background-color: #2196F3;
        color: white;
        border: none;
        padding: 8px 20px;
        border-radius: 4px;
        font-size: 13px;
    }
    QPushButton#action_btn:hover {
        background-color: #1976D2;
    }
    QPushButton#danger_btn {
        background-color: #F44336;
        color: white;
        border: none;
        padding: 8px 20px;
        border-radius: 4px;
        font-size: 13px;
    }
    QPushButton#danger_btn:hover {
        background-color: #D32F2F;
    }
    QPushButton#save_btn {
        background-color: #4CAF50;
        color: white;
        border: none;
        padding: 10px 32px;
        border-radius: 4px;
        font-size: 14px;
        font-weight: bold;
    }
    QPushButton#save_btn:hover {
        background-color: #388E3C;
    }
    QCheckBox {
        font-size: 13px;
        color: #424242;
        spacing: 8px;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
    }
    QComboBox {
        border: 1px solid #BDBDBD;
        border-radius: 4px;
        padding: 6px;
        font-size: 13px;
        background-color: white;
        min-width: 180px;
    }
    QLineEdit {
        border: 1px solid #BDBDBD;
        border-radius: 4px;
        padding: 8px;
        font-size: 13px;
        background-color: white;
    }
    QSpinBox {
        border: 1px solid #BDBDBD;
        border-radius: 4px;
        padding: 6px;
        font-size: 13px;
        background-color: white;
    }
"""


class ConfigWindow(QDialog):
    """
    Ventana de configuración de un servicio de sincronización.
    Contiene 7 secciones accesibles desde un menú lateral izquierdo.
    """

    # Señal emitida cuando se guardan cambios en el servicio
    service_updated = pyqtSignal(object)
    # Señal emitida cuando se elimina el servicio
    service_deleted = pyqtSignal(str)

    def __init__(self, service: Service, config_manager: ConfigManager, parent=None):
        """Inicializa la ventana de configuración con el servicio a editar."""
        super().__init__(parent)
        # Referencia al servicio que se está configurando
        self.service = service
        # Copia de trabajo para modificar antes de guardar
        self.working_service = Service.from_dict(service.to_dict())
        # Referencia al gestor de configuración
        self.config_manager = config_manager
        # Timer para actualizar el uso de disco cada 10 segundos
        self.disk_usage_timer = None

        self._setup_window()
        self._build_ui()
        # Mostrar sección 1 por defecto
        self._show_section(0)

    def _setup_window(self):
        """Configura las propiedades de la ventana de configuración."""
        self.setWindowTitle(f"Configuración - {self.service.get_display_name()}")

        # Obtener dimensiones de pantalla
        screen = QApplication.primaryScreen().geometry()
        screen_w = screen.width()
        screen_h = screen.height()

        # Tamaño: 60% alto, 70% ancho
        win_w = int(screen_w * 0.70)
        win_h = int(screen_h * 0.60)

        self.setFixedSize(win_w, win_h)

        # Centrar en pantalla
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.move(x, y)

        self.setStyleSheet(CONFIG_STYLESHEET)

    def _build_ui(self):
        """Construye la interfaz principal de la ventana de configuración."""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Panel izquierdo: menú de secciones
        left_panel = QWidget()
        left_panel.setFixedWidth(200)
        left_panel.setStyleSheet("background-color: #263238;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # Título del menú lateral
        menu_title = QLabel(f"  {self.service.get_display_name()}")
        menu_title.setStyleSheet(
            "background-color: #1C2529; color: white; font-size: 13px; "
            "font-weight: bold; padding: 16px 12px; border-bottom: 1px solid #37474F;"
        )
        menu_title.setWordWrap(True)
        left_layout.addWidget(menu_title)

        # Lista del menú de secciones
        self.menu_list = QListWidget()
        self.menu_list.setObjectName("menu_list")
        self.menu_list.setFocusPolicy(Qt.NoFocus)

        # Agregar ítems al menú
        menu_items = [
            "⚙️  Config. por defecto",
            "📁  Cambiar directorio",
            "🚫  Carpetas excluidas",
            "🌲  Árbol de carpetas",
            "⏱️  Intervalo sync",
            "💾  Espacio en disco",
            "ℹ️  Información",
        ]
        for item_text in menu_items:
            self.menu_list.addItem(item_text)

        self.menu_list.currentRowChanged.connect(self._show_section)
        left_layout.addWidget(self.menu_list, stretch=1)

        # Botón guardar en la parte inferior del menú
        save_btn = QPushButton("💾 Guardar cambios")
        save_btn.setObjectName("save_btn")
        save_btn.setFixedHeight(48)
        save_btn.clicked.connect(self._save_changes)
        save_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; border: none; "
            "font-size: 13px; font-weight: bold; } "
            "QPushButton:hover { background-color: #388E3C; }"
        )
        left_layout.addWidget(save_btn)

        main_layout.addWidget(left_panel)

        # Panel derecho: área de contenido de cada sección
        right_panel = QWidget()
        right_panel.setStyleSheet("background-color: #FAFAFA;")
        self.right_layout = QVBoxLayout(right_panel)
        self.right_layout.setContentsMargins(24, 24, 24, 24)
        self.right_layout.setSpacing(12)

        # Área de scroll para el contenido
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet("background-color: transparent;")
        self.right_layout.addWidget(self.scroll_area)

        main_layout.addWidget(right_panel, stretch=1)

    def _clear_content(self):
        """Limpia el área de contenido para mostrar la siguiente sección."""
        # Detener timer de disco si estaba activo
        if self.disk_usage_timer:
            self.disk_usage_timer.stop()
            self.disk_usage_timer = None

        # Crear nuevo widget de contenido vacío
        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: transparent;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(12)
        self.scroll_area.setWidget(content_widget)

    def _show_section(self, index: int):
        """
        Muestra el contenido de la sección indicada por el índice del menú.
        Llama al método de construcción de UI correspondiente.
        """
        self._clear_content()
        # Actualizar selección del menú
        if self.menu_list.currentRow() != index:
            self.menu_list.setCurrentRow(index)

        # Mapeo de índice a método de construcción
        section_builders = [
            self._build_section_default,
            self._build_section_directory,
            self._build_section_exclusions,
            self._build_section_folder_tree,
            self._build_section_interval,
            self._build_section_disk_space,
            self._build_section_info,
        ]

        if 0 <= index < len(section_builders):
            section_builders[index]()

    def _section_title(self, title: str, subtitle: str = ""):
        """Agrega un título y subtítulo de sección al layout de contenido."""
        title_label = QLabel(title)
        title_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #212121;"
        )
        self.content_layout.addWidget(title_label)

        if subtitle:
            sub_label = QLabel(subtitle)
            sub_label.setWordWrap(True)
            sub_label.setStyleSheet("font-size: 12px; color: #757575;")
            self.content_layout.addWidget(sub_label)

        # Línea separadora
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E0E0E0; margin: 4px 0;")
        self.content_layout.addWidget(line)

    def _build_section_default(self):
        """
        Construye la sección 1: Configuración por defecto.
        Muestra opciones de configuración estándar de rclone.
        """
        self._section_title(
            "⚙️ Configuración por defecto",
            "Opciones estándar de sincronización para este servicio."
        )

        # Grupo de opciones por defecto
        group = QGroupBox("Opciones de sincronización")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(12)

        # Checkbox: sincronizar desde el directorio raíz
        self.cb_root = QCheckBox(
            "Sincronizar desde el directorio raíz (/)"
        )
        self.cb_root.setChecked(self.working_service.remote_path == "/")
        self.cb_root.setToolTip(
            "Los datos se sincronizarán desde la raíz del servicio en la nube."
        )
        group_layout.addWidget(self.cb_root)

        # Checkbox: descarga bajo demanda (on-demand)
        self.cb_on_demand = QCheckBox(
            "Descargar solo archivos usados (on-demand)"
        )
        self.cb_on_demand.setChecked(self.working_service.on_demand)
        self.cb_on_demand.setToolTip(
            "Solo descarga archivos cuando se accede a ellos, ahorrando espacio."
        )
        group_layout.addWidget(self.cb_on_demand)

        # Checkbox: usar resync (sincronización bidireccional)
        self.cb_resync = QCheckBox(
            "Usar resync (sincronización bidireccional en la nube)"
        )
        self.cb_resync.setChecked(self.working_service.use_resync)
        self.cb_resync.setToolTip(
            "Sincroniza cambios tanto locales como en la nube (bisync)."
        )
        group_layout.addWidget(self.cb_resync)

        # Checkbox: excluir carpeta "Archivo personal" de OneDrive
        self.cb_vault = QCheckBox(
            'Excluir "Archivo personal" de OneDrive (recomendado)'
        )
        self.cb_vault.setChecked(self.working_service.exclude_personal_vault)
        self.cb_vault.setToolTip(
            "La carpeta Personal Vault de OneDrive causa errores al sincronizar."
        )
        group_layout.addWidget(self.cb_vault)

        self.content_layout.addWidget(group)

        # Nota informativa
        info = QLabel(
            "ℹ️ Estos ajustes representan la configuración recomendada para la "
            "mayoría de los usuarios. Los cambios se aplicarán al guardar."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "background-color: #E3F2FD; padding: 12px; border-radius: 4px; "
            "color: #1565C0; font-size: 12px;"
        )
        self.content_layout.addWidget(info)
        self.content_layout.addStretch()

    def _build_section_directory(self):
        """
        Construye la sección 2: Cambiar directorio.
        Permite modificar la carpeta local y la ruta remota del servicio.
        """
        self._section_title(
            "📁 Cambiar directorio",
            "Modifica la carpeta local o la ruta remota dentro del servicio."
        )

        # Grupo de directorio local
        local_group = QGroupBox("Directorio local")
        local_layout = QVBoxLayout(local_group)

        local_row = QHBoxLayout()
        self.dir_local_input = QLineEdit()
        self.dir_local_input.setText(self.working_service.local_path)
        self.dir_local_input.setPlaceholderText("Ruta de la carpeta local...")
        local_row.addWidget(self.dir_local_input)

        browse_local_btn = QPushButton("Examinar...")
        browse_local_btn.setObjectName("action_btn")
        browse_local_btn.setFixedWidth(110)
        browse_local_btn.clicked.connect(self._browse_local_dir)
        local_row.addWidget(browse_local_btn)
        local_layout.addLayout(local_row)

        local_info = QLabel("Carpeta en tu computadora donde se guardarán los archivos.")
        local_info.setStyleSheet("color: #757575; font-size: 11px;")
        local_layout.addWidget(local_info)
        self.content_layout.addWidget(local_group)

        # Grupo de directorio remoto
        remote_group = QGroupBox("Directorio remoto (en la nube)")
        remote_layout = QVBoxLayout(remote_group)

        self.dir_remote_input = QLineEdit()
        self.dir_remote_input.setText(self.working_service.remote_path)
        self.dir_remote_input.setPlaceholderText("/ruta/en/la/nube")
        remote_layout.addWidget(self.dir_remote_input)

        remote_info = QLabel(
            "Ruta dentro del servicio en la nube. Usa '/' para sincronizar todo."
        )
        remote_info.setStyleSheet("color: #757575; font-size: 11px;")
        remote_layout.addWidget(remote_info)
        self.content_layout.addWidget(remote_group)

        self.content_layout.addStretch()

    def _browse_local_dir(self):
        """Abre el diálogo para cambiar el directorio local."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar directorio local",
            self.working_service.local_path or os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly,
        )
        if folder:
            self.dir_local_input.setText(folder)

    def _build_section_exclusions(self):
        """
        Construye la sección 3: Carpetas excluidas.
        Permite agregar o quitar carpetas de la lista de exclusión.
        """
        self._section_title(
            "🚫 Carpetas excluidas",
            "Carpetas que no se sincronizarán con el servicio en la nube."
        )

        # Lista de carpetas excluidas
        exclusion_group = QGroupBox("Carpetas excluidas de la sincronización")
        excl_layout = QVBoxLayout(exclusion_group)

        # Widget de lista
        self.exclusions_list = QListWidget()
        self.exclusions_list.setFixedHeight(200)
        for folder in self.working_service.excluded_folders:
            if folder:
                self.exclusions_list.addItem(folder)
        excl_layout.addWidget(self.exclusions_list)

        # Fila para agregar nueva exclusión
        add_row = QHBoxLayout()
        self.new_exclusion_input = QLineEdit()
        self.new_exclusion_input.setPlaceholderText(
            "Nombre de la carpeta a excluir (ej: Downloads)"
        )
        add_row.addWidget(self.new_exclusion_input)

        add_btn = QPushButton("➕ Agregar")
        add_btn.setObjectName("action_btn")
        add_btn.setFixedWidth(100)
        add_btn.clicked.connect(self._add_exclusion)
        add_row.addWidget(add_btn)
        excl_layout.addLayout(add_row)

        # Botón para eliminar la exclusión seleccionada
        remove_btn = QPushButton("🗑️ Quitar seleccionado")
        remove_btn.setObjectName("action_btn")
        remove_btn.setStyleSheet(
            "background-color: #FF7043; color: white; border: none; "
            "padding: 8px 20px; border-radius: 4px; font-size: 13px;"
        )
        remove_btn.clicked.connect(self._remove_exclusion)
        excl_layout.addWidget(remove_btn, alignment=Qt.AlignLeft)

        self.content_layout.addWidget(exclusion_group)

        # Nota sobre la exclusión del Archivo Personal de OneDrive
        vault_note = QLabel(
            "📌 La carpeta 'Personal Vault' de OneDrive se controla en "
            "'Configuración por defecto'."
        )
        vault_note.setWordWrap(True)
        vault_note.setStyleSheet(
            "background-color: #FFF3E0; padding: 10px; border-radius: 4px; "
            "color: #E65100; font-size: 12px;"
        )
        self.content_layout.addWidget(vault_note)
        self.content_layout.addStretch()

    def _add_exclusion(self):
        """Agrega una nueva carpeta a la lista de exclusiones."""
        folder = self.new_exclusion_input.text().strip()
        if folder:
            # Verificar que no esté ya en la lista
            items = [
                self.exclusions_list.item(i).text()
                for i in range(self.exclusions_list.count())
            ]
            if folder not in items:
                self.exclusions_list.addItem(folder)
                self.new_exclusion_input.clear()

    def _remove_exclusion(self):
        """Quita la carpeta seleccionada de la lista de exclusiones."""
        current_row = self.exclusions_list.currentRow()
        if current_row >= 0:
            self.exclusions_list.takeItem(current_row)

    def _build_section_folder_tree(self):
        """
        Construye la sección 4: Árbol de carpetas.
        Muestra las carpetas del servicio en forma de árbol con checkboxes.
        """
        self._section_title(
            "🌲 Árbol de carpetas",
            "Selecciona qué carpetas sincronizar del servicio en la nube."
        )

        # Botón para cargar/actualizar el árbol de carpetas
        load_btn = QPushButton("🔄 Cargar carpetas del servicio")
        load_btn.setObjectName("action_btn")
        load_btn.clicked.connect(self._load_folder_tree)
        self.content_layout.addWidget(load_btn, alignment=Qt.AlignLeft)

        # Widget de árbol
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabels(["Carpeta", "Estado"])
        self.folder_tree.setColumnWidth(0, 350)
        self.folder_tree.setStyleSheet(
            "QTreeWidget { border: 1px solid #E0E0E0; background-color: white; "
            "font-size: 13px; } "
            "QTreeWidget::item { padding: 4px; }"
        )
        self.content_layout.addWidget(self.folder_tree, stretch=1)

        # Cargar carpetas automáticamente si hay remote configurado
        if self.working_service.rclone_remote:
            self._load_folder_tree()

    def _load_folder_tree(self):
        """Carga la lista de carpetas del remote en el árbol de forma asíncrona."""
        self.folder_tree.clear()

        # Item de carga
        loading_item = QTreeWidgetItem(["Cargando...", ""])
        self.folder_tree.addTopLevelItem(loading_item)

        def fetch_folders():
            """Obtiene las carpetas del remote en un hilo separado."""
            folders = get_remote_folders(self.working_service)
            QTimer.singleShot(0, lambda: self._populate_folder_tree(folders))

        thread = threading.Thread(target=fetch_folders, daemon=True)
        thread.start()

    def _populate_folder_tree(self, folders: list):
        """
        Rellena el árbol de carpetas con los datos obtenidos.
        Marca con checkbox las carpetas que están sincronizadas.
        """
        self.folder_tree.clear()

        if not folders:
            no_item = QTreeWidgetItem(
                ["No se pudieron cargar las carpetas.", ""]
            )
            no_item.setForeground(0, Qt.gray)
            self.folder_tree.addTopLevelItem(no_item)
            return

        # Agregar cada carpeta al árbol
        for folder_data in folders:
            item = QTreeWidgetItem()
            item.setText(0, folder_data["name"])
            item.setText(1, "Sincronizada" if folder_data.get("synced") else "Excluida")

            # Checkbox para habilitar/deshabilitar sincronización
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                0, Qt.Checked if folder_data.get("synced") else Qt.Unchecked
            )
            # Guardar nombre de carpeta en datos del ítem
            item.setData(0, Qt.UserRole, folder_data["name"])
            self.folder_tree.addTopLevelItem(item)

        # Conectar cambio de estado de checkbox
        self.folder_tree.itemChanged.connect(self._on_folder_tree_item_changed)

    def _on_folder_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        """
        Maneja el cambio de estado de un ítem del árbol.
        Actualiza las exclusiones según si el checkbox está marcado o no.
        """
        if column != 0:
            return

        folder_name = item.data(0, Qt.UserRole)
        if not folder_name:
            return

        if item.checkState(0) == Qt.Checked:
            # Carpeta habilitada: quitar de exclusiones
            item.setText(1, "Sincronizada")
            if folder_name in self.working_service.excluded_folders:
                self.working_service.excluded_folders.remove(folder_name)
        else:
            # Carpeta deshabilitada: agregar a exclusiones
            item.setText(1, "Excluida")
            if folder_name not in self.working_service.excluded_folders:
                self.working_service.excluded_folders.append(folder_name)

    def _build_section_interval(self):
        """
        Construye la sección 5: Intervalo de sincronización.
        Permite configurar cada cuánto tiempo se sincronizan los datos.
        """
        self._section_title(
            "⏱️ Intervalo de sincronización",
            "Configura la frecuencia con la que se sincronizarán los datos."
        )

        # Grupo de intervalo
        interval_group = QGroupBox("Frecuencia de sincronización")
        interval_layout = QVBoxLayout(interval_group)
        interval_layout.setSpacing(12)

        # ComboBox con opciones de intervalo
        interval_label = QLabel("Sincronizar cada:")
        interval_label.setStyleSheet("font-size: 13px; color: #424242;")
        interval_layout.addWidget(interval_label)

        self.interval_combo = QComboBox()
        for interval_name in SYNC_INTERVALS.keys():
            self.interval_combo.addItem(interval_name)

        # Seleccionar el intervalo actual
        current_display = self.working_service.get_sync_interval_display()
        idx = self.interval_combo.findText(current_display)
        if idx >= 0:
            self.interval_combo.setCurrentIndex(idx)

        interval_layout.addWidget(self.interval_combo)
        self.content_layout.addWidget(interval_group)

        # Grupo de inicio con el sistema
        startup_group = QGroupBox("Inicio con el sistema")
        startup_layout = QVBoxLayout(startup_group)
        startup_layout.setSpacing(12)

        # Checkbox para iniciar con el sistema
        self.cb_startup = QCheckBox("Iniciar este programa con el sistema")
        self.cb_startup.setChecked(self.working_service.start_with_system)
        startup_layout.addWidget(self.cb_startup)

        # SpinBox para el retraso de inicio
        delay_row = QHBoxLayout()
        delay_label = QLabel("Retraso al iniciar (segundos):")
        delay_label.setStyleSheet("font-size: 13px; color: #424242;")
        delay_row.addWidget(delay_label)

        self.startup_delay_spin = QSpinBox()
        self.startup_delay_spin.setRange(0, 3600)
        self.startup_delay_spin.setValue(self.working_service.startup_delay)
        self.startup_delay_spin.setSuffix(" seg")
        delay_row.addWidget(self.startup_delay_spin)
        delay_row.addStretch()
        startup_layout.addLayout(delay_row)

        self.content_layout.addWidget(startup_group)
        self.content_layout.addStretch()

    def _build_section_disk_space(self):
        """
        Construye la sección 6: Espacio en disco.
        Muestra el uso de disco y opciones para liberar espacio o eliminar el servicio.
        """
        self._section_title(
            "💾 Espacio en disco",
            "Administra el uso de almacenamiento local de este servicio."
        )

        # Grupo de uso de disco
        disk_group = QGroupBox("Uso de disco")
        disk_layout = QVBoxLayout(disk_group)
        disk_layout.setSpacing(12)

        # Etiquetas de uso de disco (se actualizan dinámicamente)
        self.disk_used_label = QLabel("Calculando...")
        self.disk_used_label.setStyleSheet(
            "font-size: 24px; font-weight: bold; color: #2196F3;"
        )
        disk_layout.addWidget(self.disk_used_label)

        self.disk_total_label = QLabel("")
        self.disk_total_label.setStyleSheet("font-size: 13px; color: #757575;")
        disk_layout.addWidget(self.disk_total_label)

        # Botón para liberar espacio
        free_btn = QPushButton("🗜️ Liberar espacio en disco")
        free_btn.setObjectName("action_btn")
        free_btn.clicked.connect(self._free_disk_space)
        disk_layout.addWidget(free_btn, alignment=Qt.AlignLeft)

        free_info = QLabel(
            "Los archivos descargados volverán a estar solo en la nube, "
            "liberando espacio local."
        )
        free_info.setWordWrap(True)
        free_info.setStyleSheet("color: #757575; font-size: 11px;")
        disk_layout.addWidget(free_info)
        self.content_layout.addWidget(disk_group)

        # Grupo de eliminar servicio
        delete_group = QGroupBox("Eliminar servicio")
        delete_layout = QVBoxLayout(delete_group)

        delete_warning = QLabel(
            "⚠️ Esta acción eliminará permanentemente la configuración del "
            "servicio. Los archivos locales no serán afectados."
        )
        delete_warning.setWordWrap(True)
        delete_warning.setStyleSheet("color: #D32F2F; font-size: 12px;")
        delete_layout.addWidget(delete_warning)

        delete_btn = QPushButton("🗑️ Eliminar este servicio")
        delete_btn.setObjectName("danger_btn")
        delete_btn.clicked.connect(self._confirm_delete_service)
        delete_layout.addWidget(delete_btn, alignment=Qt.AlignLeft)
        self.content_layout.addWidget(delete_group)

        self.content_layout.addStretch()

        # Actualizar uso de disco inmediatamente
        self._update_disk_usage()

        # Timer para actualizar cada 10 segundos mientras está visible
        self.disk_usage_timer = QTimer(self)
        self.disk_usage_timer.timeout.connect(self._update_disk_usage)
        self.disk_usage_timer.start(10000)

    def _update_disk_usage(self):
        """Actualiza las etiquetas de uso de disco con datos actuales."""
        usage = get_disk_usage(self.working_service.local_path)
        used_str = format_bytes(usage["used"])
        total_str = format_bytes(usage["total"])
        free_str = format_bytes(usage["free"])

        if hasattr(self, "disk_used_label"):
            self.disk_used_label.setText(f"{used_str} usados")
        if hasattr(self, "disk_total_label"):
            self.disk_total_label.setText(
                f"Total: {total_str}  |  Libre: {free_str}"
            )

    def _free_disk_space(self):
        """Intenta liberar el espacio en disco del servicio."""
        result = free_disk_space(self.working_service)
        if result:
            QMessageBox.information(
                self,
                "Espacio liberado",
                "El espacio en disco ha sido liberado correctamente.\n"
                "Los archivos ahora están solo en la nube.",
            )
        else:
            QMessageBox.warning(
                self,
                "No se pudo liberar",
                "No fue posible liberar el espacio en disco.\n"
                "Asegúrese de que el servicio de rclone esté activo.",
            )
        self._update_disk_usage()

    def _confirm_delete_service(self):
        """Muestra un diálogo de confirmación antes de eliminar el servicio."""
        reply = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Está seguro de que desea eliminar el servicio "
            f"'{self.service.get_display_name()}'?\n\n"
            "La configuración será eliminada permanentemente. "
            "Los archivos locales no serán afectados.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # Emitir señal de eliminación
            self.service_deleted.emit(self.service.service_id)
            self.close()

    def _build_section_info(self):
        """
        Construye la sección 7: Información del servicio.
        Muestra detalles completos del servicio y de rclone.
        """
        self._section_title(
            "ℹ️ Información del servicio",
            "Detalles del servicio y del cliente rclone instalado."
        )

        # Grupo de información del servicio
        service_group = QGroupBox("Detalles del servicio")
        service_layout = QVBoxLayout(service_group)
        service_layout.setSpacing(8)

        # Datos del servicio a mostrar
        platform_name = PLATFORM_DISPLAY_NAMES.get(
            self.service.platform, self.service.platform.capitalize()
        )
        info_rows = [
            ("Nombre del servicio:", self.service.get_display_name()),
            ("Plataforma:", platform_name),
            ("Cuenta/Remote:", self.service.get_rclone_remote_name()),
            ("Carpeta local:", self.service.local_path),
            ("Ruta remota:", self.service.remote_path),
            ("Intervalo de sync:", self.service.get_sync_interval_display()),
            ("Estado actual:", self.service.get_status_display()),
            ("Última sincronización:", self.service.last_sync or "Nunca"),
        ]

        for label, value in info_rows:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #757575; font-size: 12px; min-width: 180px; "
                "font-weight: bold;"
            )
            val = QLabel(value)
            val.setStyleSheet("color: #212121; font-size: 12px;")
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, stretch=1)
            service_layout.addLayout(row)

        self.content_layout.addWidget(service_group)

        # Grupo de información de rclone
        rclone_group = QGroupBox("Versión de rclone")
        rclone_layout = QVBoxLayout(rclone_group)

        rclone_version = get_rclone_version()
        version_label = QLabel(rclone_version)
        version_label.setStyleSheet(
            "font-size: 13px; color: #212121; font-family: monospace;"
        )
        rclone_layout.addWidget(version_label)

        self.content_layout.addWidget(rclone_group)
        self.content_layout.addStretch()

    def _collect_form_data(self):
        """
        Recopila los datos de todos los formularios de la ventana.
        Actualiza working_service con los valores actuales de los controles.
        """
        current_section = self.menu_list.currentRow()

        # Recopilar datos de la sección 1 (config por defecto)
        if hasattr(self, "cb_root"):
            self.working_service.remote_path = "/" if self.cb_root.isChecked() else self.working_service.remote_path
        if hasattr(self, "cb_on_demand"):
            self.working_service.on_demand = self.cb_on_demand.isChecked()
        if hasattr(self, "cb_resync"):
            self.working_service.use_resync = self.cb_resync.isChecked()
        if hasattr(self, "cb_vault"):
            self.working_service.exclude_personal_vault = self.cb_vault.isChecked()

        # Recopilar datos de la sección 2 (directorio)
        if hasattr(self, "dir_local_input"):
            self.working_service.local_path = self.dir_local_input.text().strip()
        if hasattr(self, "dir_remote_input"):
            self.working_service.remote_path = self.dir_remote_input.text().strip()

        # Recopilar datos de la sección 3 (exclusiones)
        if hasattr(self, "exclusions_list"):
            self.working_service.excluded_folders = [
                self.exclusions_list.item(i).text()
                for i in range(self.exclusions_list.count())
            ]

        # Recopilar datos de la sección 5 (intervalo)
        if hasattr(self, "interval_combo"):
            interval_name = self.interval_combo.currentText()
            self.working_service.sync_interval = SYNC_INTERVALS.get(
                interval_name, self.working_service.sync_interval
            )
        if hasattr(self, "cb_startup"):
            self.working_service.start_with_system = self.cb_startup.isChecked()
        if hasattr(self, "startup_delay_spin"):
            self.working_service.startup_delay = self.startup_delay_spin.value()

    def _save_changes(self):
        """
        Guarda todos los cambios de configuración.
        Actualiza el servicio en el gestor de configuración.
        """
        # Recopilar datos de los formularios visibles
        self._collect_form_data()

        # Actualizar el servicio original con los cambios
        for key, value in self.working_service.to_dict().items():
            setattr(self.service, key, value)

        # Guardar en el gestor de configuración
        self.config_manager.update_service(self.service)

        # Emitir señal de actualización
        self.service_updated.emit(self.service)

        # Mostrar confirmación
        QMessageBox.information(
            self,
            "Guardado",
            "✅ La configuración ha sido guardada correctamente.",
        )

    def closeEvent(self, event):
        """Detiene los timers al cerrar la ventana."""
        if self.disk_usage_timer:
            self.disk_usage_timer.stop()
        super().closeEvent(event)
