"""
wizard.py — Asistente de configuración para agregar un nuevo servicio.
Consta de tres pasos:
  1. Elegir la carpeta local base para el servicio.
  2. Elegir la plataforma/servicio de nube.
  3. Autenticar con OAuth (abre el navegador) y confirmar.
El tamaño de cada ventana es del 70% de alto y 60% de ancho.
"""

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QComboBox,
    QMessageBox,
    QProgressBar,
    QFrame,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

import os
import uuid

from config import (
    add_service,
    default_service_config,
)
from rclone_manager import (
    PLATFORM_TYPES,
    authorize_service,
    configure_remote,
    is_rclone_installed,
    install_rclone,
    get_rclone_path,
)


# ──────────────────────────────────────────────────────────────────────────────
# Hilo para la autorización OAuth (no bloquea la UI)
# ──────────────────────────────────────────────────────────────────────────────

class AuthThread(QThread):
    """
    Hilo de Qt que ejecuta la autorización OAuth en segundo plano.
    Emite señales cuando el proceso termina o falla.
    """
    # Señal emitida con (éxito: bool, datos: str)
    finished = pyqtSignal(bool, str)

    def __init__(self, platform_type: str, remote_name: str, parent=None):
        """Inicializa el hilo con la plataforma y nombre del remote."""
        super().__init__(parent)
        self.platform_type = platform_type
        self.remote_name = remote_name

    def run(self):
        """Ejecuta la autorización en el hilo."""
        # Llama a la función de autorización que abre el navegador
        authorize_service(
            self.platform_type,
            self.remote_name,
            # El callback emite la señal de Qt
            lambda success, msg: self.finished.emit(success, msg),
        )


# Prefijo y longitud del identificador único para los remotes de rclone
REMOTE_NAME_PREFIX = "rpia_"
REMOTE_UUID_LENGTH = 6

# ──────────────────────────────────────────────────────────────────────────────
# Utilidades compartidas para todas las ventanas del wizard
# ──────────────────────────────────────────────────────────────────────────────

def _apply_window_size(window: QDialog) -> None:
    """
    Ajusta el tamaño de la ventana al 70% de alto y 60% de ancho
    de la pantalla disponible.
    """
    from PyQt5.QtWidgets import QApplication
    screen = QApplication.primaryScreen().availableGeometry()
    w = int(screen.width() * 0.60)
    h = int(screen.height() * 0.70)
    window.resize(w, h)
    # Centrar la ventana en la pantalla
    x = screen.x() + (screen.width() - w) // 2
    y = screen.y() + (screen.height() - h) // 2
    window.move(x, y)


def _title_label(text: str) -> QLabel:
    """Crea un QLabel con estilo de título principal."""
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(14)
    font.setBold(True)
    lbl.setFont(font)
    lbl.setAlignment(Qt.AlignCenter)
    return lbl


def _subtitle_label(text: str) -> QLabel:
    """Crea un QLabel con estilo de subtítulo."""
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(10)
    lbl.setFont(font)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setWordWrap(True)
    return lbl


def _separator() -> QFrame:
    """Crea una línea separadora horizontal."""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


# ──────────────────────────────────────────────────────────────────────────────
# Paso 1: Elegir carpeta local
# ──────────────────────────────────────────────────────────────────────────────

