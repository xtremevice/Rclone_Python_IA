"""
src/windows/setup_wizard.py

Three-step wizard for adding a new rclone service:

  Step 1 – Choose the local directory where the service will be stored.
  Step 2 – Select the cloud-storage platform.
  Step 3 – Authorise via browser (OAuth) and confirm the new service.

Window size: 70 % of screen height × 60 % of screen width.
"""
import re
import uuid
from typing import Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QApplication,
    QFrame,
)

from src.core.config import AppConfig, ServiceConfig, SUPPORTED_PLATFORMS
from src.core.rclone import AuthWorker, rclone_available


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_remote_name(service_name: str) -> str:
    """Convert a human-readable service name to a safe rclone remote identifier.

    Only alphanumeric characters and underscores are kept.
    """
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", service_name)
    return safe or f"remote_{uuid.uuid4().hex[:6]}"


def _create_step_label(number: int, title: str) -> QLabel:
    """Return a styled QLabel for a wizard-step heading."""
    label = QLabel(f"Paso {number}: {title}")
    font = QFont()
    font.setPointSize(14)
    font.setBold(True)
    label.setFont(font)
    label.setAlignment(Qt.AlignCenter)
    return label


# ---------------------------------------------------------------------------
# Step 1 – directory selection
# ---------------------------------------------------------------------------

class _Step1Widget(QWidget):
    """Let the user enter / browse for the local sync directory."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialise the step-1 widget."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        layout.addWidget(_create_step_label(1, "Selecciona la carpeta de sincronización"))

        layout.addWidget(QLabel(
            "Elige la carpeta local donde se almacenarán los archivos del servicio."
        ))

        # Service name row
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nombre del servicio:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ej. Mi OneDrive Personal")
        name_row.addWidget(self.name_edit)
        layout.addLayout(name_row)

        # Directory row
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Carpeta local:"))
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("Ruta de la carpeta …")
        dir_row.addWidget(self.dir_edit)
        browse_btn = QPushButton("Examinar …")
        browse_btn.setFixedWidth(110)
        browse_btn.clicked.connect(self._browse)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        layout.addStretch()

    def _browse(self) -> None:
        """Open a native folder-picker dialog and fill the path edit."""
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de sincronización"
        )
        if folder:
            self.dir_edit.setText(folder)

    def validate(self) -> bool:
        """Return True when both name and directory are filled in."""
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Advertencia", "Por favor ingresa un nombre de servicio.")
            return False
        if not self.dir_edit.text().strip():
            QMessageBox.warning(self, "Advertencia", "Por favor selecciona una carpeta.")
            return False
        return True

    @property
    def service_name(self) -> str:
        """Return the entered service name."""
        return self.name_edit.text().strip()

    @property
    def local_path(self) -> str:
        """Return the selected local directory path."""
        return self.dir_edit.text().strip()


# ---------------------------------------------------------------------------
# Step 2 – platform selection
# ---------------------------------------------------------------------------

class _Step2Widget(QWidget):
    """Let the user choose the cloud-storage platform for the new service."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialise the step-2 widget."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        layout.addWidget(_create_step_label(2, "Selecciona la plataforma"))

        layout.addWidget(QLabel(
            "Elige el servicio en la nube que deseas sincronizar:"
        ))

        self.list_widget = QListWidget()
        self.list_widget.setSpacing(4)
        for platform_name in SUPPORTED_PLATFORMS:
            self.list_widget.addItem(platform_name)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget)

        layout.addStretch()

    def validate(self) -> bool:
        """Return True when a platform is selected."""
        if self.list_widget.currentItem() is None:
            QMessageBox.warning(self, "Advertencia", "Por favor selecciona una plataforma.")
            return False
        return True

    @property
    def selected_platform(self) -> str:
        """Return the selected platform display name."""
        item = self.list_widget.currentItem()
        return item.text() if item else ""


# ---------------------------------------------------------------------------
# Step 3 – OAuth authorisation + confirmation
# ---------------------------------------------------------------------------

