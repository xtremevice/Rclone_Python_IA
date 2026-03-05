"""
Asistente de configuración de nuevo servicio para Rclone Python IA.
Implementa las 3 ventanas del proceso de agregar un nuevo servicio:
  1. Selección de carpeta local
  2. Selección de plataforma/servicio
  3. Autenticación y confirmación
"""

import os

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from core.rclone import authorize_service, is_rclone_installed
from core.service import (
    PLATFORM_DISPLAY_NAMES,
    SUPPORTED_PLATFORMS,
    Service,
)


def _create_icon_pixmap() -> QPixmap:
    """Crea el ícono de la aplicación como QPixmap desde SVG."""
    from resources import get_icon_bytes
    pixmap = QPixmap()
    pixmap.loadFromData(get_icon_bytes(), "SVG")
    return pixmap


class StepIndicator(QWidget):
    """
    Widget que muestra el indicador de pasos del asistente.
    Muestra círculos numerados para cada paso, resaltando el actual.
    """

    def __init__(self, total_steps: int, current_step: int, parent=None):
        """Inicializa el indicador con el número total de pasos y el actual."""
        super().__init__(parent)
        # Número total de pasos del asistente
        self.total_steps = total_steps
        # Paso actual (1-indexed)
        self.current_step = current_step
        self._build_ui()

    def _build_ui(self):
        """Construye la interfaz del indicador de pasos."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setAlignment(Qt.AlignCenter)

        # Nombres de los pasos para mostrar debajo de los círculos
        step_names = ["Carpeta", "Plataforma", "Autenticación"]

        for i in range(1, self.total_steps + 1):
            # Contenedor vertical para círculo + texto
            step_container = QVBoxLayout()
            step_container.setAlignment(Qt.AlignCenter)

            # Círculo del paso
            circle = QLabel(str(i))
            circle.setFixedSize(32, 32)
            circle.setAlignment(Qt.AlignCenter)

            if i == self.current_step:
                # Paso actual: fondo azul
                circle.setStyleSheet(
                    "background-color: #2196F3; color: white; "
                    "border-radius: 16px; font-weight: bold; font-size: 14px;"
                )
            elif i < self.current_step:
                # Paso completado: fondo verde
                circle.setStyleSheet(
                    "background-color: #4CAF50; color: white; "
                    "border-radius: 16px; font-weight: bold; font-size: 14px;"
                )
            else:
                # Paso pendiente: fondo gris
                circle.setStyleSheet(
                    "background-color: #9E9E9E; color: white; "
                    "border-radius: 16px; font-size: 14px;"
                )

            step_container.addWidget(circle, alignment=Qt.AlignCenter)

            # Etiqueta con nombre del paso
            if i <= len(step_names):
                name_label = QLabel(step_names[i - 1])
                name_label.setStyleSheet("color: #555; font-size: 11px;")
                name_label.setAlignment(Qt.AlignCenter)
                step_container.addWidget(name_label, alignment=Qt.AlignCenter)

            # Agregar contenedor al layout principal
            layout.addLayout(step_container)

            # Línea conectora entre pasos (excepto después del último)
            if i < self.total_steps:
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setFixedWidth(40)
                line.setStyleSheet("color: #BDBDBD;")
                layout.addWidget(line)


class ServiceWizard(QDialog):
    """
    Asistente para agregar un nuevo servicio de sincronización.
    Maneja el flujo de 3 pasos para configurar un nuevo servicio.
    """

    # Señal emitida cuando el servicio es creado exitosamente
    service_created = pyqtSignal(object)

    def __init__(self, parent=None):
        """Inicializa el asistente de nuevo servicio."""
        super().__init__(parent)
        # Objeto servicio que se está creando
        self.service = Service()
        # Paso actual del asistente (1, 2 o 3)
        self.current_step = 1
        # Token de autenticación obtenido
        self.auth_token = None

        self._setup_window()
        self._build_ui()
        self._show_step(1)

    def _setup_window(self):
        """Configura las propiedades de la ventana del asistente."""
        self.setWindowTitle("Agregar Nuevo Servicio - Rclone Python IA")

        # Obtener dimensiones de la pantalla
        screen = QApplication.primaryScreen().geometry()
        screen_w = screen.width()
        screen_h = screen.height()

        # Tamaño: 70% alto, 60% ancho
        win_w = int(screen_w * 0.60)
        win_h = int(screen_h * 0.70)

        self.setFixedSize(win_w, win_h)

        # Centrar en pantalla
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.move(x, y)

        # Aplicar hoja de estilos
        self.setStyleSheet("""
            QDialog {
                background-color: #FAFAFA;
            }
            QLabel#title {
                font-size: 18px;
                font-weight: bold;
                color: #212121;
            }
            QLabel#subtitle {
                font-size: 13px;
                color: #616161;
            }
            QPushButton#primary {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 10px 24px;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#primary:hover {
                background-color: #1976D2;
            }
            QPushButton#primary:disabled {
                background-color: #BDBDBD;
            }
            QPushButton#secondary {
                background-color: white;
                color: #2196F3;
                border: 1px solid #2196F3;
                padding: 10px 24px;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton#secondary:hover {
                background-color: #E3F2FD;
            }
            QLineEdit {
                border: 1px solid #BDBDBD;
                border-radius: 4px;
                padding: 8px;
                font-size: 13px;
                background-color: white;
            }
            QLineEdit:focus {
                border: 2px solid #2196F3;
            }
            QListWidget {
                border: 1px solid #BDBDBD;
                border-radius: 4px;
                background-color: white;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #F0F0F0;
            }
            QListWidget::item:selected {
                background-color: #E3F2FD;
                color: #1565C0;
            }
            QListWidget::item:hover {
                background-color: #F5F5F5;
            }
        """)

    def _build_ui(self):
        """Construye la estructura principal de la interfaz del asistente."""
        # Layout principal vertical
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 24, 32, 24)
        main_layout.setSpacing(16)

        # Encabezado con ícono y título
        header_layout = QHBoxLayout()
        icon_label = QLabel()
        pixmap = _create_icon_pixmap()
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header_layout.addWidget(icon_label)

        header_text = QVBoxLayout()
        app_title = QLabel("Rclone Python IA")
        app_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2196F3;")
        header_text.addWidget(app_title)
        wizard_subtitle = QLabel("Asistente de nuevo servicio")
        wizard_subtitle.setStyleSheet("font-size: 12px; color: #757575;")
        header_text.addWidget(wizard_subtitle)
        header_layout.addLayout(header_text)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Línea separadora
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("color: #E0E0E0;")
        main_layout.addWidget(separator)

        # Indicador de pasos
        self.step_indicator = StepIndicator(3, 1, self)
        main_layout.addWidget(self.step_indicator)

        # Línea separadora inferior del indicador
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #E0E0E0;")
        main_layout.addWidget(sep2)

        # Área de contenido de cada paso (se reemplaza con cada paso)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        main_layout.addWidget(self.content_widget, stretch=1)

        # Botones de navegación en la parte inferior
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.btn_back = QPushButton("← Atrás")
        self.btn_back.setObjectName("secondary")
        self.btn_back.clicked.connect(self._on_back)
        btn_layout.addWidget(self.btn_back)

        btn_layout.addStretch()

        self.btn_next = QPushButton("Siguiente →")
        self.btn_next.setObjectName("primary")
        self.btn_next.clicked.connect(self._on_next)
        btn_layout.addWidget(self.btn_next)

        main_layout.addLayout(btn_layout)

    def _clear_content(self):
        """Limpia el área de contenido para mostrar el siguiente paso."""
        # Eliminar todos los widgets del área de contenido
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_step(self, step: int):
        """
        Muestra el contenido del paso indicado.
        Actualiza el indicador de pasos y los botones de navegación.
        """
        self.current_step = step
        self._clear_content()

        # Actualizar indicador de pasos
        self.step_indicator.deleteLater()
        self.step_indicator = StepIndicator(3, step, self)
        # Re-insertar indicador (está en la posición 3 del layout principal)
        main_layout = self.layout()
        main_layout.insertWidget(3, self.step_indicator)

        # Mostrar contenido del paso correspondiente
        if step == 1:
            self._build_step1()
            self.btn_back.setVisible(False)
            self.btn_next.setText("Siguiente →")
        elif step == 2:
            self._build_step2()
            self.btn_back.setVisible(True)
            self.btn_next.setText("Siguiente →")
        elif step == 3:
            self._build_step3()
            self.btn_back.setVisible(True)
            self.btn_next.setText("Crear Servicio")

    def _build_step1(self):
        """
        Construye la interfaz del paso 1: selección de nombre y carpeta local.
        Permite al usuario ingresar un nombre y elegir la carpeta de sincronización.
        """
        # Título del paso
        title = QLabel("Paso 1: Configurar carpeta local")
        title.setObjectName("title")
        self.content_layout.addWidget(title)

        subtitle = QLabel(
            "Ingrese un nombre para identificar este servicio y seleccione "
            "la carpeta local donde se sincronizarán los datos."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        self.content_layout.addWidget(subtitle)

        self.content_layout.addSpacing(20)

        # Campo de nombre del servicio
        name_label = QLabel("Nombre del servicio:")
        name_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #424242;")
        self.content_layout.addWidget(name_label)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Ej: Mi OneDrive, Work Drive...")
        if self.service.name:
            self.name_input.setText(self.service.name)
        self.content_layout.addWidget(self.name_input)

        self.content_layout.addSpacing(16)

        # Campo de carpeta local
        folder_label = QLabel("Carpeta de sincronización:")
        folder_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #424242;")
        self.content_layout.addWidget(folder_label)

        folder_row = QHBoxLayout()
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Seleccione una carpeta...")
        self.folder_input.setReadOnly(True)
        if self.service.local_path:
            self.folder_input.setText(self.service.local_path)
        folder_row.addWidget(self.folder_input)

        browse_btn = QPushButton("Examinar...")
        browse_btn.setObjectName("secondary")
        browse_btn.setFixedWidth(110)
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        self.content_layout.addLayout(folder_row)

        # Información adicional
        self.content_layout.addSpacing(12)
        info_label = QLabel(
            "ℹ️ Los archivos del servicio se descargarán en esta carpeta. "
            "Asegúrese de tener espacio suficiente en disco."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "background-color: #E3F2FD; padding: 12px; border-radius: 4px; "
            "color: #1565C0; font-size: 12px;"
        )
        self.content_layout.addWidget(info_label)

        self.content_layout.addStretch()

    def _build_step2(self):
        """
        Construye la interfaz del paso 2: selección de plataforma.
        Muestra una lista de plataformas soportadas para elegir.
        """
        # Título del paso
        title = QLabel("Paso 2: Seleccionar plataforma")
        title.setObjectName("title")
        self.content_layout.addWidget(title)

        subtitle = QLabel(
            "Seleccione el servicio en la nube con el que desea sincronizar."
        )
        subtitle.setObjectName("subtitle")
        self.content_layout.addWidget(subtitle)

        self.content_layout.addSpacing(12)

        # Lista de plataformas disponibles
        self.platform_list = QListWidget()
        for platform_key in SUPPORTED_PLATFORMS:
            display_name = PLATFORM_DISPLAY_NAMES.get(
                platform_key, platform_key.capitalize()
            )
            item = QListWidgetItem(f"  {display_name}")
            item.setData(Qt.UserRole, platform_key)
            self.platform_list.addItem(item)

        # Pre-seleccionar si ya hay una plataforma elegida
        if self.service.platform:
            for i in range(self.platform_list.count()):
                item = self.platform_list.item(i)
                if item.data(Qt.UserRole) == self.service.platform:
                    self.platform_list.setCurrentRow(i)
                    break

        self.platform_list.itemClicked.connect(self._on_platform_selected)
        self.content_layout.addWidget(self.platform_list, stretch=1)

    def _build_step3(self):
        """
        Construye la interfaz del paso 3: autenticación y confirmación.
        Muestra el resumen de configuración y botón para iniciar sesión.
        """
        # Título del paso
        title = QLabel("Paso 3: Autenticación")
        title.setObjectName("title")
        self.content_layout.addWidget(title)

        subtitle = QLabel(
            "Haga clic en 'Sincronizar sesión' para abrir el navegador e "
            "iniciar sesión en la plataforma seleccionada."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        self.content_layout.addWidget(subtitle)

        self.content_layout.addSpacing(16)

        # Resumen de configuración
        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            "background-color: white; border: 1px solid #E0E0E0; "
            "border-radius: 8px; padding: 16px;"
        )
        summary_layout = QVBoxLayout(summary_frame)

        summary_title = QLabel("Resumen de configuración:")
        summary_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #424242;")
        summary_layout.addWidget(summary_title)

        # Mostrar datos del servicio a crear
        platform_name = PLATFORM_DISPLAY_NAMES.get(
            self.service.platform, self.service.platform.capitalize()
        )
        fields = [
            ("Nombre:", self.service.name),
            ("Plataforma:", platform_name),
            ("Carpeta local:", self.service.local_path),
        ]
        for label, value in fields:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #757575; font-size: 12px; min-width: 120px;")
            val = QLabel(value)
            val.setStyleSheet("color: #212121; font-size: 12px;")
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, stretch=1)
            summary_layout.addLayout(row)

        self.content_layout.addWidget(summary_frame)
        self.content_layout.addSpacing(16)

        # Estado de autenticación
        self.auth_status_label = QLabel("")
        self.auth_status_label.setAlignment(Qt.AlignCenter)
        self.auth_status_label.setWordWrap(True)
        self.auth_status_label.setStyleSheet("font-size: 13px;")
        self.content_layout.addWidget(self.auth_status_label)

        # Botón para iniciar autenticación
        self.auth_btn = QPushButton("🔐 Sincronizar sesión")
        self.auth_btn.setObjectName("primary")
        self.auth_btn.setFixedHeight(44)
        self.auth_btn.clicked.connect(self._start_auth)
        self.content_layout.addWidget(self.auth_btn)

        # Mostrar advertencia si rclone no está instalado
        if not is_rclone_installed():
            warn_label = QLabel(
                "⚠️ rclone no está instalado. La autenticación real no "
                "funcionará, pero puede continuar para probar la interfaz."
            )
            warn_label.setWordWrap(True)
            warn_label.setStyleSheet(
                "background-color: #FFF3E0; padding: 10px; border-radius: 4px; "
                "color: #E65100; font-size: 12px;"
            )
            self.content_layout.addWidget(warn_label)

        # Indicador de progreso de autenticación
        self.auth_waiting_label = QLabel("")
        self.auth_waiting_label.setAlignment(Qt.AlignCenter)
        self.auth_waiting_label.setStyleSheet(
            "color: #2196F3; font-size: 12px; font-style: italic;"
        )
        self.content_layout.addWidget(self.auth_waiting_label)

        self.content_layout.addStretch()

        # Deshabilitar botón "Crear Servicio" hasta que haya autenticación
        self.btn_next.setEnabled(self.auth_token is not None)

    def _browse_folder(self):
        """Abre el diálogo de selección de carpeta para el paso 1."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta de sincronización",
            os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if folder:
            self.folder_input.setText(folder)

    def _on_platform_selected(self, item: QListWidgetItem):
        """Maneja la selección de una plataforma en la lista del paso 2."""
        platform_key = item.data(Qt.UserRole)
        self.service.platform = platform_key

    def _on_back(self):
        """Maneja el clic en el botón Atrás para retroceder un paso."""
        if self.current_step > 1:
            self._show_step(self.current_step - 1)

    def _on_next(self):
        """
        Maneja el clic en el botón Siguiente/Crear Servicio.
        Valida los datos del paso actual antes de avanzar.
        """
        if self.current_step == 1:
            # Validar paso 1: nombre y carpeta
            name = self.name_input.text().strip()
            folder = self.folder_input.text().strip()

            if not name:
                self._show_error("Por favor ingrese un nombre para el servicio.")
                return
            if not folder:
                self._show_error("Por favor seleccione una carpeta de sincronización.")
                return

            # Guardar datos en el objeto servicio
            self.service.name = name
            self.service.local_path = folder
            self._show_step(2)

        elif self.current_step == 2:
            # Validar paso 2: plataforma seleccionada
            if not self.platform_list.currentItem():
                self._show_error("Por favor seleccione una plataforma.")
                return

            # Guardar plataforma seleccionada
            self.service.platform = self.platform_list.currentItem().data(Qt.UserRole)
            # Generar nombre del remote de rclone
            self.service.rclone_remote = self.service.get_rclone_remote_name()
            self._show_step(3)

        elif self.current_step == 3:
            # Paso 3: confirmar y crear el servicio
            self._create_service()

    def _start_auth(self):
        """
        Inicia el proceso de autenticación OAuth con rclone.
        Abre el navegador para que el usuario inicie sesión.
        """
        self.auth_btn.setEnabled(False)
        self.btn_back.setEnabled(False)
        self.btn_next.setEnabled(False)

        # Mostrar mensaje de espera
        self.auth_status_label.setText(
            "⏳ Abriendo navegador para iniciar sesión...\n"
            "Por favor complete el proceso en su navegador."
        )
        self.auth_status_label.setStyleSheet(
            "color: #1565C0; font-size: 13px; background-color: #E3F2FD; "
            "padding: 10px; border-radius: 4px;"
        )

        # Animación de espera con puntos
        self._auth_dots = 0
        self._auth_timer = QTimer(self)
        self._auth_timer.timeout.connect(self._update_waiting_animation)
        self._auth_timer.start(500)

        # Iniciar autenticación en hilo separado
        authorize_service(self.service, self._on_auth_complete)

    def _update_waiting_animation(self):
        """Actualiza la animación de puntos durante la espera de autenticación."""
        self._auth_dots = (self._auth_dots + 1) % 4
        dots = "." * self._auth_dots
        self.auth_waiting_label.setText(f"Esperando respuesta del navegador{dots}")

    def _on_auth_complete(self, success: bool, token_or_error: str):
        """
        Callback llamado cuando termina el proceso de autenticación.
        Actualiza la UI desde el hilo principal usando QTimer.
        """
        # Usar timer para ejecutar en el hilo principal de Qt
        QTimer.singleShot(
            0,
            lambda: self._handle_auth_result(success, token_or_error),
        )

    def _handle_auth_result(self, success: bool, token_or_error: str):
        """
        Maneja el resultado de la autenticación en el hilo principal.
        Actualiza la UI según si fue exitosa o falló.
        """
        # Detener animación de espera
        if hasattr(self, "_auth_timer"):
            self._auth_timer.stop()
        self.auth_waiting_label.setText("")

        # Re-habilitar botón de volver
        self.btn_back.setEnabled(True)

        if success:
            # Autenticación exitosa
            self.auth_token = token_or_error
            self.auth_status_label.setText(
                "✅ ¡Autenticación exitosa! Token obtenido correctamente."
            )
            self.auth_status_label.setStyleSheet(
                "color: #2E7D32; font-size: 13px; background-color: #E8F5E9; "
                "padding: 10px; border-radius: 4px;"
            )
            # Habilitar botón de crear servicio
            self.btn_next.setEnabled(True)
            self.auth_btn.setText("✅ Sesión sincronizada")
            self.auth_btn.setEnabled(False)

            # Mostrar mensaje rápido de confirmación
            QTimer.singleShot(1500, self._show_token_success_message)
        else:
            # Error en autenticación
            self.auth_status_label.setText(
                f"❌ Error al autenticar: {token_or_error}\n"
                "Por favor intente de nuevo."
            )
            self.auth_status_label.setStyleSheet(
                "color: #C62828; font-size: 13px; background-color: #FFEBEE; "
                "padding: 10px; border-radius: 4px;"
            )
            self.auth_btn.setEnabled(True)
            self.auth_btn.setText("🔐 Reintentar autenticación")

    def _show_token_success_message(self):
        """Muestra un mensaje breve de confirmación de token obtenido."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Token Obtenido")
        msg.setText("✅ Token de autenticación obtenido correctamente.")
        msg.setInformativeText(
            "El servicio está listo para ser creado. "
            "Haga clic en 'Crear Servicio' para finalizar."
        )
        msg.setIcon(QMessageBox.Information)
        msg.setStandardButtons(QMessageBox.Ok)
        # Auto-cerrar después de 3 segundos
        QTimer.singleShot(3000, msg.accept)
        msg.exec_()

    def _create_service(self):
        """
        Crea el servicio con la configuración establecida.
        Emite la señal service_created y cierra el asistente.
        """
        # Configurar el remote de rclone en el servicio
        self.service.rclone_remote = self.service.get_rclone_remote_name()

        # Si no hay token real (para pruebas sin rclone instalado)
        if not self.auth_token:
            self.auth_token = "demo_token"

        # Emitir señal con el servicio creado
        self.service_created.emit(self.service)
        # Cerrar el asistente con éxito
        self.accept()

    def _show_error(self, message: str):
        """Muestra un mensaje de error al usuario."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Error de validación")
        msg.setText(message)
        msg.setIcon(QMessageBox.Warning)
        msg.exec_()