class WizardStep1(QDialog):
    """
    Primera ventana del asistente.
    Permite al usuario elegir el nombre del servicio y
    la carpeta local donde se guardarán los archivos sincronizados.
    """

    def __init__(self, parent=None):
        """Inicializa la ventana con los campos necesarios."""
        super().__init__(parent)
        self.setWindowTitle("Nuevo Servicio — Paso 1 de 3: Carpeta local")
        # Evitar que se maximice
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowMaximizeButtonHint
        )
        # Aplicar tamaño del 70% alto × 60% ancho
        _apply_window_size(self)
        self._build_ui()

    def _build_ui(self):
        """Construye los componentes visuales de la ventana."""
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)

        # Indicador de paso
        layout.addWidget(_title_label("Agregar nuevo servicio"))
        layout.addWidget(_subtitle_label("Paso 1 de 3 — Configuración de carpeta"))
        layout.addWidget(_separator())

        # ── Nombre del servicio ─────────────────────────────────────────────
        lbl_name = QLabel("Nombre del servicio:")
        lbl_name.setFont(QFont("", 11))
        layout.addWidget(lbl_name)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Ej: Mi OneDrive")
        self.name_input.setMinimumHeight(36)
        layout.addWidget(self.name_input)

        # ── Selector de carpeta ─────────────────────────────────────────────
        lbl_folder = QLabel("Carpeta local de sincronización:")
        lbl_folder.setFont(QFont("", 11))
        layout.addWidget(lbl_folder)

        folder_row = QHBoxLayout()
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Selecciona una carpeta...")
        self.folder_input.setMinimumHeight(36)
        folder_row.addWidget(self.folder_input)

        btn_browse = QPushButton("Examinar…")
        btn_browse.setMinimumHeight(36)
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(btn_browse)
        layout.addLayout(folder_row)

        # ── Espaciador ─────────────────────────────────────────────────────
        layout.addStretch()

        # ── Botones de navegación ───────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setMinimumSize(110, 38)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self.btn_next = QPushButton("Siguiente →")
        self.btn_next.setMinimumSize(110, 38)
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next)
        btn_row.addWidget(self.btn_next)
        layout.addLayout(btn_row)

    def _browse_folder(self):
        """Abre el diálogo de selección de carpeta."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta de sincronización",
            os.path.expanduser("~"),
        )
        if folder:
            self.folder_input.setText(folder)

    def _on_next(self):
        """Valida los campos y avanza al paso 2 si son correctos."""
        name = self.name_input.text().strip()
        folder = self.folder_input.text().strip()

        # Validar que el nombre no esté vacío
        if not name:
            QMessageBox.warning(self, "Campo requerido", "Por favor ingresa un nombre para el servicio.")
            return

        # Validar que se haya seleccionado una carpeta
        if not folder:
            QMessageBox.warning(self, "Campo requerido", "Por favor selecciona una carpeta de sincronización.")
            return

        # Crear la carpeta si no existe
        if not os.path.exists(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                QMessageBox.critical(self, "Error", f"No se pudo crear la carpeta:\n{e}")
                return

        # Guardar los datos y aceptar el diálogo
        self.service_name = name
        self.local_folder = folder
        self.accept()

    def get_data(self) -> tuple:
        """Devuelve el nombre del servicio y la carpeta elegidos."""
        return getattr(self, "service_name", ""), getattr(self, "local_folder", "")


# ──────────────────────────────────────────────────────────────────────────────
# Paso 2: Elegir la plataforma de nube
# ──────────────────────────────────────────────────────────────────────────────

class WizardStep2(QDialog):
    """
    Segunda ventana del asistente.
    Permite al usuario elegir la plataforma de nube (OneDrive, Google Drive, etc.).
    """

    def __init__(self, service_name: str, parent=None):
        """
        Inicializa la ventana.

        :param service_name: Nombre del servicio elegido en el paso 1.
        """
        super().__init__(parent)
        self.setWindowTitle("Nuevo Servicio — Paso 2 de 3: Plataforma")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowMaximizeButtonHint
        )
        _apply_window_size(self)
        self.service_name = service_name
        self._build_ui()

    def _build_ui(self):
        """Construye los componentes visuales de la ventana."""
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)

        # Encabezado
        layout.addWidget(_title_label("Agregar nuevo servicio"))
        layout.addWidget(_subtitle_label("Paso 2 de 3 — Seleccionar plataforma"))
        layout.addWidget(_separator())

        # Mostrar el nombre del servicio elegido
        lbl_info = QLabel(f"Servicio: <b>{self.service_name}</b>")
        lbl_info.setFont(QFont("", 11))
        layout.addWidget(lbl_info)

        # ── Selector de plataforma ──────────────────────────────────────────
        lbl_platform = QLabel("Plataforma de nube:")
        lbl_platform.setFont(QFont("", 11))
        layout.addWidget(lbl_platform)

        self.platform_combo = QComboBox()
        self.platform_combo.setMinimumHeight(36)
        # Agregar todas las plataformas disponibles
        for name in PLATFORM_TYPES.keys():
            self.platform_combo.addItem(name)
        layout.addWidget(self.platform_combo)

        # Descripción de la plataforma seleccionada
        self.desc_label = _subtitle_label(
            "Selecciona la plataforma de nube donde están almacenados tus archivos."
        )
        layout.addWidget(self.desc_label)

        layout.addStretch()

        # ── Botones de navegación ───────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_back = QPushButton("← Atrás")
        btn_back.setMinimumSize(110, 38)
        btn_back.clicked.connect(self.reject)
        btn_row.addWidget(btn_back)

        self.btn_next = QPushButton("Siguiente →")
        self.btn_next.setMinimumSize(110, 38)
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next)
        btn_row.addWidget(self.btn_next)
        layout.addLayout(btn_row)

    def _on_next(self):
        """Guarda la plataforma seleccionada y avanza al paso 3."""
        self.selected_platform = self.platform_combo.currentText()
        self.accept()

    def get_platform(self) -> str:
        """Devuelve la plataforma elegida."""
        return getattr(self, "selected_platform", "")


# ──────────────────────────────────────────────────────────────────────────────
# Paso 3: Autenticación OAuth y confirmación
# ──────────────────────────────────────────────────────────────────────────────

class WizardStep3(QDialog):
    """
    Tercera ventana del asistente.
    Inicia la sesión OAuth en el navegador y espera el token.
    Tras confirmación, guarda el servicio y avanza a la ventana principal.
    """

    def __init__(self, service_name: str, local_folder: str, platform: str, parent=None):
        """
        Inicializa la ventana.

        :param service_name: Nombre del servicio.
        :param local_folder: Carpeta local de sincronización.
        :param platform: Plataforma de nube elegida.
        """
        super().__init__(parent)
        self.setWindowTitle("Nuevo Servicio — Paso 3 de 3: Autenticación")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowMaximizeButtonHint
        )
        _apply_window_size(self)

        self.service_name = service_name
        self.local_folder = local_folder
        self.platform = platform
        # Nombre interno único para el remote en rclone (usando constantes del módulo)
        safe_name = service_name.lower().replace(" ", "_")
        uid = uuid.uuid4().hex[:REMOTE_UUID_LENGTH]
        self.remote_name = f"{REMOTE_NAME_PREFIX}{safe_name}_{uid}"
        self._token = ""
        self._auth_thread = None

        self._build_ui()

    def _build_ui(self):
        """Construye los componentes visuales de la ventana."""
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)

        # Encabezado
        layout.addWidget(_title_label("Agregar nuevo servicio"))
        layout.addWidget(_subtitle_label("Paso 3 de 3 — Autenticación con la plataforma"))
        layout.addWidget(_separator())

        # Resumen de la configuración
        summary = QLabel(
            f"<b>Servicio:</b> {self.service_name}<br>"
            f"<b>Plataforma:</b> {self.platform}<br>"
            f"<b>Carpeta local:</b> {self.local_folder}"
        )
        summary.setFont(QFont("", 11))
        summary.setWordWrap(True)
        layout.addWidget(summary)

        layout.addWidget(_separator())

        # Instrucciones de autenticación
        instructions = _subtitle_label(
            "Haz clic en 'Sincronizar sesión' para abrir el navegador e iniciar sesión "
            "en la plataforma seleccionada. La aplicación esperará a que completes "
            "la autorización."
        )
        layout.addWidget(instructions)

        # Barra de progreso (oculta hasta que inicie la auth)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Modo indeterminado
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(20)
        layout.addWidget(self.progress_bar)

        # Etiqueta de estado
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch()

        # ── Botones ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_back = QPushButton("← Atrás")
        self.btn_back.setMinimumSize(110, 38)
        self.btn_back.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_back)

        self.btn_auth = QPushButton("🔗 Sincronizar sesión")
        self.btn_auth.setMinimumSize(160, 38)
        self.btn_auth.setDefault(True)
        self.btn_auth.clicked.connect(self._start_auth)
        btn_row.addWidget(self.btn_auth)

        layout.addLayout(btn_row)

    def _start_auth(self):
        """
        Inicia el proceso de autorización OAuth.
        Verifica que rclone esté instalado, luego lanza el hilo de autenticación.
        """
        # Deshabilitar el botón para evitar doble clic
        self.btn_auth.setEnabled(False)
        self.btn_back.setEnabled(False)

        # Verificar si rclone está disponible
        if not is_rclone_installed():
            self.status_label.setText("rclone no está instalado. Instalando...")
            self.progress_bar.setVisible(True)
            # Instalar rclone antes de continuar
            install_rclone(progress_callback=self._on_install_progress)
            return

        self._launch_auth_thread()

    def _on_install_progress(self, message: str):
        """Actualiza el estado durante la instalación de rclone."""
        self.status_label.setText(message)
        # Si la instalación terminó, lanzar la autenticación
        if "correctamente" in message.lower():
            self._launch_auth_thread()
        elif "error" in message.lower():
            # Reactivar botones si hubo error
            self.progress_bar.setVisible(False)
            self.btn_auth.setEnabled(True)
            self.btn_back.setEnabled(True)

    def _launch_auth_thread(self):
        """Lanza el hilo de autorización OAuth."""
        self.status_label.setText(
            "Abriendo el navegador para autenticación... Por favor, inicia sesión."
        )
        self.progress_bar.setVisible(True)

        # Crear y conectar el hilo de autenticación
        self._auth_thread = AuthThread(self.platform, self.remote_name, parent=self)
        self._auth_thread.finished.connect(self._on_auth_finished)
        self._auth_thread.start()

    def _on_auth_finished(self, success: bool, data: str):
        """
        Se llama cuando la autenticación termina.
        Si fue exitosa, guarda el token y configura el remote.
        """
        # Ocultar barra de progreso
        self.progress_bar.setVisible(False)

        if success:
            self._token = data
            # Configurar el remote en rclone con el token obtenido
            configured = configure_remote(self.remote_name, self.platform, self._token)
            if configured:
                # Construir la configuración del servicio
                service = default_service_config()
                service["name"] = self.service_name
                service["platform"] = self.platform
                service["local_folder"] = self.local_folder
                service["rclone_remote"] = self.remote_name
                service["token"] = self._token
                # Excluir archivos personales de OneDrive por defecto
                if self.platform == "OneDrive":
                    service["excluded_folders"] = ["Personal Vault"]

                # Guardar el servicio en la configuración
                add_service(service)

                # Mostrar mensaje de confirmación brevemente
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self.status_label.setText("✔ ¡Autenticación exitosa! Servicio configurado.")

                # Avanzar a la ventana principal después de 1.5 segundos
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(1500, self.accept)
            else:
                # Error al configurar el remote
                self.status_label.setStyleSheet("color: red;")
                self.status_label.setText("Error al configurar el servicio en rclone.")
                self.btn_auth.setEnabled(True)
                self.btn_back.setEnabled(True)
        else:
            # Error en la autenticación
            self.status_label.setStyleSheet("color: red;")
            self.status_label.setText(f"Error de autenticación: {data}")
            self.btn_auth.setEnabled(True)
            self.btn_back.setEnabled(True)

    def get_service_data(self) -> dict:
        """Devuelve el nombre del remote configurado en rclone."""
        return {"remote_name": self.remote_name, "token": self._token}


# ──────────────────────────────────────────────────────────────────────────────
# Función pública: ejecutar el wizard completo
# ──────────────────────────────────────────────────────────────────────────────

def run_wizard(parent=None) -> bool:
    """
    Ejecuta el asistente completo de configuración de nuevo servicio.
    Devuelve True si el servicio fue configurado correctamente, False si el
    usuario canceló o hubo un error.
    """
    # ── Paso 1: Carpeta ─────────────────────────────────────────────────────
    step1 = WizardStep1(parent)
    if step1.exec_() != QDialog.Accepted:
        return False
    service_name, local_folder = step1.get_data()

    # ── Paso 2: Plataforma ──────────────────────────────────────────────────
    step2 = WizardStep2(service_name, parent)
    if step2.exec_() != QDialog.Accepted:
        return False
    platform = step2.get_platform()

    # ── Paso 3: Autenticación ───────────────────────────────────────────────
    step3 = WizardStep3(service_name, local_folder, platform, parent)
    if step3.exec_() != QDialog.Accepted:
        return False

    return True