class _Step3Widget(QWidget):
    """Guide the user through the OAuth browser-flow and show status."""

    # Emitted when authorisation has completed (success, message)
    auth_complete = pyqtSignal(bool, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialise the step-3 widget."""
        super().__init__(parent)
        self._thread: Optional[QThread] = None
        self._worker: Optional[AuthWorker] = None
        self._authorised = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        layout.addWidget(_create_step_label(3, "Autorizar sesión"))

        self.info_label = QLabel(
            "Haz clic en <b>Sincronizar Sesión</b> para abrir el navegador y "
            "autorizar el acceso a tu cuenta en la nube.\n\n"
            "Una vez que completes el inicio de sesión en el navegador, regresa "
            "aquí automáticamente."
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # Status area
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        # Progress bar (hidden until auth starts)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)   # indeterminate
        self.progress.hide()
        layout.addWidget(self.progress)

        # Sync button
        self.sync_btn = QPushButton("🔗 Sincronizar Sesión")
        self.sync_btn.setMinimumHeight(40)
        self.sync_btn.clicked.connect(self._start_auth)
        layout.addWidget(self.sync_btn)

        layout.addStretch()

    # ------------------------------------------------------------------

    def prepare(self, remote_name: str, platform_key: str) -> None:
        """Set the remote_name and platform_key before the step is shown."""
        self._remote_name = remote_name
        self._platform_key = platform_key
        self._authorised = False
        self.status_label.setText(
            f"Servicio: <b>{remote_name}</b>  |  Plataforma: <b>{platform_key}</b>"
        )
        self.sync_btn.setEnabled(True)
        self.progress.hide()

    def _start_auth(self) -> None:
        """Start the rclone OAuth flow in a background thread."""
        if not rclone_available():
            QMessageBox.critical(
                self,
                "rclone no encontrado",
                "rclone no está instalado.  Por favor instálalo e inténtalo de nuevo.",
            )
            return

        self.sync_btn.setEnabled(False)
        self.progress.show()
        self.status_label.setText(
            "Abriendo navegador … Por favor inicia sesión y luego regresa aquí."
        )

        # Spin up the auth worker
        self._thread = QThread(self)
        self._worker = AuthWorker(
            remote_name=self._remote_name,
            platform_key=self._platform_key,
        )
        self._worker.moveToThread(self._thread)
        self._worker.finished.connect(self._on_auth_finished)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def _on_auth_finished(self, success: bool, message: str) -> None:
        """Handle the end of the OAuth flow and update the UI."""
        self.progress.hide()
        self.sync_btn.setEnabled(True)

        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread.deleteLater()
            self._thread = None
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

        if success:
            self._authorised = True
            self.status_label.setText(
                "✅ ¡Autorización exitosa! Puedes continuar."
            )
        else:
            self.status_label.setText(f"❌ Error: {message}")

        self.auth_complete.emit(success, message)

    def is_authorised(self) -> bool:
        """Return True when the OAuth flow completed successfully."""
        return self._authorised


# ---------------------------------------------------------------------------
# Main wizard dialog
# ---------------------------------------------------------------------------

class SetupWizard(QDialog):
    """Three-step dialog for adding a new rclone service.

    Emits ``service_created(ServiceConfig)`` when the wizard completes.
    """

    service_created = pyqtSignal(object)   # ServiceConfig instance

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None) -> None:
        """Initialise the wizard with a reference to the app configuration."""
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Agregar Nuevo Servicio")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint
        )
        self._resize_to_screen()
        self._build_ui()

    # ------------------------------------------------------------------
    def _resize_to_screen(self) -> None:
        """Size the dialog to 70 % height × 60 % width of the primary screen."""
        screen = QApplication.primaryScreen().availableGeometry()
        w = int(screen.width() * 0.60)
        h = int(screen.height() * 0.70)
        self.resize(w, h)
        # Center on screen
        self.move(
            screen.x() + (screen.width() - w) // 2,
            screen.y() + (screen.height() - h) // 2,
        )

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Construct all child widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(10)

        # Step indicator
        self.step_indicator = QLabel("Paso 1 de 3")
        self.step_indicator.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(10)
        self.step_indicator.setFont(font)
        main_layout.addWidget(self.step_indicator)

        # Horizontal divider
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(line)

        # Stacked pages
        self.stack = QStackedWidget()
        self.step1 = _Step1Widget()
        self.step2 = _Step2Widget()
        self.step3 = _Step3Widget()
        self.step3.auth_complete.connect(self._on_auth_complete)
        self.stack.addWidget(self.step1)
        self.stack.addWidget(self.step2)
        self.stack.addWidget(self.step3)
        main_layout.addWidget(self.stack, stretch=1)

        # Navigation buttons
        btn_row = QHBoxLayout()
        self.back_btn = QPushButton("← Anterior")
        self.back_btn.setMinimumHeight(36)
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setEnabled(False)
        btn_row.addWidget(self.back_btn)

        btn_row.addStretch()

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.next_btn = QPushButton("Siguiente →")
        self.next_btn.setMinimumHeight(36)
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._go_next)
        btn_row.addWidget(self.next_btn)

        main_layout.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _update_nav(self, index: int) -> None:
        """Refresh navigation-button state for the current step index."""
        self.back_btn.setEnabled(index > 0)
        total = self.stack.count()
        self.step_indicator.setText(f"Paso {index + 1} de {total}")
        if index == total - 1:
            # Last step – show "Finalizar" only after auth succeeds
            self.next_btn.setText("Finalizar")
        else:
            self.next_btn.setText("Siguiente →")

    def _go_next(self) -> None:
        """Validate the current step and advance to the next one."""
        idx = self.stack.currentIndex()

        if idx == 0:
            # Validate step 1
            if not self.step1.validate():
                return
            self.stack.setCurrentIndex(1)

        elif idx == 1:
            # Validate step 2 and prepare step 3
            if not self.step2.validate():
                return
            # Build a safe remote name from the service name entered in step 1
            remote_name = _make_remote_name(self.step1.service_name)
            self.step3.prepare(remote_name, self.step2.selected_platform)
            self.stack.setCurrentIndex(2)

        elif idx == 2:
            # Step 3: the user must have completed auth before finishing
            if not self.step3.is_authorised():
                QMessageBox.information(
                    self,
                    "Autorización pendiente",
                    "Por favor completa la autorización primero.",
                )
                return
            self._finish()

        self._update_nav(self.stack.currentIndex())

    def _go_back(self) -> None:
        """Go to the previous wizard step."""
        idx = self.stack.currentIndex()
        if idx > 0:
            self.stack.setCurrentIndex(idx - 1)
            self._update_nav(idx - 1)

    def _on_auth_complete(self, success: bool, message: str) -> None:
        """React to the auth worker finishing."""
        if success:
            QMessageBox.information(
                self,
                "¡Autorización exitosa!",
                "✅ Token obtenido correctamente.\n\nHaz clic en 'Finalizar' para continuar.",
            )
        else:
            QMessageBox.warning(
                self,
                "Error de autorización",
                f"No se pudo completar la autorización:\n{message}",
            )

    def _finish(self) -> None:
        """Create the ServiceConfig, persist it, and close the wizard."""
        service = ServiceConfig()
        service.name = self.step1.service_name
        service.remote_name = _make_remote_name(self.step1.service_name)
        service.platform = self.step2.selected_platform
        service.local_path = self.step1.local_path
        service.remote_path = "/"
        # Default: exclude OneDrive's personal vault (avoids sync errors)
        if service.platform == "OneDrive":
            service.exclude_rules = ["/Almacén personal/**"]
        else:
            service.exclude_rules = []

        self.config.add_service(service)
        self.service_created.emit(service)
        self.accept()
